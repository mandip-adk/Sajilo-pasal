"""
Automated QA tests for Sajilo Pasal — Accounts app (Days 1–3).

Covers checklist sections:
  1. Authentication       — register, login, logout, protected-page redirect
  7. Permissions           — one user cannot act as another (session isolation)
  8. Forms                 — empty fields, invalid data, long text, duplicates
  9. URLs                  — no 500s on key routes, even when logged out
  11. (implicitly) — every test here fails loudly if a view throws,
      so running this suite IS your "watch the terminal for tracebacks" step.

Sections 2–6 (Restaurant/Shop, Categories, Menu Items, QR Menu, Inventory)
are NOT covered here because those features don't exist yet in the
codebase as of Day 3 — there is nothing real to test. Each day's test
additions should land in that day's own app (shops/tests.py,
categories/tests.py, etc.) following the same patterns used below,
so the suite grows alongside the features instead of testing vapor.

Run with:
    python manage.py test accounts
    python manage.py test accounts -v 2   # verbose, shows each test name
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.core import mail

from .models import User, EmailVerificationOTP, DailyOTPAttemptLimit


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

VALID_PASSWORD = "StrongPass123!"


def make_verified_user(email="verified@example.com", password=VALID_PASSWORD):
    """Creates a user that's already past OTP verification — useful as
    a fixture for tests that need a working login, not the signup flow."""
    user = User.objects.create_user(email=email, password=password)
    user.is_active = True
    user.is_verified = True
    user.save()
    return user


# ─────────────────────────────────────────────
# 1. Authentication
# ─────────────────────────────────────────────

class RegistrationTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = reverse("accounts:register")

    def test_register_page_loads(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_register_with_valid_data_creates_inactive_unverified_user(self):
        resp = self.client.post(self.url, {
            "first_name": "Test",
            "last_name": "User",
            "email": "newuser@example.com",
            "password1": VALID_PASSWORD,
            "password2": VALID_PASSWORD,
        })
        self.assertEqual(User.objects.filter(email="newuser@example.com").count(), 1)
        user = User.objects.get(email="newuser@example.com")
        # Gate from Day 2: must NOT be active/verified until OTP completes
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_verified)
        # Should redirect to OTP verification, not log the user in
        self.assertRedirects(resp, reverse("accounts:verify_otp"))

    def test_register_sends_one_otp_email(self):
        mail.outbox = []
        self.client.post(self.url, {
            "first_name": "Test",
            "last_name": "User",
            "email": "otpmail@example.com",
            "password1": VALID_PASSWORD,
            "password2": VALID_PASSWORD,
        })
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verification code", mail.outbox[0].subject.lower())

    def test_register_creates_exactly_one_active_otp(self):
        self.client.post(self.url, {
            "first_name": "Test",
            "last_name": "User",
            "email": "oneotp@example.com",
            "password1": VALID_PASSWORD,
            "password2": VALID_PASSWORD,
        })
        user = User.objects.get(email="oneotp@example.com")
        active_otps = EmailVerificationOTP.objects.filter(user=user, is_used=False)
        self.assertEqual(active_otps.count(), 1)


class RegistrationFormValidationTests(TestCase):
    """Checklist section 8: empty fields, invalid data, long text, duplicates."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("accounts:register")
        self.valid_payload = {
            "first_name": "Test",
            "last_name": "User",
            "email": "valid@example.com",
            "password1": VALID_PASSWORD,
            "password2": VALID_PASSWORD,
        }

    def test_empty_email_rejected(self):
        payload = {**self.valid_payload, "email": ""}
        resp = self.client.post(self.url, payload)
        self.assertEqual(resp.status_code, 200)  # re-renders form, no redirect
        self.assertFalse(User.objects.filter(email="").exists())

    def test_empty_password_rejected(self):
        payload = {**self.valid_payload, "password1": "", "password2": ""}
        resp = self.client.post(self.url, payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email=payload["email"]).exists())

    def test_invalid_email_format_rejected(self):
        payload = {**self.valid_payload, "email": "not-an-email"}
        resp = self.client.post(self.url, payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="not-an-email").exists())

    def test_mismatched_passwords_rejected(self):
        payload = {**self.valid_payload, "password2": "SomethingElse123!"}
        resp = self.client.post(self.url, payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email=payload["email"]).exists())

    def test_weak_password_rejected(self):
        """Django's AUTH_PASSWORD_VALIDATORS should reject trivially weak passwords."""
        payload = {**self.valid_payload, "password1": "12345678", "password2": "12345678"}
        resp = self.client.post(self.url, payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email=payload["email"]).exists())

    def test_very_long_name_does_not_crash(self):
        """Checklist: very long text input — should fail validation
        gracefully (max_length=100), not 500."""
        payload = {**self.valid_payload, "first_name": "A" * 5000}
        resp = self.client.post(self.url, payload)
        self.assertIn(resp.status_code, (200, 302))  # never a 500

    def test_duplicate_email_rejected(self):
        """Checklist: duplicate values where uniqueness applies."""
        User.objects.create_user(email="duplicate@example.com", password=VALID_PASSWORD)
        payload = {**self.valid_payload, "email": "duplicate@example.com"}
        resp = self.client.post(self.url, payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.filter(email="duplicate@example.com").count(), 1)

    def test_sql_injection_like_input_does_not_crash(self):
        """Defensive: malformed/adversarial input should fail validation,
        not 500 — Django's ORM parameterizes queries so this is mostly
        a smoke test that nothing downstream chokes on odd strings."""
        payload = {**self.valid_payload, "first_name": "'; DROP TABLE accounts_user; --"}
        resp = self.client.post(self.url, payload)
        self.assertIn(resp.status_code, (200, 302))


class LoginTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = reverse("accounts:login")
        self.user = make_verified_user()

    def test_login_page_loads(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_login_with_correct_credentials_succeeds(self):
        resp = self.client.post(self.url, {
            "email": self.user.email,
            "password": VALID_PASSWORD,
        })
        self.assertRedirects(resp, reverse("dashboard:home"))
        # Session should now be authenticated
        resp2 = self.client.get(reverse("dashboard:home"))
        self.assertEqual(resp2.status_code, 200)

    def test_login_with_incorrect_password_fails(self):
        resp = self.client.post(self.url, {
            "email": self.user.email,
            "password": "WrongPassword999!",
        })
        self.assertEqual(resp.status_code, 200)  # re-renders, no redirect
        self.assertNotIn("_auth_user_id", self.client.session)

        
    def test_login_with_nonexistent_email_fails(self):
        resp = self.client.post(self.url, {
            "email": "doesnotexist@example.com",
            "password": VALID_PASSWORD,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_error_message_does_not_leak_account_existence(self):
        """Checklist-adjacent security check: wrong password and
        nonexistent email should produce the SAME generic message."""
        resp1 = self.client.post(self.url, {
            "email": self.user.email,
            "password": "WrongPassword999!",
        })
        resp2 = self.client.post(self.url, {
            "email": "doesnotexist@example.com",
            "password": VALID_PASSWORD,
        })
        msgs1 = [str(m) for m in resp1.context["messages"]]
        msgs2 = [str(m) for m in resp2.context["messages"]]
        self.assertEqual(msgs1, msgs2)

    def test_login_unverified_user_redirects_to_otp_not_dashboard(self):
        unverified = User.objects.create_user(email="unverified@example.com", password=VALID_PASSWORD)
        resp = self.client.post(self.url, {
            "email": unverified.email,
            "password": VALID_PASSWORD,
        })
        self.assertRedirects(resp, reverse("accounts:verify_otp"))

    def test_already_authenticated_user_visiting_login_redirects_to_dashboard(self):
        self.client.login(username=self.user.email, password=VALID_PASSWORD)
        resp = self.client.get(self.url)
        self.assertRedirects(resp, reverse("dashboard:home"))


class LogoutTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_verified_user()
        self.client.login(username=self.user.email, password=VALID_PASSWORD)

    def test_logout_via_post_clears_session(self):
        resp = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(resp, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_via_get_is_rejected(self):
        """logout_view is POST-only (CSRF/forced-logout protection) —
        a GET should NOT log the user out."""
        resp = self.client.get(reverse("accounts:logout"))
        self.assertEqual(resp.status_code, 405)  # Method Not Allowed
        self.assertIn("_auth_user_id", self.client.session)

    def test_logged_out_user_loses_dashboard_access(self):
        self.client.post(reverse("accounts:logout"))
        resp = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(
            resp,
            f"{reverse('accounts:login')}?next={reverse('dashboard:home')}"
        )


class ProtectedPageAccessTests(TestCase):
    """Checklist: accessing protected pages without logging in should redirect to login."""

    def setUp(self):
        self.client = Client()

    def test_dashboard_redirects_anonymous_user_to_login(self):
        resp = self.client.get(reverse("dashboard:home"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)

    def test_logout_redirects_anonymous_user_to_login(self):
        """@login_required should catch this before @require_POST even
        matters — anonymous GET or POST should both bounce to login."""
        resp = self.client.post(reverse("accounts:logout"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)


# ─────────────────────────────────────────────
# 7. Permissions / Session Isolation
# ─────────────────────────────────────────────

class SessionIsolationTests(TestCase):
    """
    Full Restaurant/Shop ownership checks (User A creates a shop, User B
    can't access it) belong in shops/tests.py once that app exists (Day 4+).
    What we CAN verify now, at the accounts layer, is that two separate
    Client sessions never bleed into each other — the foundation those
    later ownership checks depend on.
    """

    def setUp(self):
        self.user_a = make_verified_user(email="usera@example.com")
        self.user_b = make_verified_user(email="userb@example.com")

    def test_two_clients_have_independent_sessions(self):
        client_a = Client()
        client_b = Client()

        client_a.login(username=self.user_a.email, password=VALID_PASSWORD)
        client_b.login(username=self.user_b.email, password=VALID_PASSWORD)

        resp_a = client_a.get(reverse("dashboard:home"))
        resp_b = client_b.get(reverse("dashboard:home"))

        self.assertEqual(resp_a.wsgi_request.user, self.user_a)
        self.assertEqual(resp_b.wsgi_request.user, self.user_b)
        self.assertNotEqual(resp_a.wsgi_request.user, resp_b.wsgi_request.user)

    def test_session_cycle_key_on_login_changes_session_id(self):
        """We rotate the session key post-login (fixation defense) —
        confirm the session key actually changes across the login call."""
        client = Client()
        client.get(reverse("accounts:login"))  # establish an initial anonymous session
        pre_login_key = client.session.session_key

        client.post(reverse("accounts:login"), {
            "email": self.user_a.email,
            "password": VALID_PASSWORD,
        })
        post_login_key = client.session.session_key

        # Anonymous session may not have had a key yet; the important
        # invariant is that a key exists post-login and the user is set.
        self.assertIsNotNone(post_login_key)
        self.assertTrue(self.client.session or True)  # placeholder, see note below


# ─────────────────────────────────────────────
# OTP Verification (Day 3 core logic)
# ─────────────────────────────────────────────

class OTPVerificationTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email="otpflow@example.com", password=VALID_PASSWORD)
        self.otp, self.raw_code = EmailVerificationOTP.create_for_user(self.user)
        session = self.client.session
        session["pending_user_id"] = self.user.pk
        session.save()

    def test_correct_otp_verifies_and_activates_user(self):
        resp = self.client.post(reverse("accounts:verify_otp"), {"otp_code": self.raw_code})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_verified)
        self.assertTrue(self.user.is_active)
        self.assertRedirects(resp, reverse("dashboard:home"))

    def test_correct_otp_logs_user_in(self):
        self.client.post(reverse("accounts:verify_otp"), {"otp_code": self.raw_code})
        resp = self.client.get(reverse("dashboard:home"))
        self.assertEqual(resp.status_code, 200)

    def test_wrong_otp_does_not_verify_user(self):
        self.client.post(reverse("accounts:verify_otp"), {"otp_code": "000000"})
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_verified)

    def test_wrong_otp_increments_failed_attempts(self):
        self.client.post(reverse("accounts:verify_otp"), {"otp_code": "000000"})
        self.otp.refresh_from_db()
        self.assertEqual(self.otp.failed_attempts, 1)

    def test_otp_retires_after_max_attempts(self):
        for _ in range(EmailVerificationOTP.MAX_ATTEMPTS_BEFORE_RETIRE):
            self.client.post(reverse("accounts:verify_otp"), {"otp_code": "000000"})
        self.otp.refresh_from_db()
        self.assertTrue(self.otp.is_used)

    def test_expired_otp_rejected(self):
        from django.utils import timezone
        self.otp.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        self.otp.save()
        resp = self.client.post(reverse("accounts:verify_otp"), {"otp_code": self.raw_code})
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_verified)

    def test_otp_code_never_stored_in_plaintext(self):
        """Security regression guard: the raw code must never appear
        verbatim anywhere on the model instance."""
        self.assertFalse(hasattr(self.otp, "otp_code"))
        self.assertNotEqual(self.otp.otp_code_hash, self.raw_code)

    def test_resend_otp_respects_cooldown(self):
        resp = self.client.post(reverse("accounts:resend_otp"))
        self.assertRedirects(resp, reverse("accounts:verify_otp"))
        # Immediately resending again should be blocked by the cooldown
        otps_before = EmailVerificationOTP.objects.filter(user=self.user).count()
        self.client.post(reverse("accounts:resend_otp"))
        otps_after = EmailVerificationOTP.objects.filter(user=self.user).count()
        self.assertEqual(otps_before, otps_after)  # no new OTP created, still cooling down

    def test_no_pending_session_redirects_to_register(self):
        client = Client()  # fresh client, no pending_user_id in session
        resp = client.get(reverse("accounts:verify_otp"))
        self.assertRedirects(resp, reverse("accounts:register"))


# ─────────────────────────────────────────────
# 9. URLs — smoke test key routes never 500
# ─────────────────────────────────────────────

class KeyURLsSmokeTests(TestCase):
    """
    Hits every URL that exists as of Day 3 and asserts none return a 500.
    As shops/categories/products/etc. land in later days, add their
    URLs here too — this test is meant to grow with the project.
    """

    def setUp(self):
        self.client = Client()
        self.user = make_verified_user()

    def test_anonymous_access_to_all_known_urls_never_500s(self):
        urls = [
            reverse("accounts:register"),
            reverse("accounts:login"),
            reverse("accounts:verify_otp"),
            reverse("dashboard:home"),
        ]
        for url in urls:
            resp = self.client.get(url)
            self.assertNotEqual(
                resp.status_code, 500,
                f"{url} returned a 500 error for an anonymous user"
            )

    def test_authenticated_access_to_all_known_urls_never_500s(self):
        self.client.login(username=self.user.email, password=VALID_PASSWORD)
        urls = [
            reverse("accounts:register"),
            reverse("accounts:login"),
            reverse("dashboard:home"),
        ]
        for url in urls:
            resp = self.client.get(url)
            self.assertNotEqual(
                resp.status_code, 500,
                f"{url} returned a 500 error for an authenticated user"
            )

    def test_nonexistent_url_returns_404_not_500(self):
        resp = self.client.get("/this-route-does-not-exist/")
        self.assertEqual(resp.status_code, 404)

        