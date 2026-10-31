from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.db import IntegrityError
from accounts.models import User
from catalog.models import Product, ProductVariant, Tax, Plan
from subscriptions.models import Subscription, SubscriptionLine, DiscountRule
from invoicing.models import Invoice, InvoiceLine
from invoicing.services import generate_invoices, advance_period_date, add_months

class InvoiceGenerationServiceTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            email='acme@customer.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.plan_monthly = Plan.objects.create(
            name="Cloud Monthly",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY
        )
        self.product = Product.objects.create(
            name="User License",
            sales_price=Decimal('50.00')
        )
        self.tax_10 = Tax.objects.create(
            name="VAT 10%",
            percentage=Decimal('10.00')
        )

    def test_month_end_date_advancement(self):
        """Verify Jan 31 advances to Feb 28 in non-leap year (e.g. 2025/2026)."""
        jan_31 = date(2026, 1, 31)
        feb_next = advance_period_date(jan_31, 'monthly')
        self.assertEqual(feb_next, date(2026, 2, 28))

        # Leap year Jan 31 -> Feb 29
        jan_31_leap = date(2024, 1, 31)
        feb_next_leap = advance_period_date(jan_31_leap, 'monthly')
        self.assertEqual(feb_next_leap, date(2024, 2, 29))

        # Mar 31 -> Apr 30
        mar_31 = date(2026, 3, 31)
        apr_next = advance_period_date(mar_31, 'monthly')
        self.assertEqual(apr_next, date(2026, 4, 30))

    def test_idempotency_run_twice_creates_zero_new_invoices(self):
        """Running generate_invoices twice creates zero new invoices."""
        start_d = date(2026, 1, 1)
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_monthly,
            start_date=start_d,
            status=Subscription.Status.ACTIVE
        )
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.product,
            quantity=2,
            unit_price=Decimal('50.00')
        )

        # Run 1: as of Jan 15
        run1 = generate_invoices(as_of_date=date(2026, 1, 15))
        self.assertEqual(run1['created'], 1)
        self.assertEqual(Invoice.objects.filter(subscription=sub).count(), 1)

        # Run 2: same date or slightly later in same period -> ZERO new invoices
        run2 = generate_invoices(as_of_date=date(2026, 1, 15))
        self.assertEqual(run2['created'], 0)
        self.assertEqual(run2['skipped'], 1)
        self.assertEqual(Invoice.objects.filter(subscription=sub).count(), 1)

    def test_db_unique_constraint_subscription_period_start(self):
        """DB level unique constraint on (subscription, period_start) prevents duplicates."""
        start_d = date(2026, 1, 1)
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_monthly,
            start_date=start_d,
            status=Subscription.Status.ACTIVE
        )
        Invoice.objects.create(
            subscription=sub,
            customer=self.customer,
            period_start=start_d,
            period_end=date(2026, 1, 31),
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 1)
        )
        # Attempt duplicate insert with same subscription and period_start
        with self.assertRaises(IntegrityError):
            Invoice.objects.create(
                subscription=sub,
                customer=self.customer,
                period_start=start_d,
                period_end=date(2026, 1, 31),
                issue_date=date(2026, 1, 1),
                due_date=date(2026, 1, 1)
            )

    def test_catch_up_all_missed_past_periods_in_order(self):
        """Active subscription with 3 missed months creates 3 invoices in chronological order."""
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_monthly,
            start_date=date(2026, 1, 1),
            status=Subscription.Status.ACTIVE
        )
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.product,
            quantity=1,
            unit_price=Decimal('50.00')
        )

        # Billing as of March 15 (covers Jan 1, Feb 1, Mar 1)
        results = generate_invoices(as_of_date=date(2026, 3, 15))
        self.assertEqual(results['created'], 3)

        invoices = list(Invoice.objects.filter(subscription=sub).order_by('period_start'))
        self.assertEqual(len(invoices), 3)
        self.assertEqual(invoices[0].period_start, date(2026, 1, 1))
        self.assertEqual(invoices[1].period_start, date(2026, 2, 1))
        self.assertEqual(invoices[2].period_start, date(2026, 3, 1))

    def test_paused_closed_and_expired_subscriptions_are_not_billed(self):
        """Subscriptions that are paused, closed, or expired must NOT generate invoices."""
        # 1. Paused
        sub_paused = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_monthly,
            start_date=date(2026, 1, 1),
            status=Subscription.Status.PAUSED
        )
        SubscriptionLine.objects.create(subscription=sub_paused, product=self.product, quantity=1, unit_price=Decimal('50.00'))

        # 2. Closed
        sub_closed = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_monthly,
            start_date=date(2026, 1, 1),
            status=Subscription.Status.CLOSED
        )
        SubscriptionLine.objects.create(subscription=sub_closed, product=self.product, quantity=1, unit_price=Decimal('50.00'))

        # 3. Expired Active (expiry was in past)
        sub_expired = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_monthly,
            start_date=date(2026, 1, 1),
            expiry_date=date(2026, 1, 20),
            status=Subscription.Status.ACTIVE
        )
        SubscriptionLine.objects.create(subscription=sub_expired, product=self.product, quantity=1, unit_price=Decimal('50.00'))

        # Execute billing as of Feb 15
        results = generate_invoices(as_of_date=date(2026, 2, 15))
        self.assertEqual(results['created'], 0)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_tax_and_discount_calculations_on_invoice(self):
        """Verify automatic taxes and discounts applied properly."""
        discount = DiscountRule.objects.create(
            name="Promo 20%",
            discount_type=DiscountRule.DiscountType.PERCENTAGE,
            value=Decimal('20.00'),
            scope=DiscountRule.Scope.WHOLE_SUBSCRIPTION,
            usage_limit=1,
            times_used=0
        )

        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_monthly,
            start_date=date(2026, 1, 1),
            status=Subscription.Status.ACTIVE
        )
        # Line: 2x $100.00 = $200.00 with 10% tax
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.product,
            quantity=2,
            unit_price=Decimal('100.00'),
            tax=self.tax_10
        )

        results = generate_invoices(as_of_date=date(2026, 1, 10))
        self.assertEqual(results['created'], 1)

        inv = Invoice.objects.get(subscription=sub)
        # Subtotal = 200.00
        self.assertEqual(inv.subtotal, Decimal('200.00'))
        # Discount = 20% of 200 = 40.00
        self.assertEqual(inv.discount_total, Decimal('40.00'))
        # Grand total = (200 - 40) + tax = 160.00 + 20.00 = 180.00
        self.assertEqual(inv.grand_total, Decimal('180.00'))

        # Discount times_used incremented exactly once
        discount.refresh_from_db()
        self.assertEqual(discount.times_used, 1)


