from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class AutomationRun(models.Model):
    class Trigger(models.TextChoices):
        MANUAL = 'manual', _('Manual')
        SCHEDULED = 'scheduled', _('Scheduled')

    class Status(models.TextChoices):
        SUCCESS = 'success', _('Success')
        PARTIAL = 'partial', _('Partial Failure')
        FAILED = 'failed', _('Failed')

    started_at = models.DateTimeField(_('started at'), default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(_('completed at'), null=True, blank=True)
    trigger = models.CharField(_('trigger source'), max_length=20, choices=Trigger.choices, default=Trigger.MANUAL)
    status = models.CharField(_('execution status'), max_length=20, choices=Status.choices, default=Status.SUCCESS, db_index=True)

    invoices_created = models.PositiveIntegerField(_('invoices created'), default=0)
    renewals_made = models.PositiveIntegerField(_('renewals made'), default=0)
    subscriptions_closed = models.PositiveIntegerField(_('subscriptions closed'), default=0)
    reminders_sent = models.PositiveIntegerField(_('reminders sent'), default=0)
    reports_generated = models.PositiveIntegerField(_('reports generated'), default=0)

    duration_seconds = models.DecimalField(
        _('duration (seconds)'),
        max_digits=8,
        decimal_places=3,
        default=Decimal('0.000'),
    )
    errors = models.TextField(_('error log'), blank=True, default='')
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='automation_runs',
        verbose_name=_('initiated by')
    )

    class Meta:
        verbose_name = _('automation run')
        verbose_name_plural = _('automation runs')
        ordering = ['-started_at']

    def __str__(self):
        return f"AutomationRun #{self.id} ({self.get_trigger_display()} - {self.get_status_display()}) at {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}"


class Reminder(models.Model):
    class ReminderType(models.TextChoices):
        EXPIRING_NON_RENEWABLE = 'expiring_non_renewable', _('Subscription Expiring (Non-Renewable)')
        OVERDUE_3_DAYS = 'overdue_3_days', _('Invoice Overdue (3 Days)')
        OVERDUE_7_DAYS = 'overdue_7_days', _('Invoice Overdue (7 Days)')
        OVERDUE_14_DAYS = 'overdue_14_days', _('Invoice Overdue (14 Days)')

    reminder_type = models.CharField(_('reminder type'), max_length=50, choices=ReminderType.choices, db_index=True)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='received_reminders',
        verbose_name=_('recipient')
    )
    subscription = models.ForeignKey(
        'subscriptions.Subscription',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='reminders',
        verbose_name=_('subscription')
    )
    invoice = models.ForeignKey(
        'invoicing.Invoice',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='reminders',
        verbose_name=_('invoice')
    )
    target_date = models.DateField(
        _('target date'),
        help_text=_("The expiry date or due date being referenced")
    )
    sent_at = models.DateTimeField(_('sent at'), auto_now_add=True, db_index=True)
    subject = models.CharField(_('subject'), max_length=255)
    message = models.TextField(_('message body'))

    class Meta:
        verbose_name = _('automated reminder')
        verbose_name_plural = _('automated reminders')
        ordering = ['-sent_at']
        constraints = [
            models.UniqueConstraint(
                fields=['subscription', 'reminder_type', 'target_date'],
                name='unique_subscription_reminder',
                condition=models.Q(subscription__isnull=False),
            ),
            models.UniqueConstraint(
                fields=['invoice', 'reminder_type'],
                name='unique_invoice_reminder',
                condition=models.Q(invoice__isnull=False),
            ),
        ]

    def __str__(self):
        target = self.subscription.subscription_number if self.subscription else (self.invoice.invoice_number if self.invoice else "System")
        return f"{self.get_reminder_type_display()} for {target} to {self.recipient.email} on {self.sent_at.strftime('%Y-%m-%d')}"


class DemoClock(models.Model):
    """
    Singleton model to persist the simulated demo date across processes
    when DEMO_MODE=1.
    """
    simulated_date = models.DateField(_('simulated date'), null=True, blank=True)
    is_active = models.BooleanField(_('is active'), default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('demo clock')
        verbose_name_plural = _('demo clock')

    def __str__(self):
        state = "Active" if self.is_active else "Inactive"
        return f"Demo Clock ({state}): {self.simulated_date}"

    @classmethod
    def get_instance(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        return obj

