from datetime import date, timedelta
from decimal import Decimal

from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from catalog.models import Plan, Product, Tax
from invoicing.models import Invoice, Payment
from invoicing.services import advance_period_date, generate_invoices
from subscriptions.models import DiscountRule, Subscription, SubscriptionLine


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
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_access_to_invoice_detail_and_print_redirects_to_login(self):
        """Unauthenticated requests to invoice detail and print must be redirected to login."""
        detail_url = reverse('invoicing:invoice_detail', kwargs={'pk': self.inv_alice.pk})
        resp_detail = self.client.get(detail_url)
        self.assertEqual(resp_detail.status_code, 302)
        self.assertIn(reverse('login'), resp_detail.url)

        print_url = reverse('invoicing:invoice_print', kwargs={'pk': self.inv_alice.pk})
        resp_print = self.client.get(print_url)
        self.assertEqual(resp_print.status_code, 302)
        self.assertIn(reverse('login'), resp_print.url)

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


class InvoiceStatusTransitionTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            email='transition_cust@example.com',
            password='Password@123',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(name="Pro Plan", price=Decimal('99.00'))
        self.sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.ACTIVE
        )
        self.invoice = Invoice.objects.create(
            subscription=self.sub,
            customer=self.customer,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            due_date=date(2026, 1, 15),
            status=Invoice.Status.DRAFT,
            grand_total=Decimal('99.00')
        )

    def test_valid_transitions_draft_to_confirmed_to_paid(self):
        """Enforces Draft -> Confirmed -> Paid progression."""
        self.assertEqual(self.invoice.status, Invoice.Status.DRAFT)

        # Draft -> Confirmed
        self.invoice.transition_to(Invoice.Status.CONFIRMED)
        self.assertEqual(self.invoice.status, Invoice.Status.CONFIRMED)

        # Confirmed -> Paid
        self.invoice.transition_to(Invoice.Status.PAID)
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

    def test_cancel_draft_and_confirmed(self):
        """Draft and Confirmed invoices can be cancelled."""
        inv1 = Invoice.objects.create(
            subscription=self.sub,
            customer=self.customer,
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            due_date=date(2026, 2, 15),
            status=Invoice.Status.DRAFT
        )
        inv1.transition_to(Invoice.Status.CANCELLED)
        self.assertEqual(inv1.status, Invoice.Status.CANCELLED)

        inv2 = Invoice.objects.create(
            subscription=self.sub,
            customer=self.customer,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            due_date=date(2026, 3, 15),
            status=Invoice.Status.CONFIRMED
        )
        inv2.transition_to(Invoice.Status.CANCELLED)
        self.assertEqual(inv2.status, Invoice.Status.CANCELLED)

    def test_cannot_cancel_paid_invoice(self):
        """A Paid invoice cannot be cancelled."""
        self.invoice.status = Invoice.Status.PAID
        self.invoice.save()

        with self.assertRaises(ValidationError) as ctx:
            self.invoice.transition_to(Invoice.Status.CANCELLED)
        self.assertIn("Paid invoice cannot be", str(ctx.exception))

    def test_cannot_reopen_cancelled_invoice(self):
        """A Cancelled invoice cannot be reopened."""
        self.invoice.status = Invoice.Status.CANCELLED
        self.invoice.save()

        with self.assertRaises(ValidationError) as ctx:
            self.invoice.transition_to(Invoice.Status.DRAFT)
        self.assertIn("Cancelled invoice cannot be reopened", str(ctx.exception))

    def test_cannot_jump_draft_to_paid_directly(self):
        """Draft must be confirmed before becoming Paid."""
        with self.assertRaises(ValidationError) as ctx:
            self.invoice.transition_to(Invoice.Status.PAID)
        self.assertIn("Draft invoice must be Confirmed before being marked Paid", str(ctx.exception))

    def test_invoice_mutation_views_reject_get_with_405(self):
        """GET request to invoice transition, send, payment create, or run billing must return 405."""
        staff_user = User.objects.create_user(
            email='staff_tester@brightloop.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.client.force_login(staff_user)

        res_trans = self.client.get(reverse('invoicing:invoice_transition', kwargs={'pk': self.invoice.pk}))
        self.assertEqual(res_trans.status_code, 405)

        res_send = self.client.get(reverse('invoicing:invoice_send', kwargs={'pk': self.invoice.pk}))
        self.assertEqual(res_send.status_code, 405)

        res_pay = self.client.get(reverse('invoicing:payment_create', kwargs={'pk': self.invoice.pk}))
        self.assertEqual(res_pay.status_code, 405)

        res_bill = self.client.get(reverse('invoicing:run_billing'))
        self.assertEqual(res_bill.status_code, 405)



class InvoiceSendAndPrintTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='admin_inv@example.com',
            password='AdminPassword@1'
        )
        self.customer = User.objects.create_user(
            email='client@example.com',
            first_name='John',
            last_name='Doe',
            password='Password@123',
            role=User.Role.CUSTOMER
        )
        self.other_customer = User.objects.create_user(
            email='other@example.com',
            password='Password@123',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(name="Standard", price=Decimal('40.00'))
        self.sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.ACTIVE
        )
        self.invoice = Invoice.objects.create(
            subscription=self.sub,
            customer=self.customer,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            due_date=date(2026, 1, 15),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('40.00')
        )

    def test_staff_can_send_invoice_and_records_sent_date(self):
        """Sending invoice fires console email backend and records sent_at."""
        self.client.force_login(self.admin)
        self.assertIsNone(self.invoice.sent_at)
        mail.outbox.clear()

        response = self.client.post(reverse('invoicing:invoice_send', kwargs={'pk': self.invoice.pk}), follow=True)
        self.assertEqual(response.status_code, 200)

        self.invoice.refresh_from_db()
        self.assertIsNotNone(self.invoice.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.invoice.invoice_number, mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])

    def test_customer_cannot_trigger_send_invoice(self):
        """Customer cannot POST to send endpoint (forbidden / staff required)."""
        self.client.force_login(self.customer)
        response = self.client.post(reverse('invoicing:invoice_send', kwargs={'pk': self.invoice.pk}))
        self.assertEqual(response.status_code, 403)

    def test_print_view_accessible_by_owner_and_staff_blocked_for_others(self):
        """Print page displays cleanly for owner and staff, but forbidden for 3rd party."""
        # 1. Staff access
        self.client.force_login(self.admin)
        res_staff = self.client.get(reverse('invoicing:invoice_print', kwargs={'pk': self.invoice.pk}))
        self.assertEqual(res_staff.status_code, 200)
        self.assertContains(res_staff, self.invoice.invoice_number)
        self.assertContains(res_staff, "Brightloop Software")

        # 2. Invoice Customer owner access
        self.client.force_login(self.customer)
        res_owner = self.client.get(reverse('invoicing:invoice_print', kwargs={'pk': self.invoice.pk}))
        self.assertEqual(res_owner.status_code, 200)

        # 3. Another customer blocked (returns 404 per customer query scoping)
        self.client.force_login(self.other_customer)
        res_other = self.client.get(reverse('invoicing:invoice_print', kwargs={'pk': self.invoice.pk}))
        self.assertEqual(res_other.status_code, 404)


class InvoiceListFilterAndSearchTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='finance_staff@example.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.customer1 = User.objects.create_user(
            email='cust1@example.com',
            password='Password@123',
            role=User.Role.CUSTOMER
        )
        self.customer2 = User.objects.create_user(
            email='cust2@example.com',
            password='Password@123',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(name="Basic", price=Decimal('20.00'))
        self.sub1 = Subscription.objects.create(customer=self.customer1, plan=self.plan, status=Subscription.Status.ACTIVE)
        self.sub2 = Subscription.objects.create(customer=self.customer2, plan=self.plan, status=Subscription.Status.ACTIVE)

        # Invoice 1: Jan 10, Confirmed, Due Jan 20 (past -> overdue if not paid)
        self.inv1 = Invoice.objects.create(
            subscription=self.sub1,
            customer=self.customer1,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            issue_date=date(2026, 1, 10),
            due_date=date(2026, 1, 20),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00')
        )
        # Invoice 2: Feb 10, Draft, Due Feb 25 (future)
        self.inv2 = Invoice.objects.create(
            subscription=self.sub2,
            customer=self.customer2,
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            issue_date=date(2026, 2, 10),
            due_date=date(2026, 12, 31),
            status=Invoice.Status.DRAFT,
            grand_total=Decimal('200.00')
        )

    def test_filter_by_status(self):
        self.client.force_login(self.staff)
        res = self.client.get(reverse('invoicing:invoice_list'), {'status': 'draft'})
        self.assertContains(res, self.inv2.invoice_number)
        self.assertNotContains(res, self.inv1.invoice_number)

    def test_filter_by_customer(self):
        self.client.force_login(self.staff)
        res = self.client.get(reverse('invoicing:invoice_list'), {'customer': self.customer1.pk})
        self.assertContains(res, self.inv1.invoice_number)
        self.assertNotContains(res, self.inv2.invoice_number)

    def test_filter_by_date_range(self):
        self.client.force_login(self.staff)
        # Filter Jan only
        res = self.client.get(reverse('invoicing:invoice_list'), {
            'start_date': '2026-01-01',
            'end_date': '2026-01-31'
        })
        self.assertContains(res, self.inv1.invoice_number)
        self.assertNotContains(res, self.inv2.invoice_number)

    def test_filter_by_overdue(self):
        self.client.force_login(self.staff)
        res = self.client.get(reverse('invoicing:invoice_list'), {'overdue': 'yes'})
        self.assertContains(res, self.inv1.invoice_number)
        self.assertNotContains(res, self.inv2.invoice_number)

    def test_search_by_invoice_number(self):
        self.client.force_login(self.staff)
        res = self.client.get(reverse('invoicing:invoice_list'), {'q': self.inv1.invoice_number})
        self.assertContains(res, self.inv1.invoice_number)
        self.assertNotContains(res, self.inv2.invoice_number)

    def test_customer_forbidden_from_invoice_list(self):
        self.client.force_login(self.customer1)
        res = self.client.get(reverse('invoicing:invoice_list'))
        self.assertEqual(res.status_code, 403)


class InvoicePaymentAndReconciliationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='admin_pay@example.com',
            password='AdminPassword@1'
        )
        self.customer = User.objects.create_user(
            email='pay_customer@example.com',
            password='Password@123',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(name="Enterprise", price=Decimal('100.00'))
        self.sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.ACTIVE
        )
        self.invoice = Invoice.objects.create(
            subscription=self.sub,
            customer=self.customer,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            due_date=date(2026, 1, 15),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00')
        )

    def test_partial_payment_reduces_balance(self):
        """Recording a partial payment updates amount_paid and balance_due."""
        self.client.force_login(self.admin)
        res = self.client.post(reverse('invoicing:payment_create', kwargs={'pk': self.invoice.pk}), {
            'amount': '40.00',
            'date': '2026-01-12',
            'method': Payment.Method.BANK_TRANSFER,
            'note': 'Initial installment'
        }, follow=True)
        self.assertEqual(res.status_code, 200)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('40.00'))
        self.assertEqual(self.invoice.amount_due, Decimal('60.00'))
        self.assertEqual(self.invoice.status, Invoice.Status.CONFIRMED)

    def test_full_payment_automatically_marks_invoice_paid(self):
        """Full payment transitions invoice status to Paid."""
        self.client.force_login(self.admin)
        res = self.client.post(reverse('invoicing:payment_create', kwargs={'pk': self.invoice.pk}), {
            'amount': '100.00',
            'date': '2026-01-14',
            'method': Payment.Method.CARD,
            'note': 'Full payment'
        }, follow=True)
        self.assertEqual(res.status_code, 200)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('100.00'))
        self.assertEqual(self.invoice.amount_due, Decimal('0.00'))
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

    def test_partial_then_full_payment_flips_to_paid(self):
        """Sequential payments: partial ($40) leaves Confirmed, remaining ($60) flips to Paid."""
        self.client.force_login(self.admin)

        # Payment 1: $40.00
        self.client.post(reverse('invoicing:payment_create', kwargs={'pk': self.invoice.pk}), {
            'amount': '40.00',
            'date': '2026-01-10',
            'method': Payment.Method.UPI,
            'note': 'Part 1'
        })
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('40.00'))
        self.assertEqual(self.invoice.amount_due, Decimal('60.00'))
        self.assertEqual(self.invoice.status, Invoice.Status.CONFIRMED)

        # Payment 2: $60.00
        self.client.post(reverse('invoicing:payment_create', kwargs={'pk': self.invoice.pk}), {
            'amount': '60.00',
            'date': '2026-01-12',
            'method': Payment.Method.CHEQUE,
            'note': 'Part 2 settlement'
        })
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('100.00'))
        self.assertEqual(self.invoice.amount_due, Decimal('0.00'))
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

    def test_overpayment_rejected(self):
        """Attempting to pay more than balance due is rejected."""
        self.client.force_login(self.admin)
        res = self.client.post(reverse('invoicing:payment_create', kwargs={'pk': self.invoice.pk}), {
            'amount': '150.00',
            'date': '2026-01-10',
            'method': Payment.Method.CARD,
        }, follow=True)
        self.assertContains(res, "Overpayment not allowed")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('0.00'))

    def test_zero_and_negative_payment_rejected(self):
        """Negative and zero payment amounts are rejected."""
        self.client.force_login(self.admin)
        # Zero
        res_zero = self.client.post(reverse('invoicing:payment_create', kwargs={'pk': self.invoice.pk}), {
            'amount': '0.00',
            'date': '2026-01-10',
            'method': Payment.Method.CASH,
        }, follow=True)
        self.assertContains(res_zero, "Payment amount must be greater than zero")

        # Negative
        res_neg = self.client.post(reverse('invoicing:payment_create', kwargs={'pk': self.invoice.pk}), {
            'amount': '-25.00',
            'date': '2026-01-10',
            'method': Payment.Method.CASH,
        }, follow=True)
        self.assertContains(res_neg, "Payment amount must be greater than zero")

    def test_payments_only_on_confirmed_invoices(self):
        """Payments can only be recorded on Confirmed invoices (Draft, Paid, Cancelled rejected)."""
        self.client.force_login(self.admin)

        # 1. Draft invoice
        inv_draft = Invoice.objects.create(
            subscription=self.sub,
            customer=self.customer,
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            due_date=date(2026, 2, 15),
            status=Invoice.Status.DRAFT,
            grand_total=Decimal('50.00')
        )
        res_draft = self.client.post(reverse('invoicing:payment_create', kwargs={'pk': inv_draft.pk}), {
            'amount': '50.00',
            'date': '2026-02-10',
            'method': Payment.Method.CARD,
        }, follow=True)
        self.assertContains(res_draft, "Payments can only be recorded on Confirmed invoices")
        self.assertEqual(inv_draft.payments.count(), 0)

        # 2. Cancelled invoice
        inv_cancelled = Invoice.objects.create(
            subscription=self.sub,
            customer=self.customer,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            due_date=date(2026, 3, 15),
            status=Invoice.Status.CANCELLED,
            grand_total=Decimal('50.00')
        )
        res_cancelled = self.client.post(reverse('invoicing:payment_create', kwargs={'pk': inv_cancelled.pk}), {
            'amount': '50.00',
            'date': '2026-03-10',
            'method': Payment.Method.CARD,
        }, follow=True)
        self.assertContains(res_cancelled, "Payments can only be recorded on Confirmed invoices")


class OverdueAndStatusFilterTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='auditor@example.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.customer = User.objects.create_user(
            email='debtor@example.com',
            password='Password@123',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(name="Tier 1", price=Decimal('100.00'))
        self.sub = Subscription.objects.create(customer=self.customer, plan=self.plan, status=Subscription.Status.ACTIVE)

        today = date.today()
        # 1. Overdue: due 10 days ago, Confirmed, balance $100
        self.inv_overdue = Invoice.objects.create(
            subscription=self.sub,
            customer=self.customer,
            period_start=today - timedelta(days=40),
            period_end=today - timedelta(days=10),
            issue_date=today - timedelta(days=20),
            due_date=today - timedelta(days=10),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00')
        )

        # 2. Outstanding but not overdue: due in 10 days, Confirmed, balance $100
        self.inv_outstanding = Invoice.objects.create(
            subscription=self.sub,
            customer=self.customer,
            period_start=today,
            period_end=today + timedelta(days=30),
            issue_date=today,
            due_date=today + timedelta(days=10),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00')
        )

        # 3. Paid (due date was past, but fully paid): not overdue
        self.inv_paid = Invoice.objects.create(
            subscription=self.sub,
            customer=self.customer,
            period_start=today - timedelta(days=60),
            period_end=today - timedelta(days=30),
            issue_date=today - timedelta(days=40),
            due_date=today - timedelta(days=30),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('80.00')
        )
        Payment.objects.create(
            invoice=self.inv_paid,
            amount=Decimal('80.00'),
            date=today - timedelta(days=35),
            method=Payment.Method.BANK_TRANSFER,
            recorded_by=self.staff
        )
        self.inv_paid.refresh_from_db()

    def test_overdue_logic_and_days_overdue(self):
        """Verifies is_overdue and days_overdue property logic."""
        # Unpaid past due invoice IS overdue
        self.assertTrue(self.inv_overdue.is_overdue)
        self.assertEqual(self.inv_overdue.days_overdue, 10)

        # Future due invoice is NOT overdue
        self.assertFalse(self.inv_outstanding.is_overdue)
        self.assertEqual(self.inv_outstanding.days_overdue, 0)

        # Fully paid invoice past due date is NOT overdue
        self.assertEqual(self.inv_paid.status, Invoice.Status.PAID)
        self.assertFalse(self.inv_paid.is_overdue)
        self.assertEqual(self.inv_paid.days_overdue, 0)

    def test_overdue_invoices_view(self):
        """Dedicated /invoicing/overdue/ view lists overdue invoices and calculates debt aggregate."""
        self.client.force_login(self.staff)
        res = self.client.get(reverse('invoicing:overdue_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.inv_overdue.invoice_number)
        self.assertContains(res, "10 days")
        self.assertNotContains(res, self.inv_outstanding.invoice_number)
        self.assertNotContains(res, self.inv_paid.invoice_number)

    def test_invoice_list_status_filters_paid_outstanding_overdue(self):
        """Tests status filter options: Paid, Outstanding, Overdue."""
        self.client.force_login(self.staff)

        # 1. Filter: Paid
        res_paid = self.client.get(reverse('invoicing:invoice_list'), {'status': 'paid'})
        self.assertContains(res_paid, self.inv_paid.invoice_number)
        self.assertNotContains(res_paid, self.inv_overdue.invoice_number)
        self.assertNotContains(res_paid, self.inv_outstanding.invoice_number)

        # 2. Filter: Outstanding (unpaid balance)
        res_out = self.client.get(reverse('invoicing:invoice_list'), {'status': 'outstanding'})
        self.assertContains(res_out, self.inv_overdue.invoice_number)
        self.assertContains(res_out, self.inv_outstanding.invoice_number)
        self.assertNotContains(res_out, self.inv_paid.invoice_number)

        # 3. Filter: Overdue
        res_over = self.client.get(reverse('invoicing:invoice_list'), {'status': 'overdue'})
        self.assertContains(res_over, self.inv_overdue.invoice_number)
        self.assertNotContains(res_over, self.inv_outstanding.invoice_number)
        self.assertNotContains(res_over, self.inv_paid.invoice_number)

    def test_sql_aggregate_prevents_n_plus_one(self):
        """Verifies that accessing amount_paid/due on with_payment_totals() uses SQL annotations without N+1."""
        invoices = list(Invoice.objects.with_payment_totals().filter(pk__in=[self.inv_overdue.pk, self.inv_paid.pk]))
        with self.assertNumQueries(0):
            for inv in invoices:
                _ = inv.amount_paid
                _ = inv.amount_due


class PaymentListAndPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='cashier@example.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.alice = User.objects.create_user(
            email='alice_pay@example.com',
            password='Password@123',
            role=User.Role.CUSTOMER
        )
        self.bob = User.objects.create_user(
            email='bob_pay@example.com',
            password='Password@123',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(name="Standard", price=Decimal('50.00'))
        self.sub_a = Subscription.objects.create(customer=self.alice, plan=self.plan, status=Subscription.Status.ACTIVE)
        self.sub_b = Subscription.objects.create(customer=self.bob, plan=self.plan, status=Subscription.Status.ACTIVE)

        self.inv_a = Invoice.objects.create(
            subscription=self.sub_a, customer=self.alice,
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            due_date=date(2026, 1, 15), status=Invoice.Status.CONFIRMED, grand_total=Decimal('50.00')
        )
        self.inv_b = Invoice.objects.create(
            subscription=self.sub_b, customer=self.bob,
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            due_date=date(2026, 1, 15), status=Invoice.Status.CONFIRMED, grand_total=Decimal('50.00')
        )

        self.pay_a = Payment.objects.create(
            invoice=self.inv_a,
            amount=Decimal('50.00'),
            date=date(2026, 1, 10),
            method=Payment.Method.UPI,
            note='Alice UPI payment',
            recorded_by=self.staff
        )
        self.pay_b = Payment.objects.create(
            invoice=self.inv_b,
            amount=Decimal('50.00'),
            date=date(2026, 2, 10),
            method=Payment.Method.BANK_TRANSFER,
            note='Bob Wire Transfer',
            recorded_by=self.staff
        )

    def test_unauthenticated_user_redirected_to_login_on_payment_list(self):
        """Unauthenticated request to payment list must be redirected to login."""
        res = self.client.get(reverse('invoicing:payment_list'))
        self.assertEqual(res.status_code, 302)
        self.assertIn(reverse('login'), res.url)

    def test_customer_sees_only_own_payments(self):
        """Alice sees only her payment; cannot see Bob's."""
        self.client.force_login(self.alice)
        res = self.client.get(reverse('invoicing:payment_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Alice UPI payment')
        self.assertNotContains(res, 'Bob Wire Transfer')

    def test_staff_sees_all_payments_and_can_filter_by_method_and_date(self):
        """Staff sees all payments and can filter by method and date range."""
        self.client.force_login(self.staff)
        # All
        res = self.client.get(reverse('invoicing:payment_list'))
        self.assertContains(res, 'Alice UPI payment')
        self.assertContains(res, 'Bob Wire Transfer')

        # Filter method: UPI
        res_upi = self.client.get(reverse('invoicing:payment_list'), {'method': 'upi'})
        self.assertContains(res_upi, 'Alice UPI payment')
        self.assertNotContains(res_upi, 'Bob Wire Transfer')

        # Filter date range: January only
        res_date = self.client.get(reverse('invoicing:payment_list'), {
            'start_date': '2026-01-01',
            'end_date': '2026-01-31'
        })
        self.assertContains(res_date, 'Alice UPI payment')
        self.assertNotContains(res_date, 'Bob Wire Transfer')


class FinanceDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='cfo@brightloop.com',
            password='AdminPassword@1'
        )
        self.staff = User.objects.create_user(
            email='finance_analyst@brightloop.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.cust_a = User.objects.create_user(
            email='customer_a@example.com',
            first_name='Customer',
            last_name='A',
            password='Password@123',
            role=User.Role.CUSTOMER
        )
        self.cust_b = User.objects.create_user(
            email='customer_b@example.com',
            first_name='Customer',
            last_name='B',
            password='Password@123',
            role=User.Role.CUSTOMER
        )

        # Plans
        self.plan_monthly = Plan.objects.create(
            name="Pro Monthly",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY
        )
        self.plan_yearly = Plan.objects.create(
            name="Enterprise Yearly",
            price=Decimal('2400.00'),
            billing_period=Plan.BillingPeriod.YEARLY
        )

        today = date.today()

        # Subscriptions
        # Sub 1: Active Monthly ($100 MRR)
        self.sub1 = Subscription.objects.create(
            customer=self.cust_a,
            plan=self.plan_monthly,
            status=Subscription.Status.ACTIVE,
            start_date=today - timedelta(days=60)
        )
        # Sub 2: Active Yearly ($2400 / 12 = $200 MRR)
        self.sub2 = Subscription.objects.create(
            customer=self.cust_b,
            plan=self.plan_yearly,
            status=Subscription.Status.ACTIVE,
            start_date=today - timedelta(days=30)
        )
        # Sub 3: Paused Monthly (should not contribute to active count or MRR)
        self.sub3 = Subscription.objects.create(
            customer=self.cust_a,
            plan=self.plan_monthly,
            status=Subscription.Status.PAUSED,
            start_date=today - timedelta(days=40)
        )
        # Sub 4: Closed Yearly (should not contribute to active count or MRR)
        self.sub4 = Subscription.objects.create(
            customer=self.cust_b,
            plan=self.plan_yearly,
            status=Subscription.Status.CLOSED,
            start_date=today - timedelta(days=90)
        )

        # Invoices
        # Inv 1: Cust A, due 15 days ago (Overdue), Total $100, Paid $40, Due $60
        self.inv1 = Invoice.objects.create(
            subscription=self.sub1,
            customer=self.cust_a,
            period_start=today - timedelta(days=45),
            period_end=today - timedelta(days=15),
            issue_date=today - timedelta(days=25),
            due_date=today - timedelta(days=15),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00')
        )
        self.pay1 = Payment.objects.create(
            invoice=self.inv1,
            amount=Decimal('40.00'),
            date=today - timedelta(days=20),
            method=Payment.Method.BANK_TRANSFER,
            recorded_by=self.staff
        )

        # Inv 2: Cust B, due in 10 days (Not overdue), Total $200, Paid $50, Due $150
        self.inv2 = Invoice.objects.create(
            subscription=self.sub2,
            customer=self.cust_b,
            period_start=today,
            period_end=today + timedelta(days=365),
            issue_date=today,
            due_date=today + timedelta(days=10),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('200.00')
        )
        self.pay2 = Payment.objects.create(
            invoice=self.inv2,
            amount=Decimal('50.00'),
            date=today,
            method=Payment.Method.CARD,
            recorded_by=self.staff
        )

        # Inv 3: Cust A, due 5 days ago, Total $80, Paid in full ($80) -> status Paid (Not overdue)
        self.inv3 = Invoice.objects.create(
            subscription=self.sub1,
            customer=self.cust_a,
            period_start=today - timedelta(days=75),
            period_end=today - timedelta(days=45),
            issue_date=today - timedelta(days=55),
            due_date=today - timedelta(days=5),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('80.00')
        )
        self.pay3 = Payment.objects.create(
            invoice=self.inv3,
            amount=Decimal('80.00'),
            date=today - timedelta(days=10),
            method=Payment.Method.UPI,
            recorded_by=self.staff
        )

        # Inv 4: Cancelled, Total $50 (should not be in amount owed)
        self.inv4 = Invoice.objects.create(
            subscription=self.sub2,
            customer=self.cust_b,
            period_start=today - timedelta(days=100),
            period_end=today - timedelta(days=70),
            issue_date=today - timedelta(days=80),
            due_date=today - timedelta(days=60),
            status=Invoice.Status.CANCELLED,
            grand_total=Decimal('50.00')
        )

    def test_dashboard_cards_match_known_seeded_data(self):
        """Validates all 5 finance dashboard metric cards against exact seeded totals."""
        self.client.force_login(self.staff)
        res = self.client.get(reverse('invoicing:finance_dashboard'))
        self.assertEqual(res.status_code, 200)

        ctx = res.context
        # 1. Active Subscriptions: sub1 + sub2 = 2
        self.assertEqual(ctx['active_subscriptions_count'], 2)

        # 2. Revenue Received: $40 + $50 + $80 = $170.00
        self.assertEqual(ctx['revenue_received'], Decimal('170.00'))

        # 3. Amount Still Owed: $60 (Inv 1) + $150 (Inv 2) = $210.00
        self.assertEqual(ctx['amount_still_owed'], Decimal('210.00'))

        # 4. Overdue Invoices Count: Only Inv 1 is past due and unpaid = 1
        self.assertEqual(ctx['overdue_invoice_count'], 1)

        # 5. Monthly Recurring Revenue (MRR): $100.00 (sub1 monthly) + $200.00 (sub2 $2400/12 yearly) = $300.00
        self.assertEqual(ctx['monthly_recurring_revenue'], Decimal('300.00'))

        # Check rendered HTML metrics
        self.assertContains(res, "$170.00")
        self.assertContains(res, "$210.00")
        self.assertContains(res, "$300.00")

    def test_dashboard_filter_by_customer(self):
        """Verifies filtering by Customer A scopes all metrics appropriately."""
        self.client.force_login(self.staff)
        res = self.client.get(reverse('invoicing:finance_dashboard'), {'customer': self.cust_a.pk})
        self.assertEqual(res.status_code, 200)

        ctx = res.context
        # Customer A has 1 active sub (sub1)
        self.assertEqual(ctx['active_subscriptions_count'], 1)
        # Customer A paid $40 + $80 = $120.00
        self.assertEqual(ctx['revenue_received'], Decimal('120.00'))
        # Customer A owes $60.00
        self.assertEqual(ctx['amount_still_owed'], Decimal('60.00'))
        # Customer A overdue count = 1
        self.assertEqual(ctx['overdue_invoice_count'], 1)
        # Customer A MRR = $100.00
        self.assertEqual(ctx['monthly_recurring_revenue'], Decimal('100.00'))

    def test_dashboard_filter_by_plan(self):
        """Verifies filtering by Enterprise Yearly plan scopes all metrics."""
        self.client.force_login(self.staff)
        res = self.client.get(reverse('invoicing:finance_dashboard'), {'plan': self.plan_yearly.pk})
        self.assertEqual(res.status_code, 200)

        ctx = res.context
        # Enterprise Yearly has 1 active sub (sub2)
        self.assertEqual(ctx['active_subscriptions_count'], 1)
        # Revenue on Enterprise Yearly = $50.00
        self.assertEqual(ctx['revenue_received'], Decimal('50.00'))
        # Amount owed on Enterprise Yearly = $150.00
        self.assertEqual(ctx['amount_still_owed'], Decimal('150.00'))
        # Overdue count on Enterprise Yearly = 0
        self.assertEqual(ctx['overdue_invoice_count'], 0)
        # MRR on Enterprise Yearly = $200.00
        self.assertEqual(ctx['monthly_recurring_revenue'], Decimal('200.00'))

    def test_dashboard_local_chart_js_offline_compliance(self):
        """Verifies chart library is loaded locally from static files (no CDN)."""
        self.client.force_login(self.staff)
        res = self.client.get(reverse('invoicing:finance_dashboard'))
        self.assertEqual(res.status_code, 200)
        # Local static script
        self.assertContains(res, 'src="/static/js/chart.umd.min.js"')
        # Canvas elements
        self.assertContains(res, '<canvas id="revenueChart"></canvas>')
        self.assertContains(res, '<canvas id="planChart"></canvas>')
        # Assert no CDN scripts exist
        content = res.content.decode('utf-8')
        self.assertNotIn('cdn.jsdelivr', content)
        self.assertNotIn('cdnjs.cloudflare', content)
        self.assertNotIn('unpkg.com', content)

    def test_customer_forbidden_from_finance_dashboard(self):
        """Customer is blocked from accessing the internal finance dashboard."""
        self.client.force_login(self.cust_a)
        res = self.client.get(reverse('invoicing:finance_dashboard'))
        self.assertEqual(res.status_code, 403)

        # Navigating to '/' redirects customer to their customer portal
        res_home = self.client.get(reverse('home'))
        self.assertRedirects(res_home, reverse('customer_portal'))



