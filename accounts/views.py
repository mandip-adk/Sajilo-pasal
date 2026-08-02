from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_POST
from django.db import transaction
from django.utils import timezone

from .forms import RegistrationForm, LoginForm, OTPVerificationForm, ProfileUpdateForm
from .models import EmailVerificationOTP, DailyOTPAttemptLimit
from .utils import send_otp_email

User = get_user_model()

RESEND_COOLDOWN_SECONDS = 60


# ─────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def register_view(request):
    """
    Step 1 of 2 in onboarding:
    Creates an inactive User, sends an OTP, and redirects to verification.
    """
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    form = RegistrationForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            user = form.save()

            otp, raw_code = EmailVerificationOTP.create_for_user(user)
            send_otp_email(user, raw_code)

            request.session["pending_user_id"] = user.pk
            messages.info(
                request,
                "Account created! Please check your email for the OTP verification code."
            )
            return redirect("accounts:verify_otp")
        else:
            messages.error(request, "Please correct the errors below.")

    return render(request, "accounts/register.html", {"form": form})


# ─────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def login_view(request):
    """
    # Login with email + password:
    - By default, Django blocks accounts that are not yet verified
    -   (they are marked inactive). When this happens, Django just says
    -   "login failed" without telling us why.
    - That means an unverified user who types the correct password
    -   looks the same as someone typing the wrong password.
    - To avoid confusing people, we add an extra check:
    -   if login fails, we look up the account directly and test the password.
    -   If it matches, we can show the right message:
    -   "Please verify your OTP" instead of "Invalid email or password."
    """
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    form = LoginForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            email    = form.cleaned_data["email"]
            password = form.cleaned_data["password"]
            user     = authenticate(request, username=email, password=password)

            if user is None:
                # authenticate() failed — could be a genuinely wrong
                # password/email, OR a correct password on an inactive
                # (unverified) account, which ModelBackend always
                # rejects regardless of password correctness.
                unverified_user = _check_unverified_credentials(email, password)

                if unverified_user is not None:
                    request.session["pending_user_id"] = unverified_user.pk

                    otp, raw_code = EmailVerificationOTP.create_for_user(unverified_user)
                    send_otp_email(unverified_user, raw_code)

                    messages.warning(
                        request,
                        "Your account is not verified yet. "
                        "Please enter the OTP sent to your email."
                    )
                    return redirect("accounts:verify_otp")

                messages.error(request, "Invalid email or password.")
            else:
                login(request, user)
                request.session.cycle_key()

                next_url = request.GET.get("next") or "dashboard:home"
                return redirect(next_url)
        else:
            messages.error(request, "Please correct the errors below.")

    return render(request, "accounts/login.html", {"form": form})


def _check_unverified_credentials(email, password):
    """
    # Manual password check for unverified accounts:
    - Normally, Django refuses to check passwords for accounts
    -   that are marked inactive (unverified). It just says "login failed."
    - To avoid confusion, we do the password check ourselves:
    -   if the password is correct, we know the account is fine but
    -   still needs verification.
    - This does NOT log the user in. It only helps us show the right
    -   message ("Please verify your OTP") instead of the wrong one
    -   ("Invalid email or password").
    """
    try:
        candidate = User.objects.get(email=email)
    except User.DoesNotExist:
        return None

    if candidate.is_verified:
        # They ARE verified but authenticate() still failed -> genuinely
        # wrong password. Not our case to handle here.
        return None

    if candidate.check_password(password):
        return candidate
    return None


# ─────────────────────────────────────────────
# Logout
# ─────────────────────────────────────────────

@login_required
@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect("accounts:login")


# ─────────────────────────────────────────────
# OTP Verification
# ─────────────────────────────────────────────

def _get_active_otp(user):

    return EmailVerificationOTP.objects.filter(user=user, is_used=False).first()


def _get_pending_user(request):
    """
    Resolves the user pending verification from the session-cached PK.
    """
    user_id = request.session.get("pending_user_id")
    if not user_id:
        return None
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        del request.session["pending_user_id"]
        return None


