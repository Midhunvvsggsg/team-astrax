from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.core.management import call_command
from io import StringIO
from accounts.models import User
from catalog.models import Product, ProductVariant, Tax, Plan
from .models import QuotationTemplate, QuotationTemplateLine, Subscription, SubscriptionLine, SubscriptionStatusHistory

class SubscriptionLifecycleAndTransitionsTests(TestCase):
    def setUp(self):
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
        self.plan = Plan.objects.create(
            name="Professional SaaS",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY,
            minimum_quantity=2,
            auto_close_on_end=True,
            closable_early=True,
            pausable=True,
            renewable=True
        )
        self.product = Product.objects.create(
            name="BrightSuite Seat",
            sales_price=Decimal('50.00'),
            cost_price=Decimal('10.00')
        )
        self.tax = Tax.objects.create(
            name="Standard Tax 10%",
            percentage=Decimal('10.00')
        )

    def test_auto_generate_subscription_number(self):
        sub1 = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            start_date=date.today()
        )
        sub2 = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            start_date=date.today()
        )
        self.assertTrue(sub1.subscription_number.startswith("SUB-"))
        self.assertTrue(sub2.subscription_number.startswith("SUB-"))
        self.assertNotEqual(sub1.subscription_number, sub2.subscription_number)

    def test_legal_status_transition_sequence(self):
        """Tests complete valid flow: Draft > Quotation > Confirmed > Active > Closed."""
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.DRAFT
        )
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.product,
            quantity=2,
            unit_price=Decimal('50.00')
        )

        # 1. Draft -> Quotation
        sub.transition_to(Subscription.Status.QUOTATION, user=self.staff, notes="Quoted to customer")
        self.assertEqual(sub.status, Subscription.Status.QUOTATION)

        # 2. Quotation -> Confirmed
        sub.transition_to(Subscription.Status.CONFIRMED, user=self.customer, notes="Customer agreed")
        self.assertEqual(sub.status, Subscription.Status.CONFIRMED)

        # 3. Confirmed -> Active
        sub.transition_to(Subscription.Status.ACTIVE, user=self.staff, notes="Activated")
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

        # 4. Active -> Closed
        sub.transition_to(Subscription.Status.CLOSED, user=self.staff, notes="End of term")
        self.assertEqual(sub.status, Subscription.Status.CLOSED)

        # Verify audit history
        history = list(sub.status_history.order_by('changed_at'))
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0].to_status, Subscription.Status.QUOTATION)
        self.assertEqual(history[1].to_status, Subscription.Status.CONFIRMED)
        self.assertEqual(history[2].to_status, Subscription.Status.ACTIVE)
        self.assertEqual(history[3].to_status, Subscription.Status.CLOSED)

    def test_illegal_status_transition_rejected(self):
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.DRAFT
        )
        # Attempting to jump directly from Draft to Active
        with self.assertRaises(ValidationError):
            sub.transition_to(Subscription.Status.ACTIVE)

        # Attempting to reopen Closed directly
        sub.status = Subscription.Status.CLOSED
        sub.save()
        with self.assertRaises(ValidationError):
            sub.transition_to(Subscription.Status.ACTIVE)

    def test_plan_pausable_flag_enforcement(self):
        non_pausable_plan = Plan.objects.create(
            name="Strict Yearly",
            price=Decimal('500.00'),
            billing_period=Plan.BillingPeriod.YEARLY,
            pausable=False
        )
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=non_pausable_plan,
            status=Subscription.Status.ACTIVE
        )
        # Pausing should be rejected because plan.pausable is False
        with self.assertRaises(ValidationError) as ctx:
            sub.transition_to(Subscription.Status.PAUSED)
        self.assertIn("does not allow pausing", str(ctx.exception))

        # But if plan.pausable is True, pausing succeeds
        sub.plan = self.plan # pausable is True
        sub.save()
        sub.transition_to(Subscription.Status.PAUSED)
        self.assertEqual(sub.status, Subscription.Status.PAUSED)

    def test_plan_closable_early_flag_enforcement(self):
        non_closable_plan = Plan.objects.create(
            name="Locked Contract",
            price=Decimal('1000.00'),
            billing_period=Plan.BillingPeriod.YEARLY,
            closable_early=False
        )
        future_expiry = date.today() + timedelta(days=180)
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=non_closable_plan,
            status=Subscription.Status.ACTIVE,
            expiry_date=future_expiry
        )
        # Attempt early closure
        with self.assertRaises(ValidationError) as ctx:
            sub.transition_to(Subscription.Status.CLOSED)
        self.assertIn("does not permit early closure", str(ctx.exception))

    def test_plan_renewable_flag_enforcement(self):
        non_renewable_plan = Plan.objects.create(
            name="One-off Pilot",
            price=Decimal('100.00'),
            renewable=False
        )
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=non_renewable_plan,
            status=Subscription.Status.ACTIVE,
            expiry_date=date.today() + timedelta(days=10)
        )
        with self.assertRaises(ValidationError) as ctx:
            sub.renew()
        self.assertIn("cannot be renewed", str(ctx.exception))

        # Renewable plan succeeds
        sub.plan = self.plan
        sub.save()
        initial_expiry = sub.expiry_date
        sub.renew()
        self.assertGreater(sub.expiry_date, initial_expiry)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

    def test_minimum_quantity_validation(self):
        # self.plan has minimum_quantity = 2
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.DRAFT
        )
        # Line with quantity 1
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.product,
            quantity=1,
            unit_price=Decimal('50.00')
        )
        # Fails minimum quantity check
        with self.assertRaises(ValidationError):
            sub.validate_minimum_quantity()

        # Add another line or increase quantity
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.product,
            quantity=1,
            unit_price=Decimal('50.00')
        )
        # Now total quantity is 2 >= plan.minimum_quantity
        try:
            sub.validate_minimum_quantity()
        except ValidationError:
            self.fail("validate_minimum_quantity raised ValidationError unexpectedly.")

    def test_close_expired_subscriptions_management_command(self):
        """Tests the close_expired_subscriptions command."""
        yesterday = date.today() - timedelta(days=1)
        tomorrow = date.today() + timedelta(days=1)

        # 1. Expired active with auto_close_on_end=True -> Should close
        expired_auto = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan, # auto_close_on_end=True
            status=Subscription.Status.ACTIVE,
            expiry_date=yesterday
        )

        # 2. Expired active with auto_close_on_end=False -> Should remain active
        manual_plan = Plan.objects.create(
            name="Manual Plan",
            price=Decimal('100.00'),
            auto_close_on_end=False
        )
        expired_manual = Subscription.objects.create(
            customer=self.customer,
            plan=manual_plan,
            status=Subscription.Status.ACTIVE,
            expiry_date=yesterday
        )

        # 3. Not yet expired -> Should remain active
        active_current = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.ACTIVE,
            expiry_date=tomorrow
        )

        out = StringIO()
        call_command('close_expired_subscriptions', stdout=out)

        expired_auto.refresh_from_db()
        expired_manual.refresh_from_db()
        active_current.refresh_from_db()

        self.assertEqual(expired_auto.status, Subscription.Status.CLOSED)
        self.assertEqual(expired_manual.status, Subscription.Status.ACTIVE)
        self.assertEqual(active_current.status, Subscription.Status.ACTIVE)
        self.assertIn("Successfully closed 1 expired subscription(s)", out.getvalue())


