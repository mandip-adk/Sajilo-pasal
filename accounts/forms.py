from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

User = get_user_model()


class RegistrationForm(forms.ModelForm):
    """
    Owner registration form.
    Password is validated against Django's AUTH_PASSWORD_VALIDATORS.
    OTP is sent after successful.
    """

    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Create a strong password",
            "autocomplete": "new-password",
        }),
    )
    password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Re-enter your password",
            "autocomplete": "new-password",
        }),
    )

    class Meta:
        model  = User
        fields = ["first_name", "last_name", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "First name",
                "autofocus": True,
            }),
            "last_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Last name",
            }),
            "email": forms.EmailInput(attrs={
                "class": "form-control",
                "placeholder": "your@email.com",
                "autocomplete": "email",
            }),
        }

    def clean_email(self):
        email = self.cleaned_data.get("email", "").lower().strip()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_password1(self):
        password = self.cleaned_data.get("password1")
        validate_password(password)
        return password

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get("password1")
        p2 = cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email      = self.cleaned_data["email"]
        user.is_active  = False   # held inactive until OTP verified
        user.is_verified = False
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class LoginForm(forms.Form):
    """
    Simple email + password login form.
    Validation of credentials is handled in the view.
    """

    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "your@email.com",
            "autofocus": True,
            "autocomplete": "email",
        }),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Your password",
            "autocomplete": "current-password",
        }),
    )

    def clean_email(self):
        return self.cleaned_data.get("email", "").lower().strip()


class OTPVerificationForm(forms.Form):
    """
    6-digit OTP input.
    Numeric-only, exactly 6 characters, rendered as a single text field
    (kept simple rather than 6 separate boxes — easier on low-end devices).
    """

    otp_code = forms.CharField(
        label="Verification Code",
        min_length=6,
        max_length=6,
        widget=forms.TextInput(attrs={
            "class": "form-control form-control-lg text-center",
            "placeholder": "000000",
            "autocomplete": "one-time-code",
            "inputmode": "numeric",
            "pattern": "[0-9]*",
            "autofocus": True,
        }),
    )

    def clean_otp_code(self):
        code = self.cleaned_data.get("otp_code", "").strip()
        if not code.isdigit():
            raise forms.ValidationError("OTP must contain digits only.")
        return code


class ProfileUpdateForm(forms.ModelForm):
    """
    Lets an owner update their display name. Email is deliberately
    NOT editable here — it's tied to OTP-based account verification
    and used as the login identifier, so changing it needs its own
    dedicated re-verification flow (not built yet — future enhancement).
    """

    class Meta:
        model  = User
        fields = ["first_name", "last_name"]
        widgets = {
            "first_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "First name",
                "autofocus": True,
            }),
            "last_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Last name",
            }),
        }

        