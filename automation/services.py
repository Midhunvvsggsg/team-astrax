import time
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from invoicing.models import Invoice
from invoicing.reports import generate_monthly_report
from invoicing.services import generate_invoices
from subscriptions.models import Subscription, SubscriptionStatusHistory

from .clock import get_today
from .models import AutomationRun, Reminder


def execute_daily_jobs(
    trigger: str = AutomationRun.Trigger.MANUAL,
    user: Optional[User] = None,
    as_of_date: Optional[date] = None,
) -> AutomationRun:
    """
    Executes the scheduled daily automated workflows in strict sequence:
    (a) Generate invoices via existing idempotent billing engine
    (b) Auto-renew subscriptions expiring within 3 days with renewable=True (never twice)
    (c) Close expired subscriptions with auto_close_on_end=True
    (d) Send email reminders (non-renewable expiring within 7 days, invoices overdue by 3, 7, 14 days)

    Each step runs in an isolated transaction block. A failure in one step or item
    is recorded in errors and does NOT stop the remaining steps.
    """
    if as_of_date is None:
        as_of_date = get_today()

    start_time = time.time()
    errors = []
    invoices_created = 0
    renewals_made = 0
    subscriptions_closed = 0
    reminders_sent = 0

    run_record = AutomationRun.objects.create(
        started_at=timezone.now(),
        trigger=trigger,
        initiated_by=user if user and user.is_authenticated else None,
    )

    # --------------------------------------------------------------------------
    # Step (a): Generate Recurring Invoices (Idempotent Catch-up Service)
    # --------------------------------------------------------------------------
    try:
        with transaction.atomic():
            billing_results = generate_invoices(as_of_date=as_of_date)
            invoices_created = billing_results.get('created', 0)
            if billing_results.get('errors'):
                for err in billing_results['errors']:
                    errors.append(f"Step (a) Billing error: {err}")
    except Exception as e:
        errors.append(f"Step (a) Billing run fatal error: {str(e)}")

    # --------------------------------------------------------------------------
    # Step (b): Auto-Renew Subscriptions Expiring Within 3 Days (Renewable Plans)
    # --------------------------------------------------------------------------
    try:
        renewable_subs = (
            Subscription.objects
            .filter(
                status=Subscription.Status.ACTIVE,
                plan__renewable=True,
                expiry_date__isnull=False,
                expiry_date__gte=as_of_date,
                expiry_date__lte=as_of_date + timedelta(days=3),
            )
            .select_related('plan', 'customer')
            .order_by('id')
        )

        for sub in renewable_subs:
            old_expiry = sub.expiry_date
            cycle_note = f"Automated renewal for expiry {old_expiry}"

            # Idempotency guard: verify not already renewed for this expiration cycle
            already_renewed = SubscriptionStatusHistory.objects.filter(
                subscription=sub,
                notes__contains=cycle_note,
            ).exists()

            if already_renewed:
                continue

            try:
                with transaction.atomic():
                    sub.renew(
                        user=user if user and user.is_authenticated else None,
                        notes=f"{cycle_note} on {as_of_date} via daily automation.",
                        base_date=as_of_date,
                    )
                    renewals_made += 1
            except Exception as e:
                errors.append(f"Step (b) Failed to auto-renew {sub.subscription_number}: {str(e)}")
    except Exception as e:
        errors.append(f"Step (b) Auto-renewal query error: {str(e)}")

    # --------------------------------------------------------------------------
    # Step (c): Close Expired Subscriptions (Auto-Close On End Plans)
    # --------------------------------------------------------------------------
    try:
        expired_subs = (
            Subscription.objects
            .filter(
                status__in=[Subscription.Status.ACTIVE, Subscription.Status.PAUSED],
                plan__auto_close_on_end=True,
                expiry_date__isnull=False,
                expiry_date__lt=as_of_date,
            )
            .select_related('plan', 'customer')
            .order_by('id')
        )

        for sub in expired_subs:
            try:
                with transaction.atomic():
                    sub.transition_to(
                        Subscription.Status.CLOSED,
                        user=user if user and user.is_authenticated else None,
                        notes=f"Auto-closed by scheduled automation on {as_of_date} (expiry was {sub.expiry_date})."
                    )
                    subscriptions_closed += 1
            except Exception as e:
                errors.append(f"Step (c) Failed to auto-close {sub.subscription_number}: {str(e)}")
    except Exception as e:
        errors.append(f"Step (c) Auto-close query error: {str(e)}")

    # --------------------------------------------------------------------------
    # Step (d): Automated Email Reminders (Subscriptions & Overdue Invoices)
    # --------------------------------------------------------------------------
    try:
        # Part 1: Subscriptions expiring within 7 days that are NOT renewable
        expiring_non_renewable = (
            Subscription.objects
            .filter(
                status=Subscription.Status.ACTIVE,
                plan__renewable=False,
                expiry_date__isnull=False,
                expiry_date__gte=as_of_date,
                expiry_date__lte=as_of_date + timedelta(days=7),
            )
            .select_related('plan', 'customer')
            .order_by('id')
        )

        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'support@brightloop.local')

        for sub in expiring_non_renewable:
            # Check unique constraint in advance
            if Reminder.objects.filter(
                subscription=sub,
                reminder_type=Reminder.ReminderType.EXPIRING_NON_RENEWABLE,
                target_date=sub.expiry_date,
            ).exists():
                continue

            try:
                with transaction.atomic():
                    subject = f"Notice: Subscription {sub.subscription_number} Expiring on {sub.expiry_date}"
                    body = (
                        f"Dear {sub.customer.full_name or sub.customer.email},\n\n"
                        f"This is an automated reminder that your subscription {sub.subscription_number} "
                        f"({sub.plan.name}) is scheduled to expire on {sub.expiry_date}.\n\n"
                        f"Because this plan is not set to auto-renew, your access will terminate on this date.\n"
                        f"Please contact support or visit your customer portal to select a new plan.\n\n"
                        f"Brightloop Operations Team\n"
                    )

                    send_mail(subject, body, from_email, [sub.customer.email], fail_silently=False)

                    Reminder.objects.create(
                        reminder_type=Reminder.ReminderType.EXPIRING_NON_RENEWABLE,
                        recipient=sub.customer,
                        subscription=sub,
                        target_date=sub.expiry_date,
                        subject=subject,
                        message=body,
                    )
                    reminders_sent += 1
            except Exception as e:
                errors.append(f"Step (d) Failed reminder for subscription {sub.subscription_number}: {str(e)}")

        # Part 2: Overdue Invoices at 3, 7, and 14 days
        confirmed_unpaid_invoices = (
            Invoice.objects
            .filter(
                status=Invoice.Status.CONFIRMED,
                due_date__lt=as_of_date,
            )
            .select_related('customer', 'subscription__plan')
            .order_by('id')
        )

        for inv in confirmed_unpaid_invoices:
            if inv.amount_due <= Decimal('0.00'):
                continue

            days_overdue = (as_of_date - inv.due_date).days

            # Milestone targets to send
            milestones = []
            if days_overdue >= 3:
                milestones.append((3, Reminder.ReminderType.OVERDUE_3_DAYS))
            if days_overdue >= 7:
                milestones.append((7, Reminder.ReminderType.OVERDUE_7_DAYS))
            if days_overdue >= 14:
                milestones.append((14, Reminder.ReminderType.OVERDUE_14_DAYS))

            for days_threshold, r_type in milestones:
                # Check DB unique constraint before sending
                if Reminder.objects.filter(invoice=inv, reminder_type=r_type).exists():
                    continue

                try:
                    with transaction.atomic():
                        subject = f"Urgent: Invoice {inv.invoice_number} is {days_threshold}+ Days Overdue"
                        body = (
                            f"Dear {inv.customer.full_name or inv.customer.email},\n\n"
                            f"Our records indicate invoice {inv.invoice_number} is now {days_overdue} days overdue "
                            f"(was due on {inv.due_date}).\n\n"
                            f"Outstanding Balance Due: ${inv.amount_due}\n"
                            f"Subscription: {inv.subscription.subscription_number} ({inv.subscription.plan.name})\n\n"
                            f"Please sign in to your customer portal to submit payment immediately.\n\n"
                            f"Brightloop Accounts Receivable\n"
                        )

                        send_mail(subject, body, from_email, [inv.customer.email], fail_silently=False)

                        Reminder.objects.create(
                            reminder_type=r_type,
                            recipient=inv.customer,
                            invoice=inv,
                            target_date=inv.due_date,
                            subject=subject,
                            message=body,
                        )
                        reminders_sent += 1
                except Exception as e:
                    errors.append(f"Step (d) Failed reminder for invoice {inv.invoice_number} ({r_type}): {str(e)}")

    except Exception as e:
        errors.append(f"Step (d) Reminders workflow error: {str(e)}")

    # --------------------------------------------------------------------------
    # Step (e): Month-End Finance Report (Runs on the 1st of the month)
    # --------------------------------------------------------------------------
    reports_generated = 0
    if as_of_date.day == 1:
        try:
            if as_of_date.month == 1:
                rep_year = as_of_date.year - 1
                rep_month = 12
            else:
                rep_year = as_of_date.year
                rep_month = as_of_date.month - 1

            with transaction.atomic():
                report, created = generate_monthly_report(
                    year=rep_year,
                    month=rep_month,
                    as_of_date=as_of_date,
                    generated_by=user,
                )
                if created:
                    reports_generated += 1
        except Exception as e:
            errors.append(f"Step (e) Month-end finance report error: {str(e)}")

    # --------------------------------------------------------------------------
    # Finalize Run Record
    # --------------------------------------------------------------------------
    duration = Decimal(str(round(time.time() - start_time, 3)))
    run_record.completed_at = timezone.now()
    run_record.invoices_created = invoices_created
    run_record.renewals_made = renewals_made
    run_record.subscriptions_closed = subscriptions_closed
    run_record.reminders_sent = reminders_sent
    run_record.reports_generated = reports_generated
    run_record.duration_seconds = duration

    if errors:
        run_record.errors = "\n".join(errors)
        run_record.status = (
            AutomationRun.Status.PARTIAL
            if (invoices_created or renewals_made or subscriptions_closed or reminders_sent)
            else AutomationRun.Status.FAILED
        )
    else:
        run_record.status = AutomationRun.Status.SUCCESS

    run_record.save()
    return run_record
