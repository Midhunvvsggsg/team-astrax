from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from accounts.models import User
from accounts.validators import BrightloopPasswordValidator


class Command(BaseCommand):
    help = "Creates an initial Admin user for Brightloop Subscription Management System"

    def add_arguments(self, parser):
        parser.add_argument('--email', type=str, default='admin@brightloop.com', help='Admin email')
        parser.add_argument('--password', type=str, default='Admin@12345', help='Admin password')
        parser.add_argument('--first-name', type=str, default='Brightloop', help='First name')
        parser.add_argument('--last-name', type=str, default='Admin', help='Last name')

    def handle(self, *args, **options):
        email = options['email'].strip().lower()
        password = options['password']
        first_name = options['first_name']
        last_name = options['last_name']

        validator = BrightloopPasswordValidator()
        try:
            validator.validate(password)
        except ValidationError as e:
            self.stderr.write(self.style.ERROR(f"Password validation error: {e}"))
            return

        user = User.objects.filter(email__iexact=email).first()
        if user:
            user.role = User.Role.ADMIN
            user.is_staff = True
            user.is_superuser = True
            user.first_name = first_name
            user.last_name = last_name
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Updated existing user '{email}' to Admin with new password."))
        else:
            User.objects.create_superuser(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name
            )
            self.stdout.write(self.style.SUCCESS(f"Successfully created Admin user: {email}"))