class QuotationTemplateAndCreateFromTemplateTests(TestCase):
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
        self.plan = Plan.objects.create(
            name="Enterprise Monthly",
            price=Decimal('200.00'),
            billing_period=Plan.BillingPeriod.MONTHLY
        )
        self.product = Product.objects.create(
            name="Cloud Storage 500GB",
            sales_price=Decimal('40.00'),
            cost_price=Decimal('10.00')
        )
        self.tax = Tax.objects.create(
            name="GST 18%",
            percentage=Decimal('18.00')
        )
        self.template = QuotationTemplate.objects.create(
            name="Standard Cloud SaaS Offer",
            plan=self.plan,
            validity_days=15
        )
        QuotationTemplateLine.objects.create(
            template=self.template,
            product=self.product,
            quantity=3,
            unit_price=Decimal('40.00'),
            tax=self.tax
        )

    def test_create_subscription_from_template_view(self):
        self.client.force_login(self.staff)
        start_date = date.today()
        response = self.client.post(reverse('subscriptions:subscription_from_template'), {
            'template': self.template.pk,
            'customer': self.customer.pk,
            'start_date': start_date.strftime('%Y-%m-%d'),
            'payment_terms': '15_days',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        sub = Subscription.objects.filter(customer=self.customer).first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.plan, self.plan)
        self.assertEqual(sub.status, Subscription.Status.QUOTATION)
        # Expiry date should equal start_date + validity_days (15 days)
        self.assertEqual(sub.expiry_date, start_date + timedelta(days=15))
        # Lines copied
        self.assertEqual(sub.lines.count(), 1)
        line = sub.lines.first()
        self.assertEqual(line.quantity, 3)
        self.assertEqual(line.unit_price, Decimal('40.00'))
        self.assertEqual(line.tax, self.tax)


class SubscriptionCustomerPermissionIsolationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.plan = Plan.objects.create(name="Basic", price=Decimal('10.00'))
        self.customer1 = User.objects.create_user(
            email='alice@customer.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.customer2 = User.objects.create_user(
            email='bob@customer.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.sub_alice = Subscription.objects.create(
            customer=self.customer1,
            plan=self.plan,
            status=Subscription.Status.ACTIVE
        )

    def test_customer_can_view_own_subscription(self):
        self.client.force_login(self.customer1)
        response = self.client.get(reverse('subscriptions:subscription_detail', kwargs={'pk': self.sub_alice.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.sub_alice.subscription_number)

    def test_customer_cannot_view_other_customer_subscription(self):
        self.client.force_login(self.customer2)
        response = self.client.get(reverse('subscriptions:subscription_detail', kwargs={'pk': self.sub_alice.pk}))
        self.assertEqual(response.status_code, 403)
