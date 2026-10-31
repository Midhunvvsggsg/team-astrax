from decimal import Decimal
from datetime import date, timedelta
from django.db import models, transaction
from django.utils import timezone
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.conf import settings

class QuotationTemplate(models.Model):
    name = models.CharField(_('template name'), max_length=255, unique=True, db_index=True)
    plan = models.ForeignKey(
        'catalog.Plan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quotation_templates',
        help_text=_("Associated plan for recurring billing structure")
    )
    validity_days = models.PositiveIntegerField(
        _('validity (days)'),
        default=30,
        validators=[MinValueValidator(1)],
        help_text=_("Number of days quotations created from this template remain valid.")
    )
    is_active = models.BooleanField(_('active'), default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('quotation template')
        verbose_name_plural = _('quotation templates')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} (Valid: {self.validity_days}d)"


class QuotationTemplateLine(models.Model):
    template = models.ForeignKey(QuotationTemplate, related_name='lines', on_delete=models.CASCADE)
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    variant = models.ForeignKey('catalog.ProductVariant', on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text=_("Leave blank to automatically use product/variant final sales price.")
    )
    tax = models.ForeignKey('catalog.Tax', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        verbose_name = _('quotation template line')
        verbose_name_plural = _('quotation template lines')

    def __str__(self):
        variant_str = f" [{self.variant.attribute}: {self.variant.value}]" if self.variant else ""
        return f"{self.quantity}x {self.product.name}{variant_str}"

    def save(self, *args, **kwargs):
        if self.unit_price is None:
            if self.variant:
                self.unit_price = self.variant.final_price
            elif self.product:
                self.unit_price = self.product.sales_price
            else:
                self.unit_price = Decimal('0.00')
        super().save(*args, **kwargs)


class Subscription(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Draft')
        QUOTATION = 'quotation', _('Quotation')
        CONFIRMED = 'confirmed', _('Confirmed')
        ACTIVE = 'active', _('Active')
        PAUSED = 'paused', _('Paused')
        CLOSED = 'closed', _('Closed')

    class PaymentTerms(models.TextChoices):
        IMMEDIATE = 'immediate', _('Immediate Payment')
        DAYS_15 = '15_days', _('15 Days')
        DAYS_30 = '30_days', _('30 Days')
        NET_60 = 'net_60', _('Net 60 Days')

    subscription_number = models.CharField(
        _('subscription number'),
        max_length=50,
        unique=True,
        db_index=True,
        editable=False,
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='subscriptions',
        limit_choices_to={'role': 'customer'},
        db_index=True,
    )
    plan = models.ForeignKey(
        'catalog.Plan',
        on_delete=models.PROTECT,
        related_name='subscriptions',
        db_index=True,
    )
    start_date = models.DateField(_('start date'), default=timezone.now)
    expiry_date = models.DateField(_('expiry date'), null=True, blank=True)
    payment_terms = models.CharField(
        _('payment terms'),
        max_length=50,
        choices=PaymentTerms.choices,
        default=PaymentTerms.IMMEDIATE,
    )
    status = models.CharField(
        _('status'),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_subscriptions',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('subscription')
        verbose_name_plural = _('subscriptions')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'expiry_date']),
            models.Index(fields=['customer', 'status']),
            models.Index(fields=['plan', 'status']),
        ]

    def __str__(self):
        return f"{self.subscription_number} - {self.customer.email} ({self.get_status_display()})"

    @classmethod
    def generate_next_number(cls):
        """Generates sequential number like SUB-00001."""
        last_sub = cls.objects.order_by('-id').first()
        if last_sub and last_sub.subscription_number.startswith('SUB-'):
            try:
                num = int(last_sub.subscription_number.split('-')[1]) + 1
            except (IndexError, ValueError):
                num = cls.objects.count() + 1
        else:
            num = cls.objects.count() + 1
        return f"SUB-{num:05d}"

    def save(self, *args, **kwargs):
        if not self.subscription_number:
            self.subscription_number = self.generate_next_number()
        super().save(*args, **kwargs)

    @property
    def total_quantity(self):
        return sum(line.quantity for line in self.lines.all())

    @property
    def subtotal(self):
        return sum(line.line_subtotal for line in self.lines.all())

    @property
    def total_tax(self):
        return sum(line.line_tax_amount for line in self.lines.all())

    @property
    def total_amount(self):
        return (self.subtotal + self.total_tax).quantize(Decimal('0.01'))

    @property
    def is_expired(self):
        if self.expiry_date:
            return date.today() > self.expiry_date
        return False

    def validate_minimum_quantity(self):
        """Enforces plan's minimum quantity across lines."""
        if self.pk:
            total_qty = self.total_quantity
            if self.plan and total_qty < self.plan.minimum_quantity:
                raise ValidationError(
                    _(f"Plan '{self.plan.name}' requires a minimum quantity of {self.plan.minimum_quantity}. "
                      f"Current total quantity is {total_qty}.")
                )

    def can_transition_to(self, new_status):
        """
        Validates whether transition to new_status is legal.
        Returns (is_valid: bool, error_message: str).
        """
        curr = self.status
        today = date.today()

        # Same status -> no-op
        if curr == new_status:
            return True, ""

        # Draft -> Quotation, Confirmed, Closed
        if curr == self.Status.DRAFT:
            if new_status in [self.Status.QUOTATION, self.Status.CONFIRMED, self.Status.CLOSED]:
                return True, ""
            return False, _(f"Cannot transition Draft directly to {new_status.capitalize()}. Must move to Quotation or Confirmed first.")

        # Quotation -> Confirmed, Closed
        if curr == self.Status.QUOTATION:
            if new_status in [self.Status.CONFIRMED, self.Status.CLOSED]:
                return True, ""
            return False, _(f"Quotation can only transition to Confirmed or Closed, not {new_status.capitalize()}.")

        # Confirmed -> Active, Closed
        if curr == self.Status.CONFIRMED:
            if new_status in [self.Status.ACTIVE, self.Status.CLOSED]:
                return True, ""
            return False, _(f"Confirmed subscription can only transition to Active or Closed, not {new_status.capitalize()}.")

        # Active -> Paused, Closed
        if curr == self.Status.ACTIVE:
            if new_status == self.Status.PAUSED:
                if not self.plan.pausable:
                    return False, _(f"Plan '{self.plan.name}' does not allow pausing (pausable flag is disabled).")
                return True, ""

            if new_status == self.Status.CLOSED:
                # Check early-close restriction
                if self.expiry_date and today < self.expiry_date:
                    if not self.plan.closable_early:
                        return False, _(f"Plan '{self.plan.name}' does not permit early closure before {self.expiry_date}.")
                return True, ""

            return False, _(f"Active subscription cannot transition to {new_status.capitalize()}.")

        # Paused -> Active, Closed
        if curr == self.Status.PAUSED:
            if new_status in [self.Status.ACTIVE, self.Status.CLOSED]:
                return True, ""
            return False, _(f"Paused subscription can only resume to Active or be Closed.")

        # Closed -> No further transitions except renewal
        if curr == self.Status.CLOSED:
            return False, _("A Closed subscription cannot be reopened directly. Please use Renew if the plan permits.")

        return False, _(f"Illegal status transition from {curr} to {new_status}.")

    @transaction.atomic
    def transition_to(self, new_status, user=None, notes=""):
        """Executes status change, logs history, and enforces business rules."""
        is_valid, msg = self.can_transition_to(new_status)
        if not is_valid:
            raise ValidationError(msg)

        old_status = self.status
        self.status = new_status
        self.save()

        # Log history
        SubscriptionStatusHistory.objects.create(
            subscription=self,
            from_status=old_status,
            to_status=new_status,
            changed_by=user if user and user.is_authenticated else None,
            notes=notes,
        )

    @transaction.atomic
    def renew(self, user=None, extension_days=None, notes=""):
        """Renews subscription if plan.renewable is allowed."""
        if not self.plan.renewable:
            raise ValidationError(_(f"Plan '{self.plan.name}' cannot be renewed (renewable flag is False)."))

        # Calculate extension
        if extension_days:
            days = extension_days
        elif self.plan.billing_period == 'daily':
            days = 1
        elif self.plan.billing_period == 'weekly':
            days = 7
        elif self.plan.billing_period == 'monthly':
            days = 30
        elif self.plan.billing_period == 'yearly':
            days = 365
        else:
            days = 30

        base_date = max(self.expiry_date or date.today(), date.today())
        self.expiry_date = base_date + timedelta(days=days)
        old_status = self.status
        self.status = self.Status.ACTIVE
        self.save()

        SubscriptionStatusHistory.objects.create(
            subscription=self,
            from_status=old_status,
            to_status=self.Status.ACTIVE,
            changed_by=user if user and user.is_authenticated else None,
            notes=f"Renewed for {days} days until {self.expiry_date}. {notes}".strip(),
        )


