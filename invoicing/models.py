from decimal import Decimal
from datetime import date
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.conf import settings

class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Draft')
        CONFIRMED = 'confirmed', _('Confirmed')
        PAID = 'paid', _('Paid')
        CANCELLED = 'cancelled', _('Cancelled')

    invoice_number = models.CharField(
        _('invoice number'),
        max_length=50,
        unique=True,
        db_index=True,
        editable=False,
    )
    subscription = models.ForeignKey(
        'subscriptions.Subscription',
        on_delete=models.PROTECT,
        related_name='invoices',
        db_index=True,
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='invoices',
        db_index=True,
    )
    period_start = models.DateField(_('billing period start'))
    period_end = models.DateField(_('billing period end'))
    issue_date = models.DateField(_('issue date'), default=timezone.now)
    due_date = models.DateField(_('due date'))
    status = models.CharField(
        _('status'),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    subtotal = models.DecimalField(
        _('subtotal ($)'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    discount_total = models.DecimalField(
        _('discount total ($)'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    tax_total = models.DecimalField(
        _('tax total ($)'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    grand_total = models.DecimalField(
        _('grand total ($)'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('invoice')
        verbose_name_plural = _('invoices')
        ordering = ['-issue_date', '-id']
        constraints = [
            # DB-level unique constraint prevents double billing even under concurrent execution
            models.UniqueConstraint(
                fields=['subscription', 'period_start'],
                name='unique_subscription_period_billing'
            )
        ]
        indexes = [
            models.Index(fields=['status', 'due_date']),
            models.Index(fields=['customer', 'status']),
            models.Index(fields=['subscription', 'period_start']),
        ]

    def __str__(self):
        return f"{self.invoice_number} - {self.customer.email} (${self.grand_total})"

    @classmethod
    def generate_next_number(cls):
        """Generates sequential invoice number like INV-00001."""
        last_inv = cls.objects.order_by('-id').first()
        if last_inv and last_inv.invoice_number.startswith('INV-'):
            try:
                num = int(last_inv.invoice_number.split('-')[1]) + 1
            except (IndexError, ValueError):
                num = cls.objects.count() + 1
        else:
            num = cls.objects.count() + 1
        return f"INV-{num:05d}"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            self.invoice_number = self.generate_next_number()
        super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        """True if invoice is unpaid and past due date."""
        if self.status in [self.Status.PAID, self.Status.CANCELLED]:
            return False
        return date.today() > self.due_date

    def can_transition_to(self, new_status):
        """Validates status progression: Draft > Confirmed > Paid (plus Cancelled)."""
        curr = self.status
        if curr == new_status:
            return True, ""
        if curr == self.Status.DRAFT:
            if new_status in [self.Status.CONFIRMED, self.Status.CANCELLED]:
                return True, ""
            return False, _(f"Draft invoice must be Confirmed before being marked Paid.")
        elif curr == self.Status.CONFIRMED:
            if new_status in [self.Status.PAID, self.Status.CANCELLED]:
                return True, ""
            return False, _(f"Confirmed invoice can only transition to Paid or Cancelled.")
        elif curr == self.Status.PAID:
            return False, _("Paid invoice cannot be modified or cancelled.")
        elif curr == self.Status.CANCELLED:
            return False, _("Cancelled invoice cannot be reopened.")
        return False, _("Invalid transition.")

    def transition_to(self, new_status):
        valid, msg = self.can_transition_to(new_status)
        if not valid:
            raise ValidationError(msg)
        self.status = new_status
        self.save(update_fields=['status', 'updated_at'])


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, related_name='lines', on_delete=models.CASCADE)
    description = models.CharField(_('description'), max_length=255)
    product = models.ForeignKey('catalog.Product', on_delete=models.SET_NULL, null=True, blank=True)
    variant = models.ForeignKey('catalog.ProductVariant', on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(_('quantity'), default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(
        _('unit price ($)'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    tax = models.ForeignKey('catalog.Tax', on_delete=models.SET_NULL, null=True, blank=True)
    tax_amount = models.DecimalField(
        _('tax amount ($)'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    discount_amount = models.DecimalField(
        _('discount amount ($)'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    amount = models.DecimalField(
        _('amount ($)'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text=_("Final line total after line discounts and line taxes.")
    )

    class Meta:
        verbose_name = _('invoice line')
        verbose_name_plural = _('invoice lines')

    def __str__(self):
        return f"{self.quantity}x {self.description} (${self.amount})"

    @property
    def line_subtotal(self):
        return (Decimal(self.quantity) * self.unit_price).quantize(Decimal('0.01'))
