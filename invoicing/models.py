from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class InvoiceQuerySet(models.QuerySet):
    def with_payment_totals(self):
        """Annotates SQL aggregate amount_paid and amount_due to prevent N+1 queries."""
        from django.db.models import Case, DecimalField, F, Sum, Value, When
        from django.db.models.functions import Coalesce, Greatest

        return self.annotate(
            annotated_amount_paid=Coalesce(
                Sum('payments__amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        ).annotate(
            annotated_amount_due=Case(
                When(status__in=['paid', 'cancelled'], then=Value(Decimal('0.00'))),
                default=Greatest(
                    F('grand_total') - F('annotated_amount_paid'),
                    Value(Decimal('0.00')),
                ),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        ).order_by('-issue_date', '-id')

class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Draft')
        CONFIRMED = 'confirmed', _('Confirmed')
        PAID = 'paid', _('Paid')
        CANCELLED = 'cancelled', _('Cancelled')

    objects = InvoiceQuerySet.as_manager()

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
    period_start = models.DateField(_('billing period start'), db_index=True)
    period_end = models.DateField(_('billing period end'))
    issue_date = models.DateField(_('issue date'), default=timezone.now, db_index=True)
    due_date = models.DateField(_('due date'), db_index=True)
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
    sent_at = models.DateTimeField(_('sent at'), null=True, blank=True)

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
        """True if invoice is unpaid, not cancelled, and past due date."""
        if self.status in [self.Status.PAID, self.Status.CANCELLED]:
            return False
        if self.amount_due <= Decimal('0.00'):
            return False
        return date.today() > self.due_date

    @property
    def days_overdue(self):
        """Days past the due date if overdue, otherwise 0."""
        if not self.is_overdue:
            return 0
        return max(0, (date.today() - self.due_date).days)

    @property
    def amount_paid(self):
        """Total payments recorded against this invoice via SQL aggregate."""
        if hasattr(self, 'annotated_amount_paid'):
            return self.annotated_amount_paid
        from django.db.models import Sum
        val = self.payments.aggregate(total=Sum('amount'))['total']
        return (val if val is not None else Decimal('0.00')).quantize(Decimal('0.01'))

    @property
    def amount_due(self):
        """Remaining balance unpaid on this invoice via SQL aggregate."""
        if self.status in [self.Status.PAID, self.Status.CANCELLED]:
            return Decimal('0.00')
        if hasattr(self, 'annotated_amount_due'):
            return self.annotated_amount_due
        return max(Decimal('0.00'), self.grand_total - self.amount_paid).quantize(Decimal('0.01'))

    def can_transition_to(self, new_status):
        """Validates status progression: Draft > Confirmed > Paid (plus Cancelled)."""
        curr = self.status
        if curr == new_status:
            return True, ""
        if curr == self.Status.DRAFT:
            if new_status in [self.Status.CONFIRMED, self.Status.CANCELLED]:
                return True, ""
            return False, _("Draft invoice must be Confirmed before being marked Paid.")
        elif curr == self.Status.CONFIRMED:
            if new_status == self.Status.CANCELLED:
                if self.amount_paid > Decimal('0.00'):
                    return False, _("Cannot cancel an invoice with recorded payments. Please refund or remove payments first.")
                return True, ""
            if new_status == self.Status.PAID:
                return True, ""
            return False, _("Confirmed invoice can only transition to Paid or Cancelled.")
        elif curr == self.Status.PAID:
            return False, _("A Paid invoice cannot be cancelled or reopened.")
        elif curr == self.Status.CANCELLED:
            return False, _("A Cancelled invoice cannot be reopened.")
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


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = 'cash', _('Cash')
        BANK_TRANSFER = 'bank_transfer', _('Bank Transfer')
        CARD = 'card', _('Card')
        UPI = 'upi', _('UPI')
        CHEQUE = 'cheque', _('Cheque')

    invoice = models.ForeignKey(Invoice, related_name='payments', on_delete=models.CASCADE)
    method = models.CharField(
        _('payment method'),
        max_length=50,
        choices=Method.choices,
        default=Method.BANK_TRANSFER,
        db_column='payment_method',
        db_index=True,
    )
    amount = models.DecimalField(
        _('amount ($)'),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    date = models.DateField(
        _('payment date'),
        default=timezone.now,
        db_column='payment_date',
        db_index=True,
    )
    note = models.TextField(_('notes'), blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recorded_payments'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('payment')
        verbose_name_plural = _('payments')
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"Payment of ${self.amount} for {self.invoice.invoice_number} ({self.get_method_display()})"

    @property
    def payment_date(self):
        return self.date

    @property
    def payment_method(self):
        return self.method

    def get_payment_method_display(self):
        return self.get_method_display()

    def clean(self):
        super().clean()
        if self.amount is not None:
            if self.amount <= Decimal('0.00'):
                raise ValidationError({'amount': _("Payment amount must be greater than zero.")})
            if hasattr(self, 'invoice') and self.invoice:
                if self.invoice.status != Invoice.Status.CONFIRMED:
                    raise ValidationError(_("Payments can only be recorded on Confirmed invoices."))
                from django.db.models import Sum
                current_paid = self.invoice.payments.exclude(pk=self.pk).aggregate(
                    total=Sum('amount')
                )['total'] or Decimal('0.00')
                remaining = self.invoice.grand_total - current_paid
                if self.amount > remaining:
                    raise ValidationError({
                        'amount': _(f"Overpayment not allowed. Payment amount (${self.amount}) exceeds remaining balance (${remaining}).")
                    })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        if self.invoice:
            from django.db.models import Sum
            total_paid = self.invoice.payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            if total_paid >= self.invoice.grand_total:
                if self.invoice.status != Invoice.Status.PAID:
                    self.invoice.status = Invoice.Status.PAID
                    self.invoice.save(update_fields=['status', 'updated_at'])


