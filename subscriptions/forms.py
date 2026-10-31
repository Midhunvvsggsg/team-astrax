from decimal import Decimal
from datetime import date
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from accounts.models import User
from catalog.models import Product, ProductVariant, Plan, Tax
from .models import QuotationTemplate, QuotationTemplateLine, Subscription, SubscriptionLine, DiscountRule

class QuotationTemplateForm(forms.ModelForm):
    class Meta:
        model = QuotationTemplate
        fields = ['name', 'plan', 'validity_days', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Standard SaaS Quote'}),
            'plan': forms.Select(attrs={'class': 'form-select'}),
            'validity_days': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'is_active': forms.CheckboxInput(attrs={'style': 'margin-right: 8px;'}),
        }

    def clean_validity_days(self):
        val = self.cleaned_data.get('validity_days')
        if val is not None and val < 1:
            raise ValidationError(_("Validity days must be at least 1."))
        return val


class QuotationTemplateLineForm(forms.ModelForm):
    class Meta:
        model = QuotationTemplateLine
        fields = ['product', 'variant', 'quantity', 'unit_price', 'tax']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'variant': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'unit_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'tax': forms.Select(attrs={'class': 'form-select'}),
        }

    def clean_quantity(self):
        qty = self.cleaned_data.get('quantity')
        if qty is not None and qty < 1:
            raise ValidationError(_("Quantity must be at least 1."))
        return qty

    def clean_unit_price(self):
        price = self.cleaned_data.get('unit_price')
        if price is not None and price < Decimal('0.00'):
            raise ValidationError(_("Unit price cannot be negative."))
        return price


class SubscriptionForm(forms.ModelForm):
    class Meta:
        model = Subscription
        fields = ['customer', 'plan', 'start_date', 'expiry_date', 'payment_terms', 'status']
        widgets = {
            'customer': forms.Select(attrs={'class': 'form-select'}),
            'plan': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'expiry_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'payment_terms': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].queryset = User.objects.filter(role=User.Role.CUSTOMER).order_by('email')
        self.fields['plan'].queryset = Plan.objects.filter(is_active=True).order_by('name')

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get('start_date')
        expiry = cleaned_data.get('expiry_date')

        if start and expiry and expiry < start:
            self.add_error('expiry_date', _("Expiry date cannot be before start date."))

        return cleaned_data


class SubscriptionLineForm(forms.ModelForm):
    class Meta:
        model = SubscriptionLine
        fields = ['product', 'variant', 'quantity', 'unit_price', 'tax']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'variant': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'unit_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'tax': forms.Select(attrs={'class': 'form-select'}),
        }

    def clean_quantity(self):
        qty = self.cleaned_data.get('quantity')
        if qty is not None and qty < 1:
            raise ValidationError(_("Quantity must be at least 1."))
        return qty

    def clean_unit_price(self):
        price = self.cleaned_data.get('unit_price')
        if price is not None and price < Decimal('0.00'):
            raise ValidationError(_("Unit price cannot be negative."))
        return price


class CreateFromTemplateForm(forms.Form):
    template = forms.ModelChoiceField(
        label=_("Quotation Template"),
        queryset=QuotationTemplate.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    customer = forms.ModelChoiceField(
        label=_("Customer Account"),
        queryset=User.objects.filter(role=User.Role.CUSTOMER).order_by('email'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    start_date = forms.DateField(
        label=_("Subscription Start Date"),
        initial=date.today,
        widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'})
    )
    payment_terms = forms.ChoiceField(
        label=_("Payment Terms"),
        choices=Subscription.PaymentTerms.choices,
        initial=Subscription.PaymentTerms.IMMEDIATE,
        widget=forms.Select(attrs={'class': 'form-select'})
    )


class DiscountRuleForm(forms.ModelForm):
    class Meta:
        model = DiscountRule
        fields = [
            'name', 'discount_type', 'value', 'min_purchase_amount', 'min_quantity',
            'start_date', 'end_date', 'usage_limit', 'scope', 'products', 'is_active'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Summer SaaS 20%'}),
            'discount_type': forms.Select(attrs={'class': 'form-select', 'id': 'id_discount_type'}),
            'value': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0.01'}),
            'min_purchase_amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'min_quantity': forms.NumberInput(attrs={'class': 'form-input', 'min': '0'}),
            'start_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'usage_limit': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'scope': forms.Select(attrs={'class': 'form-select', 'id': 'id_scope'}),
            'products': forms.SelectMultiple(attrs={'class': 'form-select', 'style': 'min-height: 120px;'}),
            'is_active': forms.CheckboxInput(attrs={'style': 'margin-right: 8px;'}),
        }

    def clean_value(self):
        val = self.cleaned_data.get('value')
        dtype = self.cleaned_data.get('discount_type')
        if val is not None:
            if val <= Decimal('0.00'):
                raise ValidationError(_("Discount value must be greater than zero."))
            if dtype == DiscountRule.DiscountType.PERCENTAGE and val > Decimal('100.00'):
                raise ValidationError(_("Percentage discount cannot exceed 100%."))
        return val

    def clean_usage_limit(self):
        limit = self.cleaned_data.get('usage_limit')
        if limit is not None and limit < 1:
            raise ValidationError(_("Usage limit must be at least 1."))
        return limit

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get('start_date')
        end = cleaned_data.get('end_date')
        scope = cleaned_data.get('scope')
        products = cleaned_data.get('products')

        if start and end and end < start:
            self.add_error('end_date', _("End date must be on or after start date."))

        if scope == DiscountRule.Scope.SPECIFIC_PRODUCTS and not products:
            self.add_error('products', _("Please select at least one product when scope is 'Specific Products'."))

        return cleaned_data
