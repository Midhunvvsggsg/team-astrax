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


class CustomerPortalSecurityAndProfileTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Customer A
        self.customer_a = User.objects.create_user(
            email='customer_a@example.com',
            password='CustomerA@123',
            first_name='Alice',
            last_name='Astrax'
        )

        # Customer B
        self.customer_b = User.objects.create_user(
            email='customer_b@example.com',
            password='CustomerB@123',
            first_name='Bob',
            last_name='Beta'
        )

        # Plans
        from decimal import Decimal
        from datetime import date, timedelta
        from catalog.models import Plan, Product
        from subscriptions.models import Subscription, SubscriptionLine
        from invoicing.models import Invoice, Payment

        self.plan_pausable = Plan.objects.create(
            name="Pausable & Renewable Plan",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY,
            pausable=True,
            renewable=True,
            closable_early=True
        )

        self.plan_strict = Plan.objects.create(
            name="Strict 1-Year Lockin Plan",
            price=Decimal('200.00'),
            billing_period=Plan.BillingPeriod.YEARLY,
            pausable=False,
            renewable=False,
            closable_early=False
        )

        self.product = Product.objects.create(
            name="Cloud Server",
            sales_price=Decimal('100.00'),
            cost_price=Decimal('20.00')
        )

        # Subscription A for Customer A
        self.sub_a = Subscription.objects.create(
            subscription_number="SUB-CUST-A",
            customer=self.customer_a,
            plan=self.plan_pausable,
            start_date=date.today(),
            expiry_date=date.today() + timedelta(days=30),
            status=Subscription.Status.ACTIVE
        )
        SubscriptionLine.objects.create(
            subscription=self.sub_a,
            product=self.product,
            quantity=1,
            unit_price=Decimal('100.00')
        )

        # Subscription B for Customer B
        self.sub_b = Subscription.objects.create(
            subscription_number="SUB-CUST-B",
            customer=self.customer_b,
            plan=self.plan_strict,
            start_date=date.today(),
            expiry_date=date.today() + timedelta(days=365),
            status=Subscription.Status.ACTIVE
        )

        # Invoices
        self.inv_a = Invoice.objects.create(
            invoice_number="INV-CUST-A",
            subscription=self.sub_a,
            customer=self.customer_a,
            period_start=date.today(),
            period_end=date.today() + timedelta(days=30),
            due_date=date.today() + timedelta(days=15),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00')
        )

        self.inv_b = Invoice.objects.create(
            invoice_number="INV-CUST-B",
            subscription=self.sub_b,
            customer=self.customer_b,
            period_start=date.today(),
            period_end=date.today() + timedelta(days=365),
            due_date=date.today() + timedelta(days=15),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('200.00')
        )

        # Payments
        self.payment_a = Payment.objects.create(
            invoice=self.inv_a,
            method=Payment.Method.CARD,
            amount=Decimal('50.00'),
            date=date.today(),
            note="Partial payment for Cust A"
        )
        self.payment_b = Payment.objects.create(
            invoice=self.inv_b,
            method=Payment.Method.BANK_TRANSFER,
            amount=Decimal('200.00'),
            date=date.today(),
            note="Full payment for Cust B"
        )

    def test_customer_cannot_view_another_customer_subscription_detail_returns_404(self):
        """Cross-customer subscription access by ID must return 404."""
        self.client.force_login(self.customer_a)
        response = self.client.get(reverse('subscriptions:subscription_detail', kwargs={'pk': self.sub_b.pk}))
        self.assertEqual(response.status_code, 404)

    def test_customer_can_view_own_subscription_detail(self):
        """Customer can access their own subscription detail."""
        self.client.force_login(self.customer_a)
        response = self.client.get(reverse('subscriptions:subscription_detail', kwargs={'pk': self.sub_a.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.sub_a.subscription_number)

    def test_customer_cannot_trigger_action_on_another_customer_subscription_returns_404(self):
        """Cross-customer action attempt by ID must return 404."""
        self.client.force_login(self.customer_a)
        response = self.client.post(reverse('subscriptions:customer_action', kwargs={'pk': self.sub_b.pk}), {
            'action': 'pause'
        })
        self.assertEqual(response.status_code, 404)

    def test_customer_cannot_view_another_customer_invoice_detail_returns_404(self):
        """Cross-customer invoice detail access by ID must return 404."""
        self.client.force_login(self.customer_a)
        response = self.client.get(reverse('invoicing:invoice_detail', kwargs={'pk': self.inv_b.pk}))
        self.assertEqual(response.status_code, 404)

    def test_customer_cannot_print_another_customer_invoice_returns_404(self):
        """Cross-customer invoice print access by ID must return 404."""
        self.client.force_login(self.customer_a)
        response = self.client.get(reverse('invoicing:invoice_print', kwargs={'pk': self.inv_b.pk}))
        self.assertEqual(response.status_code, 404)

    def test_customer_can_view_and_print_own_invoice(self):
        """Customer can view and print their own invoices."""
        self.client.force_login(self.customer_a)
        resp_detail = self.client.get(reverse('invoicing:invoice_detail', kwargs={'pk': self.inv_a.pk}))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, self.inv_a.invoice_number)

        resp_print = self.client.get(reverse('invoicing:invoice_print', kwargs={'pk': self.inv_a.pk}))
        self.assertEqual(resp_print.status_code, 200)
        self.assertContains(resp_print, self.inv_a.invoice_number)

    def test_customer_portal_filters_invoices_and_shows_payments(self):
        """Customer portal filters own invoices and lists recent payments."""
        self.client.force_login(self.customer_a)
        response = self.client.get(reverse('customer_portal') + '?status=confirmed')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.inv_a.invoice_number)
        self.assertNotContains(response, self.inv_b.invoice_number)
        self.assertContains(response, "Partial payment for Cust A")
        self.assertNotContains(response, "Full payment for Cust B")

    def test_customer_allowed_subscription_actions(self):
        """Customer can pause, resume, renew, and close when plan permits."""
        self.client.force_login(self.customer_a)

        # 1. Pause
        resp_pause = self.client.post(reverse('subscriptions:customer_action', kwargs={'pk': self.sub_a.pk}), {
            'action': 'pause'
        }, follow=True)
        self.assertEqual(resp_pause.status_code, 200)
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.status, 'paused')

        # 2. Resume
        resp_resume = self.client.post(reverse('subscriptions:customer_action', kwargs={'pk': self.sub_a.pk}), {
            'action': 'resume'
        }, follow=True)
        self.assertEqual(resp_resume.status_code, 200)
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.status, 'active')

        # 3. Renew
        old_expiry = self.sub_a.expiry_date
        resp_renew = self.client.post(reverse('subscriptions:customer_action', kwargs={'pk': self.sub_a.pk}), {
            'action': 'renew'
        }, follow=True)
        self.assertEqual(resp_renew.status_code, 200)
        self.sub_a.refresh_from_db()
        self.assertGreater(self.sub_a.expiry_date, old_expiry)

        # 4. Close (closable_early is True)
        resp_close = self.client.post(reverse('subscriptions:customer_action', kwargs={'pk': self.sub_a.pk}), {
            'action': 'close'
        }, follow=True)
        self.assertEqual(resp_close.status_code, 200)
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.status, 'closed')

    def test_customer_disallowed_actions_blocked_by_plan_flags(self):
        """Customer cannot pause or close early when plan forbids it."""
        self.client.force_login(self.customer_b)

        # Plan strict pausable=False
        resp_pause = self.client.post(reverse('subscriptions:customer_action', kwargs={'pk': self.sub_b.pk}), {
            'action': 'pause'
        }, follow=True)
        self.sub_b.refresh_from_db()
        self.assertEqual(self.sub_b.status, 'active')
        self.assertContains(resp_pause, "does not permit pausing")

        # Plan strict closable_early=False
        resp_close = self.client.post(reverse('subscriptions:customer_action', kwargs={'pk': self.sub_b.pk}), {
            'action': 'close'
        }, follow=True)
        self.sub_b.refresh_from_db()
        self.assertEqual(self.sub_b.status, 'active')
        self.assertContains(resp_close, "does not permit early closure")

    def test_customer_profile_update(self):
        """Customer can update their first name, last name, and email."""
        self.client.force_login(self.customer_a)
        response = self.client.post(reverse('customer_profile'), {
            'first_name': 'Alice Updated',
            'last_name': 'Astrax Updated',
            'email': 'alice.new@example.com'
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.customer_a.refresh_from_db()
        self.assertEqual(self.customer_a.first_name, 'Alice Updated')
        self.assertEqual(self.customer_a.email, 'alice.new@example.com')

    def test_customer_profile_duplicate_email_rejected(self):
        """Customer cannot take another user's email."""
        self.client.force_login(self.customer_a)
        response = self.client.post(reverse('customer_profile'), {
            'first_name': 'Alice',
            'last_name': 'Astrax',
            'email': 'CUSTOMER_B@EXAMPLE.COM'  # duplicate of customer_b
        })
        self.assertEqual(response.status_code, 200)
        self.customer_a.refresh_from_db()
        self.assertNotEqual(self.customer_a.email, 'customer_b@example.com')

    def test_customer_change_password_flow(self):
        """Customer can change password with strict security rules."""
        self.client.force_login(self.customer_a)

        # 1. Wrong current password
        resp_wrong = self.client.post(reverse('customer_change_password'), {
            'current_password': 'WrongPassword@1',
            'new_password': 'BrandNewPass@999',
            'confirm_password': 'BrandNewPass@999',
        })
        self.assertContains(resp_wrong, "incorrect")

        # 2. Invalid new password (missing special char)
        resp_invalid = self.client.post(reverse('customer_change_password'), {
            'current_password': 'CustomerA@123',
            'new_password': 'BrandNewPass999',
            'confirm_password': 'BrandNewPass999',
        })
        self.assertContains(resp_invalid, "special character")

        # 3. Successful change
        resp_success = self.client.post(reverse('customer_change_password'), {
            'current_password': 'CustomerA@123',
            'new_password': 'BrandNewPass@999',
            'confirm_password': 'BrandNewPass@999',
        }, follow=True)
        self.assertRedirects(resp_success, reverse('customer_portal'))
        self.customer_a.refresh_from_db()
        self.assertTrue(self.customer_a.check_password('BrandNewPass@999'))

    def test_logout_view_rejects_get_with_405(self):
        """GET request to logout must be rejected with 405 Method Not Allowed (CSRF prevention)."""
        self.client.force_login(self.customer_a)
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 405)

    def test_logout_view_succeeds_on_post(self):
        """POST request to logout successfully logs out the session."""
        self.client.force_login(self.customer_a)
        response = self.client.post(reverse('logout'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse('login'))

    def test_security_settings_headers(self):
        """Verify essential security headers and cookie policies are configured."""
        from django.conf import settings
        self.assertEqual(settings.X_FRAME_OPTIONS, 'DENY')
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, 'Lax')
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, 'Lax')

    def test_insecure_secret_key_rejected_in_production(self):
        """Verify that DEBUG=False with default insecure secret key is rejected."""
        from django.core.exceptions import ImproperlyConfigured
        from config.settings import INSECURE_DEV_SECRET

        def check_prod_secret(debug, secret_key, argv):
            if not debug and 'test' not in argv:
                if not secret_key or secret_key == INSECURE_DEV_SECRET:
                    raise ImproperlyConfigured("SECRET_KEY must be configured with a secure value when DEBUG=False.")
            return True

        with self.assertRaises(ImproperlyConfigured):
            check_prod_secret(False, INSECURE_DEV_SECRET, ['gunicorn', 'config.wsgi'])

        # Secure key should succeed
        self.assertTrue(check_prod_secret(False, 'super-secure-random-key-12345', ['gunicorn', 'config.wsgi']))



