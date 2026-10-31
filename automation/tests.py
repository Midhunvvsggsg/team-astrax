from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from automation.clock import (
    advance_demo_date,
    get_demo_clock_status,
    get_today,
    reset_demo_date,
    set_demo_date,
)
from automation.models import AutomationRun, DemoClock, Reminder
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


class DemoClockTests(TestCase):
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
        self.client = Client()
        reset_demo_date()

    def tearDown(self):
        reset_demo_date()

    @override_settings(DEMO_MODE=False)
    def test_demo_mode_disabled_behavior(self):
        """When DEMO_MODE is False, get_today() is strictly real date and endpoints reject."""
        # Simulated date in DB is ignored
        DemoClock.objects.update_or_create(id=1, defaults={'simulated_date': date(2030, 1, 1), 'is_active': True})
        self.assertEqual(get_today(), date.today())

        # Advance / set returns real date
        self.assertEqual(advance_demo_date('1_day'), date.today())
        self.assertEqual(set_demo_date(date(2030, 1, 1)), date.today())

        # Status dictionary
        status = get_demo_clock_status()
        self.assertFalse(status['enabled'])
        self.assertFalse(status['is_active'])

        # Endpoints reject manipulation
        self.client.force_login(self.admin)
        resp_adv = self.client.post(reverse('automation:clock_advance'), {'amount': '1_day'})
        self.assertEqual(resp_adv.status_code, 403)

        resp_set = self.client.post(reverse('automation:clock_set'), {'target_date': '2027-01-01'})
        self.assertEqual(resp_set.status_code, 403)

        resp_reset = self.client.post(reverse('automation:clock_reset'))
        self.assertEqual(resp_reset.status_code, 403)

        resp_run = self.client.post(reverse('automation:clock_run_jobs'))
        self.assertEqual(resp_run.status_code, 403)

        # Banner is NOT rendered on page
        resp_page = self.client.get(reverse('home'))
        self.assertNotContains(resp_page, 'DEMO CLOCK:')
        self.assertNotContains(resp_page, 'demo-clock-banner')

    @override_settings(DEMO_MODE=True)
    def test_demo_mode_advance_and_set_dates(self):
        """When DEMO_MODE is True, admin can advance by 1d, 1w, 1m, set date, and reset."""
        real_today = date.today()
        self.assertEqual(get_today(), real_today)

        # 1. Advance +1 day
        day_1 = advance_demo_date('1_day')
        self.assertEqual(day_1, real_today + timedelta(days=1))
        self.assertEqual(get_today(), real_today + timedelta(days=1))

        # 2. Advance +1 week
        week_1 = advance_demo_date('1_week')
        self.assertEqual(week_1, real_today + timedelta(days=8))
        self.assertEqual(get_today(), real_today + timedelta(days=8))

        # 3. Advance +1 month
        month_1 = advance_demo_date('1_month')
        self.assertGreater(month_1, week_1)
        self.assertEqual(get_today(), month_1)

        # 4. Set explicit target date
        explicit_date = date(2028, 5, 25)
        set_demo_date(explicit_date)
        self.assertEqual(get_today(), explicit_date)

        # 5. Reset back to real time
        reset_demo_date()
        self.assertEqual(get_today(), real_today)
        clock = DemoClock.objects.first()
        self.assertFalse(clock.is_active)

    @override_settings(DEMO_MODE=True)
    def test_demo_clock_banner_display_and_reset_in_ui(self):
        """Banner 'DEMO CLOCK: <date>' appears on every page while active with Reset button."""
        simulated = date(2027, 4, 15)
        set_demo_date(simulated)

        # Admin visits pages and sees banner and controls
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('home'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "DEMO CLOCK: 2027-04-15")
        self.assertContains(resp, "Reset")
        self.assertContains(resp, "+1 Day")
        self.assertContains(resp, "+1 Week")
        self.assertContains(resp, "+1 Month")
        self.assertContains(resp, "⚡ Run daily jobs")

        # Customer visits portal and sees active DEMO CLOCK banner notice
        self.client.force_login(self.customer)
        resp_portal = self.client.get(reverse('customer_portal'))
        self.assertEqual(resp_portal.status_code, 200)
        self.assertContains(resp_portal, "DEMO CLOCK: 2027-04-15")

        # Admin presses Reset button
        self.client.force_login(self.admin)
        resp_reset = self.client.post(reverse('automation:clock_reset'), follow=True)
        self.assertEqual(resp_reset.status_code, 200)
        self.assertEqual(get_today(), date.today())

    @override_settings(DEMO_MODE=True)
    def test_demo_clock_permissions_enforcement(self):
        """Only admins can advance, set, or reset the demo clock."""
        # Anonymous redirected
        resp = self.client.post(reverse('automation:clock_advance'), {'amount': '1_day'})
        self.assertEqual(resp.status_code, 302)

        # Customer denied
        self.client.force_login(self.customer)
        resp = self.client.post(reverse('automation:clock_advance'), {'amount': '1_day'})
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post(reverse('automation:clock_set'), {'target_date': '2027-10-10'})
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post(reverse('automation:clock_reset'))
        self.assertEqual(resp.status_code, 403)

        # Staff denied
        self.client.force_login(self.staff)
        resp = self.client.post(reverse('automation:clock_advance'), {'amount': '1_day'})
        self.assertEqual(resp.status_code, 403)

        # Admin allowed
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('automation:clock_set'), {'target_date': '2027-09-01'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(get_today(), date(2027, 9, 1))

    @override_settings(DEMO_MODE=True)
    def test_demo_clock_integration_with_overdue_and_billing(self):
        """
        Advancing the demo clock drives overdue invoice checks, days overdue calculations,
        and triggers automated jobs as of the simulated date.
        """
        # Create plan, customer, active subscription, and confirmed invoice
        plan = Plan.objects.create(
            name="Cloud Plan",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY,
            renewable=True,
        )
        seat = Product.objects.create(name="Seat", sales_price=Decimal('100.00'))
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=plan,
            start_date=date(2026, 1, 1),
            status=Subscription.Status.ACTIVE,
        )
        SubscriptionLine.objects.create(subscription=sub, product=seat, quantity=1, unit_price=Decimal('100.00'))

        inv = Invoice.objects.create(
            customer=self.customer,
            subscription=sub,
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 15),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            status=Invoice.Status.CONFIRMED,
            grand_total=Decimal('100.00'),
        )
        InvoiceLine.objects.create(invoice=inv, description="Seat", product=seat, quantity=1, unit_price=Decimal('100.00'))

        # Set clock to Jan 10 (not overdue yet)
        set_demo_date(date(2026, 1, 10))
        self.assertFalse(inv.is_overdue)
        self.assertEqual(inv.days_overdue, 0)

        # Advance clock to Jan 25 (now 10 days overdue!)
        set_demo_date(date(2026, 1, 25))
        self.assertTrue(inv.is_overdue)
        self.assertEqual(inv.days_overdue, 10)

        # Admin clicks "⚡ Run daily jobs" via demo clock action
        self.client.force_login(self.admin)
        mail.outbox.clear()
        resp = self.client.post(reverse('automation:clock_run_jobs'), follow=True)
        self.assertEqual(resp.status_code, 200)

        # Daily jobs should have executed as of Jan 25:
        # overdue reminder dispatched for 3, 7, and 14 thresholds (here 10 days overdue -> 3 and 7 milestones sent)
        self.assertGreater(Reminder.objects.filter(invoice=inv).count(), 0)
        self.assertGreater(len(mail.outbox), 0)