@require_http_methods(["GET", "POST"])
def verify_otp_view(request):
    """ OTP verification notes:
    - otp_code_hash is compared via otp.check_code(submitted_code), which
      hashes the submission and compares it to the stored hash using a
      constant-time comparison.
    - Lockout cooldown is tracked in the database, not the session.
    - After MAX_ATTEMPTS_BEFORE_RETIRE wrong guesses, the OTP retires
      itself (is_used=True).
    - A daily per-user attempt ceiling caps total guesses across resends.
    - Verification runs inside transaction.atomic() with select_for_update()
      on the OTP row to prevent a race between near-simultaneous submissions.
    """
    user = _get_pending_user(request)

    if user is None:
        messages.error(request, "No pending verification found. Please register or log in again.")
        return redirect("accounts:register")

    if user.is_verified:
        del request.session["pending_user_id"]
        login(request, user)
        request.session.cycle_key()
        return redirect("dashboard:home")

    daily_limit = DailyOTPAttemptLimit.get_or_reset_for_user(user)
    otp = _get_active_otp(user)
    form = OTPVerificationForm(request.POST or None)

    locked = otp.is_currently_locked() if otp else False
    wait_seconds = otp.seconds_until_unlock() if otp else 0
    daily_limit_exceeded = daily_limit.has_exceeded_limit()

    if request.method == "POST":
        if daily_limit_exceeded:
            messages.error(
                request,
                "You've reached today's verification attempt limit. Please try again tomorrow."
            )
        elif otp is None:
            messages.error(request, "No active OTP found. Please request a new one.")
        elif locked:
            messages.warning(request, f"Please wait {wait_seconds}s before trying again.")
        elif form.is_valid():
            submitted_code = form.cleaned_data["otp_code"]

            with transaction.atomic():
                locked_otp = (
                    EmailVerificationOTP.objects
                    .select_for_update()
                    .get(pk=otp.pk)
                )

                daily_limit.increment()

                if not locked_otp.is_valid():
                    messages.error(request, "This OTP is no longer valid. Please request a new one.")
                elif not locked_otp.check_code(submitted_code):
                    retired = locked_otp.record_failed_attempt()
                    if retired:
                        messages.error(
                            request,
                            "Too many failed attempts. This code is now retired — please request a new one."
                        )
                    else:
                        remaining = locked_otp.MAX_ATTEMPTS_BEFORE_RETIRE - locked_otp.failed_attempts
                        wait = EmailVerificationOTP.LOCKOUT_SCHEDULE.get(locked_otp.failed_attempts, 0)
                        messages.error(
                            request,
                            f"Incorrect OTP. {remaining} attempt(s) left before this code is retired. "
                            f"Please wait {wait}s before trying again."
                        )
                else:
                    locked_otp.is_used = True
                    locked_otp.save(update_fields=["is_used"])

                    user.is_active   = True
                    user.is_verified = True
                    user.save(update_fields=["is_active", "is_verified"])

                    del request.session["pending_user_id"]

                    login(request, user)
                    request.session.cycle_key()

                    messages.success(request, "Email verified! Welcome to Sajilo Pasal.")
                    return redirect("dashboard:home")
        else:
            messages.error(request, "Please enter a valid 6-digit code.")

    return render(request, "accounts/verify_otp.html", {
        "form": form,
        "email": user.email,
        "locked": locked,
        "wait_seconds": wait_seconds,
        "daily_limit_exceeded": daily_limit_exceeded,
    })


@require_http_methods(["POST"])
def resend_otp_view(request):
    """
    Issues a fresh OTP for the pending user. Rate limiting is enforced
    via the database, tied to the user account — not the session.
    """
    user = _get_pending_user(request)

    if user is None:
        messages.error(request, "No pending verification found.")
        return redirect("accounts:register")

    daily_limit = DailyOTPAttemptLimit.get_or_reset_for_user(user)
    if daily_limit.has_exceeded_limit():
        messages.error(
            request,
            "You've reached today's verification attempt limit. Please try again tomorrow."
        )
        return redirect("accounts:verify_otp")

    most_recent = EmailVerificationOTP.objects.filter(user=user).order_by("-created_at").first()

    if most_recent:
        elapsed = (timezone.now() - most_recent.created_at).total_seconds()
        if elapsed < RESEND_COOLDOWN_SECONDS:
            wait = int(RESEND_COOLDOWN_SECONDS - elapsed)
            messages.warning(request, f"Please wait {wait}s before requesting another code.")
            return redirect("accounts:verify_otp")

    otp, raw_code = EmailVerificationOTP.create_for_user(user)
    send_otp_email(user, raw_code)

    messages.success(request, "A new OTP has been sent to your email.")
    return redirect("accounts:verify_otp")


# ─────────────────────────────────────────────
# Profile
# ─────────────────────────────────────────────

@login_required
@require_http_methods(["GET", "POST"])
def profile_view(request):
    """
    View and edit the logged-in owner's display name.
    Email is read-only here (see ProfileUpdateForm docstring for why).
    """
    form = ProfileUpdateForm(request.POST or None, instance=request.user)

    if request.method == "POST":
        if form.is_valid():
            user = form.save(commit=False)
            user.full_clean(exclude=["password"])
            user.save()
            messages.success(request, "Profile updated successfully.")
            return redirect("accounts:profile")
        else:
            messages.error(request, "Please correct the errors below.")

    return render(request, "accounts/profile.html", {"form": form})
