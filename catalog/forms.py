from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import Plan, Product, ProductVariant, Tax


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'product_type', 'sales_price', 'cost_price', 'notes', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. BrightSuite CRM'}),
            'product_type': forms.Select(attrs={'class': 'form-select'}),
            'sales_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'cost_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3, 'placeholder': 'Product specifications and notes'}),
            'is_active': forms.CheckboxInput(attrs={'style': 'margin-right: 8px;'}),
        }

    def clean_sales_price(self):
        price = self.cleaned_data.get('sales_price')
        if price is not None and price < Decimal('0.00'):
            raise ValidationError(_("Sales price cannot be negative."))
        return price

    def clean_cost_price(self):
        cost = self.cleaned_data.get('cost_price')
        if cost is not None and cost < Decimal('0.00'):
            raise ValidationError(_("Cost price cannot be negative."))
        return cost


class ProductVariantForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = ['attribute', 'value', 'extra_price', 'is_active']
        widgets = {
            'attribute': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Brand, Tier, Storage'}),
            'value': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Odoo, Enterprise, 500GB'}),
            'extra_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'is_active': forms.CheckboxInput(attrs={'style': 'margin-right: 8px;'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['extra_price'].required = False

    def clean_extra_price(self):
        extra = self.cleaned_data.get('extra_price')
        if extra is None:
            return Decimal('0.00')
        if extra < Decimal('0.00'):
            raise ValidationError(_("Extra price cannot be negative."))
        return extra


class TaxForm(forms.ModelForm):
    class Meta:
        model = Tax
        fields = ['name', 'percentage', 'fixed_amount', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. VAT 18%'}),
            'percentage': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0', 'max': '100'}),
            'fixed_amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'is_active': forms.CheckboxInput(attrs={'style': 'margin-right: 8px;'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['percentage'].required = False
        self.fields['fixed_amount'].required = False

    def clean_percentage(self):
        pct = self.cleaned_data.get('percentage')
        if pct is None:
            return Decimal('0.00')
        if pct < Decimal('0.00'):
            raise ValidationError(_("Tax percentage cannot be negative."))
        return pct

    def clean_fixed_amount(self):
        amt = self.cleaned_data.get('fixed_amount')
        if amt is None:
            return Decimal('0.00')
        if amt < Decimal('0.00'):
            raise ValidationError(_("Fixed amount cannot be negative."))
        return amt

    def clean(self):
        cleaned_data = super().clean()
        pct = cleaned_data.get('percentage') or Decimal('0.00')
        fixed = cleaned_data.get('fixed_amount') or Decimal('0.00')
        if pct == Decimal('0.00') and fixed == Decimal('0.00'):
            raise ValidationError(_("Please specify at least a tax percentage or a fixed amount."))
        return cleaned_data


class PlanForm(forms.ModelForm):
    class Meta:
        model = Plan
        fields = [
            'name', 'price', 'billing_period', 'minimum_quantity',
            'available_from', 'available_to',
            'auto_close_on_end', 'closable_early', 'pausable', 'renewable',
            'is_active'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Professional Monthly'}),
            'price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'billing_period': forms.Select(attrs={'class': 'form-select'}),
            'minimum_quantity': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'available_from': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'available_to': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'auto_close_on_end': forms.CheckboxInput(attrs={'style': 'margin-right: 8px;'}),
            'closable_early': forms.CheckboxInput(attrs={'style': 'margin-right: 8px;'}),
            'pausable': forms.CheckboxInput(attrs={'style': 'margin-right: 8px;'}),
            'renewable': forms.CheckboxInput(attrs={'style': 'margin-right: 8px;'}),
            'is_active': forms.CheckboxInput(attrs={'style': 'margin-right: 8px;'}),
        }

    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is not None and price < Decimal('0.00'):
            raise ValidationError(_("Price cannot be negative."))
        return price

    def clean_minimum_quantity(self):
        qty = self.cleaned_data.get('minimum_quantity')
        if qty is not None and qty < 1:
            raise ValidationError(_("Minimum quantity must be at least 1."))
        return qty

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get('available_from')
        end = cleaned_data.get('available_to')

        if start and end and end < start:
            self.add_error('available_to', _("Available To date must be on or after Available From date."))

        return cleaned_data
