from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from accounts.models import User
from accounts.validators import BrightloopPasswordValidator
from accounts.forms import CustomerSignupForm, StaffCreationForm

class CustomUserModelTests(TestCase):
    def test_create_user_normalizes_and_lowercases_email(self):
        user = User.objects.create_user(
            email="TestUser@EXAMPLE.com",
            password="SecurePass@123",
            first_name="Test",
            last_name="User"
        )
        self.assertEqual(user.email, "testuser@example.com")
        self.assertEqual(user.role, User.Role.CUSTOMER)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_superuser(self):
        admin = User.objects.create_superuser(
            email="admin@example.com",
            password="AdminPass@123"
        )
        self.assertEqual(admin.role, User.Role.ADMIN)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_case_insensitive_unique_email_in_form(self):
        User.objects.create_user(email="member@brightloop.com", password="Password@123")
        form = CustomerSignupForm(data={
            'first_name': 'Another',
            'last_name': 'Member',
            'email': 'MEMBER@BRIGHTLOOP.COM',
            'password': 'StrongPassword@1',
            'confirm_password': 'StrongPassword@1'
        })
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)


class PasswordValidatorTests(TestCase):
    def setUp(self):
        self.validator = BrightloopPasswordValidator()

    def test_password_length_must_be_more_than_8(self):
        # 8 characters should fail (must be MORE than 8, so at least 9)
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("Pass@123")
        self.assertEqual(ctx.exception.code, 'password_too_short')

        # 9 characters should pass this rule
        self.validator.validate("Pass@1234")

    def test_password_requires_uppercase(self):
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("pass@12345")
        self.assertEqual(ctx.exception.code, 'password_no_upper')

    def test_password_requires_lowercase(self):
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("PASS@12345")
        self.assertEqual(ctx.exception.code, 'password_no_lower')

    def test_password_requires_special_character(self):
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("Password12345")
        self.assertEqual(ctx.exception.code, 'password_no_special')

    def test_valid_password(self):
        # Valid password: length >= 9, has upper, lower, special
        try:
            self.validator.validate("SecurePass@2026")
        except ValidationError:
            self.fail("Valid password raised ValidationError unexpectedly")


class CustomerSignupTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_signup_creates_customer_role_only(self):
        response = self.client.post(reverse('signup'), {
            'first_name': 'Alice',
            'last_name': 'Smith',
            'email': 'alice@customer.com',
            'password': 'SecureAlice@123',
            'confirm_password': 'SecureAlice@123',
        }, follow=True)

        self.assertRedirects(response, reverse('customer_portal'))
        user = User.objects.get(email='alice@customer.com')
        self.assertEqual(user.role, User.Role.CUSTOMER)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_signup_fails_on_password_mismatch(self):
        response = self.client.post(reverse('signup'), {
            'first_name': 'Bob',
            'last_name': 'Smith',
            'email': 'bob@customer.com',
            'password': 'SecurePass@123',
            'confirm_password': 'DifferentPass@123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='bob@customer.com').exists())


class AuthenticationAndRoleAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='admin@brightloop.com',
            password='AdminPassword@1'
        )
        self.staff = User.objects.create_user(
            email='staff@brightloop.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.customer = User.objects.create_user(
            email='customer@brightloop.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )

    def test_case_insensitive_login(self):
        # User created with customer@brightloop.com
        # Attempt login with UPPERCASE email
        response = self.client.post(reverse('login'), {
            'email': 'CUSTOMER@BRIGHTLOOP.COM',
            'password': 'CustomerPassword@1',
        }, follow=True)
        self.assertRedirects(response, reverse('customer_portal'))
        self.assertTrue(response.context['user'].is_authenticated)

    def test_staff_management_unauthenticated_redirects(self):
        response = self.client.get(reverse('staff_management'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_staff_management_forbidden_for_customer(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('staff_management'))
        self.assertEqual(response.status_code, 403)

    def test_staff_management_forbidden_for_staff(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('staff_management'))
        self.assertEqual(response.status_code, 403)

    def test_staff_management_allowed_for_admin(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff_management'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff Management")

    def test_admin_can_create_staff_user(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('staff_management'), {
            'first_name': 'John',
            'last_name': 'Ops',
            'email': 'john.ops@brightloop.com',
            'password': 'StaffSecret@123',
        }, follow=True)
        self.assertEqual(response.status_code, 200)

        created_staff = User.objects.get(email='john.ops@brightloop.com')
        self.assertEqual(created_staff.role, User.Role.STAFF)
        self.assertTrue(created_staff.is_staff)
        self.assertFalse(created_staff.is_superuser)

    def test_customer_portal_forbidden_for_staff(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('customer_portal'))
        self.assertEqual(response.status_code, 403)

    def test_customer_portal_accessible_by_customer(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('customer_portal'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Customer Portal")


class PasswordResetTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='user@brightloop.com',
            password='InitialPassword@1'
        )

    def test_password_reset_flow(self):
        response = self.client.post(reverse('password_reset'), {
            'email': 'user@brightloop.com'
        }, follow=True)
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password Reset Link Sent")
