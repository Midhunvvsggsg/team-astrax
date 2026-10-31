
from django.core.management.base import BaseCommand

from automation.clock import get_today
from subscriptions.models import Subscription


class Command(BaseCommand):
    help = "Closes active subscriptions that have passed their expiry date and have plan.auto_close_on_end enabled."

    def handle(self, *args, **options):
        today = get_today()
        expired_subs = Subscription.objects.filter(
            status__in=[Subscription.Status.ACTIVE, Subscription.Status.PAUSED],
            plan__auto_close_on_end=True,
            expiry_date__lt=today,
        ).select_related('plan', 'customer')

        count = 0
        for sub in expired_subs:
            sub.transition_to(
                Subscription.Status.CLOSED,
                user=None,
                notes=f"Auto-closed by scheduled job on {today} (expiry was {sub.expiry_date})."
            )
            count += 1
            self.stdout.write(
                self.style.SUCCESS(f"Closed subscription {sub.subscription_number} for {sub.customer.email}")
            )

        self.stdout.write(self.style.SUCCESS(f"Finished processing. Successfully closed {count} expired subscription(s)."))
