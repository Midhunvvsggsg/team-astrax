from decimal import Decimal
from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .models import Payment

class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['amount', 'payment_date', 'payment_method', 'note']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0.01', 'placeholder': '0.00'}),
            'payment_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'payment_method': forms.Select(attrs={'class': 'form-select'}),
            'note': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Check #, bank reference, transaction id...'}),
        }

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        if invoice and not self.initial.get('amount'):
            self.initial['amount'] = invoice.amount_due
        if not self.initial.get('payment_date'):
            self.initial['payment_date'] = timezone.now().date()

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if not amount or amount <= Decimal('0.00'):
            raise forms.ValidationError(_("Payment amount must be greater than zero."))
        if self.invoice and amount > self.invoice.amount_due:
            raise forms.ValidationError(
                _(f"Payment amount (${amount}) exceeds outstanding invoice balance (${self.invoice.amount_due}).")
            )
        return amount
