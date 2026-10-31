from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Product(models.Model):
    class ProductType(models.TextChoices):
        SOFTWARE = 'software', _('Software')
        SERVICE = 'service', _('Service')
        PHYSICAL = 'physical', _('Physical')
        ADDON = 'addon', _('Add-on')

    name = models.CharField(_('name'), max_length=255, db_index=True)
    product_type = models.CharField(
        _('type'),
        max_length=50,
        choices=ProductType.choices,
        default=ProductType.SOFTWARE,
        db_index=True,
    )
    sales_price = models.DecimalField(
        _('sales price'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    cost_price = models.DecimalField(
        _('cost price'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    notes = models.TextField(_('notes'), blank=True)
    is_active = models.BooleanField(_('active'), default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('product')
        verbose_name_plural = _('products')
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['product_type', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_product_type_display()}) - ${self.sales_price}"

    def clean(self):
        super().clean()
        if self.sales_price is not None and self.sales_price < Decimal('0.00'):
            raise ValidationError({'sales_price': _("Sales price must be non-negative.")})
        if self.cost_price is not None and self.cost_price < Decimal('0.00'):
            raise ValidationError({'cost_price': _("Cost price must be non-negative.")})


class ProductVariant(models.Model):
    product = models.ForeignKey(Product, related_name='variants', on_delete=models.CASCADE)
    attribute = models.CharField(_('attribute'), max_length=100) # e.g. Brand, Storage, Tier
    value = models.CharField(_('value'), max_length=100)         # e.g. Odoo, 256GB, Premium
    extra_price = models.DecimalField(
        _('extra price'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text=_("Amount added to base sales price (e.g. Brand=Odoo adds 560.00).")
    )
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        verbose_name = _('product variant')
        verbose_name_plural = _('product variants')
        ordering = ['attribute', 'value']
        constraints = [
            models.UniqueConstraint(fields=['product', 'attribute', 'value'], name='unique_product_variant')
        ]
        indexes = [
            models.Index(fields=['product', 'attribute']),
        ]

    def __str__(self):
        return f"{self.product.name} [{self.attribute}: {self.value}] (+${self.extra_price})"

    @property
    def final_price(self):
        """Final price = product sales price + variant extra price"""
        base = self.product.sales_price if self.product else Decimal('0.00')
        return base + (self.extra_price or Decimal('0.00'))

    def clean(self):
        super().clean()
        if self.extra_price is not None and self.extra_price < Decimal('0.00'):
            raise ValidationError({'extra_price': _("Extra price cannot be negative.")})


class Tax(models.Model):
    name = models.CharField(_('name'), max_length=100, db_index=True)
    percentage = models.DecimalField(
        _('percentage (%)'),
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    fixed_amount = models.DecimalField(
        _('fixed amount ($)'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    is_active = models.BooleanField(_('active'), default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('tax')
        verbose_name_plural = _('taxes')
        ordering = ['name']
        indexes = [
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        if self.percentage and self.fixed_amount:
            return f"{self.name} ({self.percentage}% + ${self.fixed_amount})"
        elif self.percentage:
            return f"{self.name} ({self.percentage}%)"
        return f"{self.name} (${self.fixed_amount})"

    def calculate_tax(self, amount):
        """Calculates tax for a given amount using percentage and fixed amount."""
        base_amount = Decimal(str(amount))
        pct_tax = (base_amount * self.percentage) / Decimal('100.00')
        total_tax = pct_tax + self.fixed_amount
        return total_tax.quantize(Decimal('0.01'))

    def clean(self):
        super().clean()
        if self.percentage is not None and self.percentage < Decimal('0.00'):
            raise ValidationError({'percentage': _("Tax percentage must be non-negative.")})
        if self.fixed_amount is not None and self.fixed_amount < Decimal('0.00'):
            raise ValidationError({'fixed_amount': _("Fixed tax amount must be non-negative.")})


class Plan(models.Model):
    class BillingPeriod(models.TextChoices):
        DAILY = 'daily', _('Daily')
        WEEKLY = 'weekly', _('Weekly')
        MONTHLY = 'monthly', _('Monthly')
        YEARLY = 'yearly', _('Yearly')

    name = models.CharField(_('plan name'), max_length=255, db_index=True)
    price = models.DecimalField(
        _('price'),
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    billing_period = models.CharField(
        _('billing period'),
        max_length=20,
        choices=BillingPeriod.choices,
        default=BillingPeriod.MONTHLY,
        db_index=True,
    )
    minimum_quantity = models.PositiveIntegerField(
        _('minimum quantity'),
        default=1,
        validators=[MinValueValidator(1)],
    )
    available_from = models.DateField(_('available from'), null=True, blank=True)
    available_to = models.DateField(_('available to'), null=True, blank=True)

    # Per-plan lifecycle flags
    auto_close_on_end = models.BooleanField(
        _('auto close on end'),
        default=False,
        help_text=_("Close subscription automatically when end date is reached.")
    )
    closable_early = models.BooleanField(
        _('closable early'),
        default=True,
        help_text=_("Allow customers or staff to cancel before term end.")
    )
    pausable = models.BooleanField(
        _('pausable'),
        default=True,
        help_text=_("Allow subscription to be paused and resumed.")
    )
    renewable = models.BooleanField(
        _('renewable'),
        default=True,
        help_text=_("Allow subscription renewal at the end of period.")
    )

    is_active = models.BooleanField(_('active'), default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('plan')
        verbose_name_plural = _('plans')
        ordering = ['name']
        indexes = [
            models.Index(fields=['billing_period', 'is_active']),
            models.Index(fields=['available_from', 'available_to']),
        ]

    def __str__(self):
        return f"{self.name} (${self.price}/{self.get_billing_period_display()})"

    def clean(self):
        super().clean()
        if self.price is not None and self.price < Decimal('0.00'):
            raise ValidationError({'price': _("Plan price must be non-negative.")})
        if self.minimum_quantity is not None and self.minimum_quantity < 1:
            raise ValidationError({'minimum_quantity': _("Minimum quantity must be at least 1.")})
        if self.available_from and self.available_to:
            if self.available_to < self.available_from:
                raise ValidationError({
                    'available_to': _("Available To date must be on or after Available From date.")
                })
