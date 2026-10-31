from django import forms
from django.contrib.auth import authenticate, password_validation
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from .models import User
from .validators import BrightloopPasswordValidator

class CustomerSignupForm(forms.ModelForm):
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Min 9 chars, 1 uppercase, 1 lowercase, 1 special',
            'id': 'id_password',
            'autocomplete': 'new-password',
            'class': 'form-input'
        }),
        help_text=_(
            "Password must be more than 8 characters, with an uppercase letter, "
            "a lowercase letter, and a special character."
        )
    )
    confirm_password = forms.CharField(
        label=_("Confirm Password"),
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm your password',
            'id': 'id_confirm_password',
            'autocomplete': 'new-password',
            'class': 'form-input'
        })
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'placeholder': 'Jane', 'class': 'form-input'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Doe', 'class': 'form-input'}),
            'email': forms.EmailInput(attrs={'placeholder': 'jane@example.com', 'id': 'id_email', 'class': 'form-input'}),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("An account with this email address already exists."))
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        if password and confirm_password:
            if password != confirm_password:
                self.add_error('confirm_password', _("Passwords do not match."))
            else:
                validator = BrightloopPasswordValidator()
                try:
                    validator.validate(password)
                except ValidationError as e:
                    self.add_error('password', e)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.CUSTOMER
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class StaffCreationForm(forms.ModelForm):
    password = forms.CharField(
        label=_("Initial Password"),
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Min 9 chars, 1 upper, 1 lower, 1 special',
            'id': 'id_staff_password',
            'class': 'form-input'
        }),
        help_text=_(
            "Password must be more than 8 characters, with an uppercase letter, "
            "a lowercase letter, and a special character."
        )
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'placeholder': 'First Name', 'class': 'form-input'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Last Name', 'class': 'form-input'}),
            'email': forms.EmailInput(attrs={'placeholder': 'staff@brightloop.com', 'class': 'form-input'}),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("An account with this email address already exists."))
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        if password:
            validator = BrightloopPasswordValidator()
            try:
                validator.validate(password)
            except ValidationError as e:
                self.add_error('password', e)
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.STAFF
        user.is_staff = True
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class LoginForm(forms.Form):
    email = forms.EmailField(
        label=_("Email Address"),
        widget=forms.EmailInput(attrs={'placeholder': 'name@example.com', 'class': 'form-input', 'autofocus': True})
    )
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={'placeholder': '••••••••', 'class': 'form-input'})
    )

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email', '').strip().lower()
        password = cleaned_data.get('password')

        if email and password:
            self.user = authenticate(username=email, password=password)
            if self.user is None:
                raise ValidationError(_("Invalid email or password."))
            if not self.user.is_active:
                raise ValidationError(_("This account is inactive."))

        return cleaned_data

    def get_user(self):
        return getattr(self, 'user', None)
