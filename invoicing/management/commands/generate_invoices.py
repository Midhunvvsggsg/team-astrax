from datetime import datetime

from django.core.management.base import BaseCommand

from automation.clock import get_today
from invoicing.services import generate_invoices


class Command(BaseCommand):
    help = "Generates recurring invoices for all active subscriptions with due/missed periods."

    def add_arguments(self, parser):
        parser.add_argument(
            '--as-of-date',
            type=str,
            default=None,
            help="Target billing date in YYYY-MM-DD format (defaults to today)."
        )

    def handle(self, *args, **options):
        as_of_str = options.get('as_of_date')
        if as_of_str:
            try:
                as_of_date = datetime.strptime(as_of_str, '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid date format: {as_of_str}. Expected YYYY-MM-DD."))
                return
        else:
            as_of_date = get_today()

        self.stdout.write(f"Executing recurring billing run as of {as_of_date}...")

        results = generate_invoices(as_of_date=as_of_date)

        self.stdout.write(
            self.style.SUCCESS(
                f"Billing run completed:\n"
                f" - Invoices Created: {results['created']}\n"
                f" - Subscriptions Up-To-Date (Skipped): {results['skipped']}\n"
                f" - Subscriptions Failed: {results['failed']}"
            )
        )

        if results['errors']:
            for sub_num, err in results['errors']:
                self.stderr.write(self.style.ERROR(f"Error on {sub_num}: {err}"))
