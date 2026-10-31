from datetime import datetime

from django.core.management.base import BaseCommand

from automation.clock import get_today
from automation.models import AutomationRun
from automation.services import execute_daily_jobs


class Command(BaseCommand):
    help = (
        "Runs the automated daily operations in sequential isolated steps:\n"
        "(a) Catch-up recurring billing (generate_invoices)\n"
        "(b) Auto-renew subscriptions expiring within 3 days\n"
        "(c) Auto-close expired subscriptions with auto_close_on_end=True\n"
        "(d) Dispatch non-renewable and overdue invoice reminders"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help="Target execution date (format: YYYY-MM-DD). Defaults to today.",
        )
        parser.add_argument(
            '--trigger',
            type=str,
            default=AutomationRun.Trigger.SCHEDULED,
            choices=[AutomationRun.Trigger.SCHEDULED, AutomationRun.Trigger.MANUAL],
            help="Execution trigger metadata (scheduled or manual).",
        )

    def handle(self, *args, **options):
        date_str = options.get('date')
        if date_str:
            try:
                as_of_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid date format: {date_str}. Expected YYYY-MM-DD."))
                return
        else:
            as_of_date = get_today()

        trigger = options.get('trigger', AutomationRun.Trigger.SCHEDULED)

        self.stdout.write(self.style.NOTICE(f"=== Starting Daily Automation Run (Date: {as_of_date}, Trigger: {trigger}) ==="))

        run_record = execute_daily_jobs(
            trigger=trigger,
            user=None,
            as_of_date=as_of_date,
        )

        self.stdout.write(self.style.SUCCESS(f"(a) Invoices generated: {run_record.invoices_created}"))
        self.stdout.write(self.style.SUCCESS(f"(b) Subscriptions auto-renewed: {run_record.renewals_made}"))
        self.stdout.write(self.style.SUCCESS(f"(c) Expired subscriptions closed: {run_record.subscriptions_closed}"))
        self.stdout.write(self.style.SUCCESS(f"(d) Automated reminders dispatched: {run_record.reminders_sent}"))
        self.stdout.write(self.style.SUCCESS(f"Duration: {run_record.duration_seconds}s | Status: {run_record.get_status_display()}"))

        if run_record.errors:
            self.stdout.write(self.style.WARNING("Logged Errors during execution:"))
            for line in run_record.errors.splitlines():
                self.stdout.write(self.style.WARNING(f"  - {line}"))

        self.stdout.write(self.style.SUCCESS(f"=== Finished AutomationRun #{run_record.id} ==="))