class InvoiceViewAndCustomerIsolationTests(TestCase):
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
        self.alice = User.objects.create_user(
            email='alice@customer.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.bob = User.objects.create_user(
            email='bob@customer.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(name="SaaS", price=Decimal('50.00'))
        self.sub_alice = Subscription.objects.create(
            customer=self.alice,
            plan=self.plan,
            status=Subscription.Status.ACTIVE
        )
        self.inv_alice = Invoice.objects.create(
            subscription=self.sub_alice,
            customer=self.alice,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            due_date=date(2026, 1, 1),
            grand_total=Decimal('50.00')
        )

    def test_customer_can_view_own_invoice(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse('invoicing:invoice_detail', kwargs={'pk': self.inv_alice.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.inv_alice.invoice_number)

    def test_customer_cannot_view_other_customer_invoice(self):
        self.client.force_login(self.bob)
        response = self.client.get(reverse('invoicing:invoice_detail', kwargs={'pk': self.inv_alice.pk}))
        self.assertEqual(response.status_code, 403)

    def test_staff_can_view_invoice_list_and_run_billing(self):
        self.client.force_login(self.staff)
        # View list
        response = self.client.get(reverse('invoicing:invoice_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.inv_alice.invoice_number)

        # Run billing via POST
        response = self.client.post(reverse('invoicing:run_billing'), {
            'as_of_date': '2026-02-01'
        }, follow=True)
        self.assertEqual(response.status_code, 200)