class SubscriptionLine(models.Model):
    subscription = models.ForeignKey(Subscription, related_name='lines', on_delete=models.CASCADE)
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    variant = models.ForeignKey('catalog.ProductVariant', on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    tax = models.ForeignKey('catalog.Tax', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        verbose_name = _('subscription line')
        verbose_name_plural = _('subscription lines')

    def __str__(self):
        variant_str = f" [{self.variant.attribute}: {self.variant.value}]" if self.variant else ""
        return f"{self.quantity}x {self.product.name}{variant_str} @ ${self.unit_price}"

    @property
    def line_subtotal(self):
        return (Decimal(self.quantity) * self.unit_price).quantize(Decimal('0.01'))

    @property
    def line_tax_amount(self):
        if self.tax:
            return self.tax.calculate_tax(self.line_subtotal)
        return Decimal('0.00')

    @property
    def line_total(self):
        return (self.line_subtotal + self.line_tax_amount).quantize(Decimal('0.01'))

    def save(self, *args, **kwargs):
        if self.unit_price is None:
            if self.variant:
                self.unit_price = self.variant.final_price
            elif self.product:
                self.unit_price = self.product.sales_price
            else:
                self.unit_price = Decimal('0.00')
        super().save(*args, **kwargs)


class SubscriptionStatusHistory(models.Model):
    subscription = models.ForeignKey(Subscription, related_name='status_history', on_delete=models.CASCADE)
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    changed_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _('status history')
        verbose_name_plural = _('status histories')
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.from_status} → {self.to_status} at {self.changed_at.strftime('%Y-%m-%d %H:%M')}"
