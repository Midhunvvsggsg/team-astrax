from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from automation.models import AutomationRun, Reminder
from automation.services import execute_daily_jobs
from catalog.models import Plan, Product
from invoicing.models import Invoice, InvoiceLine
from subscriptions.models import Subscription, SubscriptionLine


class DailyAutomationEngineTests(TestCase):
    def setUp(self):
        # Users
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

        # Products
        self.seat = Product.objects.create(
            name="Platform Seat",
            sales_price=Decimal('50.00'),
            cost_price=Decimal('10.00'),
        )

        # Plans
        self.plan_renewable = Plan.objects.create(
            name="Monthly Pro Renewable",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY,
            renewable=True,
            auto_close_on_end=False,
        )
        self.plan_autoclose = Plan.objects.create(
            name="Fixed Term AutoClose",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY,
            renewable=False,
            auto_close_on_end=True,
        )
        self.plan_non_renewable = Plan.objects.create(
            name="Seasonal Fixed Non-Renewable",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY,
            renewable=False,
            auto_close_on_end=False,
        )

        self.client = Client()

    def test_run_daily_jobs_executes_all_steps_in_order(self):
        """
        Verifies steps (a), (b), (c), (d) all execute successfully:
        (a) Recurring billing catch-up
        (b) Auto-renew subscription expiring in 2 days
        (c) Auto-close expired subscription
        (d) Dispatch reminders for non-renewable expiring in 5 days & overdue invoice
        """
        as_of_date = date(2026, 6, 15)

        # (a) Subscription due for billing
        sub_billing = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_renewable,
            start_date=date(2026, 6, 1),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub_billing,
            product=self.seat,
            quantity=2,
            unit_price=Decimal('50.00'),
        )

        # (b) Subscription expiring in 2 days (renewable)
        sub_renew = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_renewable,
            start_date=date(2026, 5, 17),
            expiry_date=as_of_date + timedelta(days=2),  # 2026-06-17
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub_renew,
            product=self.seat,
            quantity=1,
            unit_price=Decimal('50.00'),
        )
        Invoice.objects.create(
            customer=self.customer,
            subscription=sub_renew,
            issue_date=date(2026, 5, 17),
            due_date=date(2026, 5, 17),
            period_start=date(2026, 5, 17),
            period_end=date(2026, 6, 16),
            status=Invoice.Status.PAID,
        )

        # (c) Subscription already expired (auto_close_on_end=True)
        sub_close = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_autoclose,
            start_date=date(2026, 5, 1),
            expiry_date=as_of_date - timedelta(days=2),  # 2026-06-13
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub_close,
            product=self.seat,
            quantity=1,
            unit_price=Decimal('50.00'),
        )
        Invoice.objects.create(
            customer=self.customer,
            subscription=sub_close,
            issue_date=date(2026, 5, 1),
            due_date=date(2026, 5, 1),
            period_start=date(2026, 5, 1),
            period_end=date(2026, 6, 15),
            status=Invoice.Status.PAID,
        )

        # (d1) Subscription expiring in 5 days (non-renewable) -> reminder notice
        sub_remind = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_non_renewable,
            start_date=date(2026, 5, 20),
            expiry_date=as_of_date + timedelta(days=5),  # 2026-06-20
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub_remind,
            product=self.seat,
            quantity=1,
            unit_price=Decimal('50.00'),
        )
        Invoice.objects.create(
            customer=self.customer,
            subscription=sub_remind,
            issue_date=date(2026, 5, 20),
            due_date=date(2026, 5, 20),
            period_start=date(2026, 5, 20),
            period_end=date(2026, 6, 19),
            status=Invoice.Status.PAID,
        )

        # (d2) Invoice overdue by 3 days
        inv_overdue = Invoice.objects.create(
            customer=self.customer,
            subscription=sub_billing,
            issue_date=date(2026, 5, 1),
            due_date=as_of_date - timedelta(days=3),  # 2026-06-12 (3 days overdue)
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00'),
        )
        InvoiceLine.objects.create(
            invoice=inv_overdue,
            description="Seat License",
            product=self.seat,
            quantity=1,
            unit_price=Decimal('100.00'),
        )

        # Execute daily jobs
        mail.outbox.clear()
        run = execute_daily_jobs(
            trigger=AutomationRun.Trigger.SCHEDULED,
            user=None,
            as_of_date=as_of_date,
        )

        self.assertEqual(run.status, AutomationRun.Status.SUCCESS)
        self.assertEqual(run.errors, '')

        # Step (a): 1 invoice created for sub_billing
        self.assertEqual(run.invoices_created, 1)

        # Step (b): 1 renewal made
        self.assertEqual(run.renewals_made, 1)
        sub_renew.refresh_from_db()
        self.assertGreater(sub_renew.expiry_date, as_of_date + timedelta(days=2))

        # Step (c): 1 subscription closed
        self.assertEqual(run.subscriptions_closed, 1)
        sub_close.refresh_from_db()
        self.assertEqual(sub_close.status, Subscription.Status.CLOSED)

        # Step (d): 2 reminders sent (1 expiring sub, 1 overdue 3 days invoice)
        self.assertEqual(run.reminders_sent, 2)
        self.assertEqual(Reminder.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 2)

    def test_idempotency_running_twice_creates_no_duplicates(self):
        """
        Running run_daily_jobs twice on the same date produces:
        - 0 duplicate invoices
        - 0 duplicate renewals
        - 0 duplicate closed subscriptions
        - 0 duplicate reminders
        """
        as_of_date = date(2026, 7, 10)

        # Renewable subscription expiring tomorrow
        sub_renew = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_renewable,
            start_date=date(2026, 6, 11),
            expiry_date=as_of_date + timedelta(days=1),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub_renew,
            product=self.seat,
            quantity=1,
            unit_price=Decimal('50.00'),
        )

        # Non-renewable subscription expiring in 4 days
        sub_non_renew = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_non_renewable,
            start_date=date(2026, 6, 14),
            expiry_date=as_of_date + timedelta(days=4),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub_non_renew,
            product=self.seat,
            quantity=1,
            unit_price=Decimal('50.00'),
        )

        # Confirmed invoice overdue by 7 days
        inv = Invoice.objects.create(
            customer=self.customer,
            subscription=sub_renew,
            issue_date=date(2026, 6, 1),
            due_date=as_of_date - timedelta(days=7),
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00'),
        )
        InvoiceLine.objects.create(
            invoice=inv,
            description="Seat License",
            product=self.seat,
            quantity=1,
            unit_price=Decimal('100.00'),
        )

        # First Execution
        mail.outbox.clear()
        run1 = execute_daily_jobs(as_of_date=as_of_date)
        self.assertEqual(run1.status, AutomationRun.Status.SUCCESS)
        first_reminders_count = Reminder.objects.count()
        sub_renew.refresh_from_db()
        first_renewed_expiry = sub_renew.expiry_date

        self.assertGreater(first_reminders_count, 0)
        self.assertEqual(run1.renewals_made, 1)

        # Second Execution on same date
        mail.outbox.clear()
        run2 = execute_daily_jobs(as_of_date=as_of_date)

        self.assertEqual(run2.status, AutomationRun.Status.SUCCESS)
        self.assertEqual(run2.invoices_created, 0, "No duplicate invoices generated")
        self.assertEqual(run2.renewals_made, 0, "No duplicate renewals made")
        self.assertEqual(run2.subscriptions_closed, 0, "No duplicate closes")
        self.assertEqual(run2.reminders_sent, 0, "No duplicate reminders sent")

        # Confirm DB objects remained completely stable
        self.assertEqual(Reminder.objects.count(), first_reminders_count)
        self.assertEqual(len(mail.outbox), 0, "No duplicate emails sent to mail outbox")
        sub_renew.refresh_from_db()
        self.assertEqual(sub_renew.expiry_date, first_renewed_expiry, "Subscription expiry was not shifted again")

    def test_step_failure_isolation(self):
        """
        A failure in Step (a) (generate_invoices) is logged and does NOT stop
        Step (b) (renewals), Step (c) (auto-close), or Step (d) (reminders).
        """
        as_of_date = date(2026, 8, 20)

        # Renewable subscription expiring tomorrow
        sub_renew = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_renewable,
            start_date=date(2026, 7, 21),
            expiry_date=as_of_date + timedelta(days=1),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub_renew,
            product=self.seat,
            quantity=1,
            unit_price=Decimal('50.00'),
        )

        # Expired auto-close subscription
        sub_close = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_autoclose,
            start_date=date(2026, 7, 1),
            expiry_date=as_of_date - timedelta(days=1),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub_close,
            product=self.seat,
            quantity=1,
            unit_price=Decimal('50.00'),
        )

        # Mock generate_invoices in Step (a) to fail violently
        with patch('automation.services.generate_invoices', side_effect=RuntimeError("Billing engine simulated database disk failure")):
            run = execute_daily_jobs(as_of_date=as_of_date)

        # Run status should reflect partial failure because other steps succeeded
        self.assertEqual(run.status, AutomationRun.Status.PARTIAL)
        self.assertIn("Step (a) Billing run fatal error", run.errors)
        self.assertIn("Billing engine simulated database disk failure", run.errors)

        # Steps (b) and (c) succeeded despite Step (a) crash
        self.assertEqual(run.renewals_made, 1)
        self.assertEqual(run.subscriptions_closed, 1)

        sub_renew.refresh_from_db()
        self.assertGreater(sub_renew.expiry_date, as_of_date)
        sub_close.refresh_from_db()
        self.assertEqual(sub_close.status, Subscription.Status.CLOSED)

    def test_reminder_unique_constraints(self):
        """Database enforces unique constraints preventing duplicate reminders."""
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_non_renewable,
            start_date=date(2026, 9, 1),
            expiry_date=date(2026, 9, 30),
            status=Subscription.Status.ACTIVE,
        )

        # Create subscription reminder
        Reminder.objects.create(
            reminder_type=Reminder.ReminderType.EXPIRING_NON_RENEWABLE,
            recipient=self.customer,
            subscription=sub,
            target_date=sub.expiry_date,
            subject="Expiring notice 1",
            message="Message 1",
        )

        # Attempt duplicate subscription reminder for same target_date
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Reminder.objects.create(
                    reminder_type=Reminder.ReminderType.EXPIRING_NON_RENEWABLE,
                    recipient=self.customer,
                    subscription=sub,
                    target_date=sub.expiry_date,
                    subject="Expiring notice 2",
                    message="Message 2",
                )

        # Create invoice reminder
        inv = Invoice.objects.create(
            customer=self.customer,
            subscription=sub,
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 15),
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            status=Invoice.Status.CONFIRMED,
        )

        Reminder.objects.create(
            reminder_type=Reminder.ReminderType.OVERDUE_3_DAYS,
            recipient=self.customer,
            invoice=inv,
            target_date=inv.due_date,
            subject="Invoice overdue notice 1",
            message="Overdue message 1",
        )

        # Attempt duplicate invoice reminder for same reminder_type
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Reminder.objects.create(
                    reminder_type=Reminder.ReminderType.OVERDUE_3_DAYS,
                    recipient=self.customer,
                    invoice=inv,
                    target_date=inv.due_date,
                    subject="Invoice overdue notice 2",
                    message="Overdue message 2",
                )

    def test_automation_views_and_permission_enforcement(self):
        """Admin can access dashboard and trigger jobs; customers and staff are blocked."""
        # 1. Anonymous redirected to login
        resp = self.client.get(reverse('automation:dashboard'))
        self.assertEqual(resp.status_code, 302)

        # 2. Customer denied (403)
        self.client.force_login(self.customer)
        resp = self.client.get(reverse('automation:dashboard'))
        self.assertEqual(resp.status_code, 403)
        resp_post = self.client.post(reverse('automation:run_now'))
        self.assertEqual(resp_post.status_code, 403)

        # 3. Staff denied (admin_required decorator enforces role == admin)
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('automation:dashboard'))
        self.assertEqual(resp.status_code, 403)
        resp_post = self.client.post(reverse('automation:run_now'))
        self.assertEqual(resp_post.status_code, 403)

        # 4. Superuser/Admin allowed
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('automation:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Automated Daily Operations")

        # Admin triggers POST 'Run Now'
        resp_post = self.client.post(reverse('automation:run_now'), follow=True)
        self.assertEqual(resp_post.status_code, 200)
        self.assertContains(resp_post, "Automation Run #")
        self.assertEqual(AutomationRun.objects.filter(trigger=AutomationRun.Trigger.MANUAL).count(), 1)

    def test_admin_custom_run_now_action(self):
        """Django Admin changelist custom 'Run Daily Jobs Now' triggers execution."""
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('admin:automation_run_daily_jobs'), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AutomationRun.objects.count(), 1)

    def test_management_command_run_daily_jobs(self):
        """Management command `run_daily_jobs` executes cleanly with CLI arguments."""
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan_renewable,
            start_date=date(2026, 10, 1),
            expiry_date=date(2026, 10, 31),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.seat,
            quantity=1,
            unit_price=Decimal('50.00'),
        )

        call_command(
            'run_daily_jobs',
            '--date=2026-10-30',
            '--trigger=scheduled',
        )

        run = AutomationRun.objects.latest('id')
        self.assertEqual(run.trigger, AutomationRun.Trigger.SCHEDULED)
        self.assertEqual(run.status, AutomationRun.Status.SUCCESS)
        self.assertEqual(run.renewals_made, 1)
