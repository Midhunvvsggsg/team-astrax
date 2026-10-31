from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from automation.services import execute_daily_jobs
from catalog.models import Plan, Product, Tax
from invoicing.models import BankStatement, BankStatementRow, Invoice, MonthlyReport, Payment
from invoicing.reconciliation import (
    parse_statement_csv,
)
from invoicing.reports import (
    calculate_monthly_metrics,
    generate_monthly_report,
)
from invoicing.services import advance_period_date, generate_invoices
from subscriptions.models import DiscountRule, Subscription, SubscriptionLine, SubscriptionStatusHistory


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

    def test_cancelled_invoice_amount_due_is_zero(self):
        """Cancelled invoice must always report zero amount due in Python property and SQL annotation."""
        self.invoice.transition_to(Invoice.Status.CANCELLED)
        self.assertEqual(self.invoice.amount_due, Decimal('0.00'))

        annotated_inv = Invoice.objects.with_payment_totals().get(pk=self.invoice.pk)
        self.assertEqual(annotated_inv.annotated_amount_due, Decimal('0.00'))

    def test_cannot_cancel_confirmed_invoice_with_payments(self):
        """Confirmed invoice with existing recorded payments cannot be cancelled."""
        self.invoice.status = Invoice.Status.CONFIRMED
        self.invoice.save()

        Payment.objects.create(
            invoice=self.invoice,
            amount=Decimal('10.00'),
            date=date.today(),
            method=Payment.Method.CARD
        )
        self.invoice.refresh_from_db()
        with self.assertRaises(ValidationError) as ctx:
            self.invoice.transition_to(Invoice.Status.CANCELLED)
        self.assertIn("Cannot cancel an invoice with recorded payments", str(ctx.exception))

    def test_mark_as_paid_transition_creates_settlement_payment(self):
        """Staff marking a confirmed invoice as Paid records a settlement payment for the balance."""
        self.invoice.status = Invoice.Status.CONFIRMED
        self.invoice.grand_total = Decimal('100.00')
        self.invoice.save()

        staff_user = User.objects.create_user(
            email='cashier_staff@brightloop.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.client.force_login(staff_user)

        res = self.client.post(reverse('invoicing:invoice_transition', kwargs={'pk': self.invoice.pk}), {
            'new_status': Invoice.Status.PAID
        }, follow=True)
        self.assertEqual(res.status_code, 200)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(self.invoice.amount_paid, Decimal('100.00'))
        self.assertEqual(self.invoice.amount_due, Decimal('0.00'))
        self.assertEqual(self.invoice.payments.count(), 1)
        self.assertEqual(self.invoice.payments.first().recorded_by, staff_user)




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


class BankStatementReconciliationTests(TestCase):
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
            email='acme@corp.com',
            first_name='Acme',
            last_name='Corp',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.other_customer = User.objects.create_user(
            email='beta@solutions.com',
            first_name='Beta',
            last_name='Solutions',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )

        self.plan = Plan.objects.create(
            name="Pro Plan",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY
        )
        self.product = Product.objects.create(name="Seat", sales_price=Decimal('100.00'))

        self.sub1 = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            start_date=date(2026, 1, 1),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(subscription=self.sub1, product=self.product, quantity=1, unit_price=Decimal('100.00'))

        # Create confirmed invoice 1 for Acme Corp
        self.inv1 = Invoice.objects.create(
            customer=self.customer,
            subscription=self.sub1,
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 15),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00'),
        )
        # Create confirmed invoice 2 for Beta Solutions
        self.sub2 = Subscription.objects.create(
            customer=self.other_customer,
            plan=self.plan,
            start_date=date(2026, 1, 1),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(subscription=self.sub2, product=self.product, quantity=1, unit_price=Decimal('100.00'))
        self.inv2 = Invoice.objects.create(
            customer=self.other_customer,
            subscription=self.sub2,
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 15),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00'),
        )

        self.client = Client()

    def test_exact_match_auto_reconciliation(self):
        """
        Row matching exact invoice number and exact amount is auto-reconciled (High Confidence).
        Payment is automatically recorded and invoice transitions to Paid.
        """
        csv_data = (
            f"date,description,amount,reference\n"
            f"2026-01-10,Wire settlement from Acme,100.00,{self.inv1.invoice_number} Acme Corp\n"
        )
        uploaded = SimpleUploadedFile("statement.csv", csv_data.encode('utf-8'), content_type="text/csv")

        self.client.force_login(self.staff)
        resp = self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': uploaded}, follow=True)
        self.assertEqual(resp.status_code, 200)

        statement = BankStatement.objects.latest('id')
        self.assertEqual(statement.total_rows, 1)
        self.assertEqual(statement.auto_reconciled_count, 1)
        self.assertEqual(statement.needs_review_count, 0)
        self.assertEqual(statement.unmatched_count, 0)

        row = statement.rows.first()
        self.assertEqual(row.status, BankStatementRow.Status.AUTO_RECONCILED)
        self.assertEqual(row.confidence, BankStatementRow.Confidence.HIGH)
        self.assertIsNotNone(row.payment)
        self.assertEqual(row.payment.amount, Decimal('100.00'))

        self.inv1.refresh_from_db()
        self.assertEqual(self.inv1.status, Invoice.Status.PAID)
        self.assertEqual(self.inv1.amount_due, Decimal('0.00'))

    def test_partial_payment_medium_confidence_and_one_click_approval(self):
        """
        Row matching invoice number but with partial amount without name confirmation
        is placed in review (Medium Confidence). One-click approval records partial payment.
        """
        csv_data = (
            f"date,description,amount,reference\n"
            f"2026-01-12,Direct Deposit Part Payment,40.00,{self.inv2.invoice_number}\n"
        )
        uploaded = SimpleUploadedFile("statement.csv", csv_data.encode('utf-8'), content_type="text/csv")

        self.client.force_login(self.staff)
        self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': uploaded})

        statement = BankStatement.objects.latest('id')
        self.assertEqual(statement.auto_reconciled_count, 0)
        self.assertEqual(statement.needs_review_count, 1)

        row = statement.rows.first()
        self.assertEqual(row.status, BankStatementRow.Status.NEEDS_REVIEW)
        self.assertEqual(row.confidence, BankStatementRow.Confidence.MEDIUM)
        self.assertIsNone(row.payment)

        # Staff approves via one-click review button
        approve_url = reverse('invoicing:reconciliation_row_approve', kwargs={'row_pk': row.pk})
        resp_approve = self.client.post(approve_url, follow=True)
        self.assertEqual(resp_approve.status_code, 200)

        row.refresh_from_db()
        self.assertEqual(row.status, BankStatementRow.Status.MANUALLY_RECONCILED)
        self.assertIsNotNone(row.payment)
        self.assertEqual(row.payment.amount, Decimal('40.00'))

        self.inv2.refresh_from_db()
        # Invoice remains Confirmed because only partial payment recorded ($40 of $100)
        self.assertEqual(self.inv2.status, Invoice.Status.CONFIRMED)
        self.assertEqual(self.inv2.amount_due, Decimal('60.00'))

    def test_customer_name_similarity_without_invoice_number(self):
        """
        Row without invoice number matches by customer name similarity and exact amount.
        """
        csv_data = (
            "date,description,amount,reference\n"
            "2026-01-14,Subscription Settlement,100.00,Acme Corp monthly direct wire\n"
        )
        uploaded = SimpleUploadedFile("statement.csv", csv_data.encode('utf-8'), content_type="text/csv")

        self.client.force_login(self.staff)
        self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': uploaded})

        statement = BankStatement.objects.latest('id')
        row = statement.rows.first()
        self.assertEqual(row.matched_invoice, self.inv1)
        self.assertIn(row.status, [BankStatementRow.Status.AUTO_RECONCILED, BankStatementRow.Status.NEEDS_REVIEW])

    def test_ambiguous_matching_queued_for_review(self):
        """
        When multiple candidate invoices exist for the same customer with equal amounts,
        the system marks the row as Medium Confidence (Needs Review) to prevent incorrect auto-application.
        """
        # Create a second confirmed invoice for Acme Corp with same amount
        Invoice.objects.create(
            customer=self.customer,
            subscription=self.sub1,
            issue_date=date(2026, 2, 1),
            due_date=date(2026, 2, 15),
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00'),
        )

        csv_data = (
            "date,description,amount,reference\n"
            "2026-02-10,Wire Transfer,100.00,Acme Corp wire transfer\n"
        )
        uploaded = SimpleUploadedFile("statement.csv", csv_data.encode('utf-8'), content_type="text/csv")

        self.client.force_login(self.staff)
        self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': uploaded})

        statement = BankStatement.objects.latest('id')
        row = statement.rows.first()
        self.assertEqual(row.confidence, BankStatementRow.Confidence.MEDIUM)
        self.assertEqual(row.status, BankStatementRow.Status.NEEDS_REVIEW)
        self.assertIn("Ambiguous", row.match_reasons)

    def test_unmatched_row_and_manual_handling(self):
        """
        Row with unknown reference and unknown amount is marked Unmatched (Low Confidence).
        Staff can select an invoice from the dropdown to reconcile manually.
        """
        csv_data = (
            "date,description,amount,reference\n"
            "2026-01-15,Random unidentifiable deposit,55.00,UNKNOWN REF 9999\n"
        )
        uploaded = SimpleUploadedFile("statement.csv", csv_data.encode('utf-8'), content_type="text/csv")

        self.client.force_login(self.staff)
        self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': uploaded})

        statement = BankStatement.objects.latest('id')
        self.assertEqual(statement.unmatched_count, 1)

        row = statement.rows.first()
        self.assertEqual(row.status, BankStatementRow.Status.UNMATCHED)
        self.assertEqual(row.confidence, BankStatementRow.Confidence.LOW)
        self.assertIsNone(row.matched_invoice)

        # Staff manually assigns row to inv1
        manual_url = reverse('invoicing:reconciliation_row_manual', kwargs={'row_pk': row.pk})
        resp_manual = self.client.post(manual_url, {'invoice_id': self.inv1.pk}, follow=True)
        self.assertEqual(resp_manual.status_code, 200)

        row.refresh_from_db()
        self.assertEqual(row.status, BankStatementRow.Status.MANUALLY_RECONCILED)
        self.assertEqual(row.matched_invoice, self.inv1)
        self.assertIsNotNone(row.payment)
        self.assertEqual(row.payment.amount, Decimal('55.00'))

    def test_duplicate_upload_idempotency(self):
        """
        Uploading the same CSV twice stores a SHA-256 hash per row and skips
        already reconciled transactions, guaranteeing no row is applied twice.
        """
        csv_data = (
            f"date,description,amount,reference\n"
            f"2026-01-10,Wire settlement,100.00,{self.inv1.invoice_number} Acme Corp\n"
        )

        self.client.force_login(self.staff)

        # First upload: Auto-reconciled
        file1 = SimpleUploadedFile("statement_first.csv", csv_data.encode('utf-8'), content_type="text/csv")
        self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': file1})
        self.assertEqual(Payment.objects.filter(invoice=self.inv1).count(), 1)

        # Second upload with identical row data
        file2 = SimpleUploadedFile("statement_second.csv", csv_data.encode('utf-8'), content_type="text/csv")
        self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': file2})

        # Statement 2 marks row as DUPLICATE
        statement2 = BankStatement.objects.latest('id')
        self.assertEqual(statement2.duplicate_count, 1)
        self.assertEqual(statement2.auto_reconciled_count, 0)

        row2 = statement2.rows.first()
        self.assertEqual(row2.status, BankStatementRow.Status.DUPLICATE)
        self.assertIn("Duplicate transaction ignored", row2.match_reasons)

        # Payments count strictly unchanged (no double billing!)
        self.assertEqual(Payment.objects.filter(invoice=self.inv1).count(), 1)

    def test_file_type_and_size_validation(self):
        """Validates file extension, empty files, missing columns, and file size limits."""
        self.client.force_login(self.staff)

        # 1. Non-CSV extension rejected
        txt_file = SimpleUploadedFile("statement.txt", b"date,description,amount,reference\n", content_type="text/plain")
        resp = self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': txt_file}, follow=True)
        self.assertContains(resp, "Invalid file format. Only .CSV files are accepted")

        # 2. Empty CSV rejected
        empty_file = SimpleUploadedFile("empty.csv", b"", content_type="text/csv")
        resp = self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': empty_file}, follow=True)
        self.assertContains(resp, "The uploaded CSV file is empty")

        # 3. Missing required columns rejected
        bad_csv = SimpleUploadedFile("bad.csv", b"date,amount\n2026-01-01,50.00\n", content_type="text/csv")
        resp = self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': bad_csv}, follow=True)
        self.assertContains(resp, "Missing required CSV column")

        # 4. Exceeds 5MB rejected
        large_content = b"x" * (6 * 1024 * 1024)
        large_file = SimpleUploadedFile("large.csv", large_content, content_type="text/csv")
        rows, errors = parse_statement_csv(large_file)
        self.assertEqual(len(rows), 0)
        self.assertTrue(any("exceeds the 5MB limit" in err for err in errors))

    def test_customer_forbidden_from_reconciliation(self):
        """Customers are strictly forbidden from accessing the bank reconciliation system."""
        self.client.force_login(self.customer)

        resp = self.client.get(reverse('invoicing:reconciliation_dashboard'))
        self.assertEqual(resp.status_code, 403)

        dummy_csv = SimpleUploadedFile("statement.csv", b"date,description,amount,reference\n", content_type="text/csv")
        resp_post = self.client.post(reverse('invoicing:reconciliation_dashboard'), {'statement_file': dummy_csv})
        self.assertEqual(resp_post.status_code, 403)


class MonthlyFinanceReportTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            email='admin_fin_test@brightloop.local',
            password='AdminPassword@1',
            role=User.Role.ADMIN
        )
        self.staff = User.objects.create_user(
            email='staff_fin_test@brightloop.local',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.customer1 = User.objects.create_user(
            email='acme_corp@client.com',
            first_name='Acme',
            last_name='Corp',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.customer2 = User.objects.create_user(
            email='beta_inc@client.com',
            first_name='Beta',
            last_name='Inc',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.customer3 = User.objects.create_user(
            email='gamma_llc@client.com',
            first_name='Gamma',
            last_name='LLC',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )

        self.plan_monthly = Plan.objects.create(
            name="Cloud Monthly Plan",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY
        )
        self.plan_yearly = Plan.objects.create(
            name="Cloud Yearly Plan",
            price=Decimal('1200.00'),
            billing_period=Plan.BillingPeriod.YEARLY
        )
        self.product = Product.objects.create(
            name="Platform Seat",
            sales_price=Decimal('50.00')
        )

    def test_monthly_metrics_calculation_with_known_seeded_data(self):
        """
        Validates calculation accuracy of active/new/closed subscriptions, MRR,
        revenue collected, amount owed, overdue aging buckets (1-30, 31-60, 60+),
        and top customers against deterministic test data.
        """
        # Active subscription 1: started in Jan 2026 ($100/mo)
        sub1 = Subscription.objects.create(
            customer=self.customer1,
            plan=self.plan_monthly,
            start_date=date(2026, 1, 10),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub1,
            product=self.product,
            quantity=2,
            unit_price=Decimal('50.00')
        )

        # Active subscription 2: started in 2025 ($1200/yr -> $100 MRR)
        sub2 = Subscription.objects.create(
            customer=self.customer2,
            plan=self.plan_yearly,
            start_date=date(2025, 6, 1),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub2,
            product=self.product,
            quantity=1,
            unit_price=Decimal('1200.00')
        )

        # Closed subscription 3: expired and closed in Jan 2026
        sub3 = Subscription.objects.create(
            customer=self.customer3,
            plan=self.plan_monthly,
            start_date=date(2025, 1, 1),
            expiry_date=date(2026, 1, 15),
            status=Subscription.Status.CLOSED,
        )
        history = SubscriptionStatusHistory.objects.create(
            subscription=sub3,
            from_status=Subscription.Status.ACTIVE,
            to_status=Subscription.Status.CLOSED,
            changed_by=self.admin,
            notes="Expired end of period"
        )
        SubscriptionStatusHistory.objects.filter(pk=history.pk).update(
            changed_at=timezone.make_aware(datetime(2026, 1, 15, 12, 0, 0))
        )

        # Invoices:
        # 1. Fully paid invoice for customer 1 ($100 paid in Jan 2026)
        inv_paid = Invoice.objects.create(
            subscription=sub1,
            customer=self.customer1,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            issue_date=date(2026, 1, 10),
            due_date=date(2026, 1, 25),
            status=Invoice.Status.CONFIRMED,
            subtotal=Decimal('100.00'),
            grand_total=Decimal('100.00'),
        )
        Payment.objects.create(
            invoice=inv_paid,
            amount=Decimal('100.00'),
            date=date(2026, 1, 12),
            note='PAY-PAID-001'
        )

        # 2. Partially paid invoice for customer 2 ($300 total, $200 paid, $100 due).
        # Due 2026-01-20 -> As of 2026-02-01, 12 days past due (1-30 days bucket).
        inv_part = Invoice.objects.create(
            subscription=sub2,
            customer=self.customer2,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            issue_date=date(2026, 1, 15),
            due_date=date(2026, 1, 20),
            status=Invoice.Status.CONFIRMED,
            subtotal=Decimal('300.00'),
            grand_total=Decimal('300.00'),
        )
        Payment.objects.create(
            invoice=inv_part,
            amount=Decimal('200.00'),
            date=date(2026, 1, 18),
            note='PAY-PART-002'
        )

        # 3. 31-60 days overdue invoice for customer 3 ($250 due).
        # Due 2025-12-15 -> As of 2026-02-01, 48 days past due.
        Invoice.objects.create(
            subscription=sub3,
            customer=self.customer3,
            period_start=date(2025, 12, 1),
            period_end=date(2025, 12, 31),
            issue_date=date(2025, 12, 1),
            due_date=date(2025, 12, 15),
            status=Invoice.Status.CONFIRMED,
            subtotal=Decimal('250.00'),
            grand_total=Decimal('250.00'),
        )

        # 4. 60+ days overdue invoice for customer 1 ($500 due).
        # Due 2025-10-20 -> As of 2026-02-01, 104 days past due.
        Invoice.objects.create(
            subscription=sub1,
            customer=self.customer1,
            period_start=date(2025, 10, 1),
            period_end=date(2025, 10, 31),
            issue_date=date(2025, 10, 1),
            due_date=date(2025, 10, 20),
            status=Invoice.Status.CONFIRMED,
            subtotal=Decimal('500.00'),
            grand_total=Decimal('500.00'),
        )

        as_of = date(2026, 2, 1)
        metrics = calculate_monthly_metrics(year=2026, month=1, as_of_date=as_of)

        self.assertEqual(metrics['active_subscriptions'], 2)
        self.assertEqual(metrics['new_subscriptions'], 1)
        self.assertEqual(metrics['closed_subscriptions'], 1)
        self.assertEqual(metrics['mrr'], Decimal('200.00'))
        self.assertEqual(metrics['revenue_received'], Decimal('300.00'))  # $100 + $200
        self.assertEqual(metrics['amount_still_owed'], Decimal('850.00'))  # $100 + $250 + $500

        # Aging buckets validation
        self.assertEqual(metrics['overdue_1_30_count'], 1)
        self.assertEqual(metrics['overdue_1_30_amount'], Decimal('100.00'))

        self.assertEqual(metrics['overdue_31_60_count'], 1)
        self.assertEqual(metrics['overdue_31_60_amount'], Decimal('250.00'))

        self.assertEqual(metrics['overdue_60_plus_count'], 1)
        self.assertEqual(metrics['overdue_60_plus_amount'], Decimal('500.00'))

        self.assertEqual(metrics['total_overdue_count'], 3)
        self.assertEqual(metrics['total_overdue_amount'], Decimal('850.00'))

        # Top customers validation
        self.assertEqual(len(metrics['top_customers']), 2)
        self.assertEqual(metrics['top_customers'][0]['email'], 'beta_inc@client.com')
        self.assertEqual(metrics['top_customers'][0]['total_paid'], '200.00')
        self.assertEqual(metrics['top_customers'][1]['email'], 'acme_corp@client.com')
        self.assertEqual(metrics['top_customers'][1]['total_paid'], '100.00')

    def test_single_generation_constraint_and_idempotency(self):
        """Calling generate_monthly_report twice returns existing record without creating duplicates."""
        report1, created1 = generate_monthly_report(2026, 3, as_of_date=date(2026, 4, 1), generated_by=self.admin)
        self.assertTrue(created1)
        self.assertEqual(MonthlyReport.objects.filter(year=2026, month=3).count(), 1)

        # Second call with force=False returns existing report
        report2, created2 = generate_monthly_report(2026, 3, as_of_date=date(2026, 4, 1), generated_by=self.admin)
        self.assertFalse(created2)
        self.assertEqual(report1.pk, report2.pk)
        self.assertEqual(MonthlyReport.objects.filter(year=2026, month=3).count(), 1)

        # Database unique constraint also prevents duplicate insertion
        with self.assertRaises(IntegrityError):
            MonthlyReport.objects.create(
                year=2026,
                month=3,
                period_start=date(2026, 3, 1),
                period_end=date(2026, 3, 31)
            )

    def test_admin_console_email_dispatch(self):
        """Report generation emails executive summary to all active admins."""
        mail.outbox = []
        report, created = generate_monthly_report(2026, 4, as_of_date=date(2026, 5, 1), generated_by=self.admin)
        self.assertTrue(created)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn("Brightloop Month-End Finance Report: April 2026", msg.subject)
        self.assertIn("Active Subscriptions:", msg.body)
        self.assertIn("MRR:", msg.body)
        self.assertIn("Revenue Received:", msg.body)
        self.assertIn("OVERDUE INVOICES & AGING BUCKETS", msg.body)

    def test_csv_download_endpoint(self):
        """CSV download returns attachment with correct MIME type and formatted content."""
        report, _ = generate_monthly_report(2026, 5, as_of_date=date(2026, 6, 1), generated_by=self.admin)
        self.client.force_login(self.staff)

        resp = self.client.get(reverse('invoicing:report_csv', kwargs={'pk': report.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        self.assertIn('attachment; filename="Brightloop_Monthly_Report_2026_05.csv"', resp['Content-Disposition'])

        csv_text = resp.content.decode('utf-8')
        self.assertIn("BRIGHTLOOP MONTH-END FINANCE REPORT", csv_text)
        self.assertIn("Period,May 2026", csv_text)
        self.assertIn("Monthly Recurring Revenue (MRR)", csv_text)
        self.assertIn("OVERDUE AGING BUCKET", csv_text)

    def test_daily_jobs_integration_on_first_of_month(self):
        """Daily automation generates previous month report on the 1st, and never twice."""
        # Run on 2026-07-01: should trigger report for June 2026 (2026, 6)
        run_record = execute_daily_jobs(
            trigger='scheduled',
            user=None,
            as_of_date=date(2026, 7, 1)
        )
        self.assertEqual(run_record.reports_generated, 1)
        self.assertTrue(MonthlyReport.objects.filter(year=2026, month=6).exists())

        # Running again on same day creates 0 duplicate reports
        run_record2 = execute_daily_jobs(
            trigger='scheduled',
            user=None,
            as_of_date=date(2026, 7, 1)
        )
        self.assertEqual(run_record2.reports_generated, 0)
        self.assertEqual(MonthlyReport.objects.filter(year=2026, month=6).count(), 1)

        # Running on the 2nd does not trigger report generation
        run_record3 = execute_daily_jobs(
            trigger='scheduled',
            user=None,
            as_of_date=date(2026, 7, 2)
        )
        self.assertEqual(run_record3.reports_generated, 0)

    def test_permissions_role_enforcement(self):
        """Staff/Admin have full access; Customers are blocked with 403 Forbidden."""
        report, _ = generate_monthly_report(2026, 8, as_of_date=date(2026, 9, 1), generated_by=self.admin)

        # Customer is forbidden
        self.client.force_login(self.customer1)
        resp_list = self.client.get(reverse('invoicing:report_list'))
        self.assertEqual(resp_list.status_code, 403)

        resp_detail = self.client.get(reverse('invoicing:report_detail', kwargs={'pk': report.pk}))
        self.assertEqual(resp_detail.status_code, 403)

        resp_gen = self.client.post(reverse('invoicing:report_generate'), {'year': 2026, 'month': 8})
        self.assertEqual(resp_gen.status_code, 403)

        resp_csv = self.client.get(reverse('invoicing:report_csv', kwargs={'pk': report.pk}))
        self.assertEqual(resp_csv.status_code, 403)

        # Staff can view reports and download CSV
        self.client.force_login(self.staff)
        resp_staff_list = self.client.get(reverse('invoicing:report_list'))
        self.assertEqual(resp_staff_list.status_code, 200)

        resp_staff_detail = self.client.get(reverse('invoicing:report_detail', kwargs={'pk': report.pk}))
        self.assertEqual(resp_staff_detail.status_code, 200)

        # Admin can generate
        self.client.force_login(self.admin)
        resp_admin_gen = self.client.post(
            reverse('invoicing:report_generate'),
            {'year': 2026, 'month': 8, 'force': '1'},
            follow=True
        )
        self.assertEqual(resp_admin_gen.status_code, 200)
        self.assertContains(resp_admin_gen, "Month-end report for August 2026 generated successfully")




