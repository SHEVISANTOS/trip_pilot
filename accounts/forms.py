import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

User = get_user_model()


def _generate_username(email: str) -> str:
    """The User model still has a `username` column (unique, required) —
    changing that would mean swapping AUTH_USER_MODEL, which is unsafe with
    real users already in the database. So username stays, just hidden:
    derived from the email's local part, with a numeric suffix on collision.
    """
    base = re.sub(r"[^a-zA-Z0-9_.-]", "", email.split("@", 1)[0]).strip(".-_") or "user"
    base = base[:145]
    username = base
    n = 1
    while User.objects.filter(username=username).exists():
        n += 1
        suffix = str(n)
        username = f"{base[: 150 - len(suffix)]}{suffix}"
    return username


class SignupForm(UserCreationForm):
    """Email + password only — no visible username field. The model still
    has one (see _generate_username above), filled in automatically.
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.fields["email"].widget.attrs["autofocus"] = True

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = _generate_username(self.cleaned_data["email"])
        if commit:
            user.save()
        return user


class EmailAuthenticationForm(AuthenticationForm):
    """Relabels the login field "Email" for the flow every new signup now
    goes through, but keeps it a plain CharField (not EmailField) rather
    than validating email syntax — accounts created before this change have
    no email on file at all, so they can only still log in with their
    original username. A strict EmailField would reject that input outright
    before authenticate() ever got to try accounts.backends.EmailBackend's
    email lookup *and* the username fallback both.
    """

    username = forms.CharField(label="Email", widget=forms.TextInput(attrs={"autofocus": True}))
