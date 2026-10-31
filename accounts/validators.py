import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class AstraFlowPasswordValidator:
    """
    Validates that the password:
    1. Has more than 8 characters (at least 9 characters).
    2. Contains at least one uppercase letter.
    3. Contains at least one lowercase letter.
    4. Contains at least one special character.
    """

    def validate(self, password, user=None):
        if len(password) <= 8:
            raise ValidationError(
                _("Password must be more than 8 characters long (at least 9 characters)."),
                code='password_too_short',
            )
        if not re.search(r'[A-Z]', password):
            raise ValidationError(
                _("Password must contain at least one uppercase letter (A-Z)."),
                code='password_no_upper',
            )
        if not re.search(r'[a-z]', password):
            raise ValidationError(
                _("Password must contain at least one lowercase letter (a-z)."),
                code='password_no_lower',
            )
        if not re.search(r'[^A-Za-z0-9]', password):
            raise ValidationError(
                _("Password must contain at least one special character (e.g. !@#$%^&*)."),
                code='password_no_special',
            )

    def get_help_text(self):
        return _(
            "Your password must be more than 8 characters and include at least one uppercase letter, "
            "one lowercase letter, and one special character."
        )
