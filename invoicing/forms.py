from decimal import Decimal
from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .models import Payment, Invoice

class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['amount', 'date', 'method', 'note']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0.01', 'placeholder': '0.00'}),
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'method': forms.Select(attrs={'class': 'form-select'}),
            'note': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Bank ref, UPI ID, card auth, cheque #...'}),
        }

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        if invoice and not self.initial.get('amount'):
            self.initial['amount'] = invoice.amount_due
        if not self.initial.get('date'):
            self.initial['date'] = timezone.now().date()

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get('amount')
        if not self.invoice:
            return cleaned_data

        if self.invoice.status != Invoice.Status.CONFIRMED:
            raise forms.ValidationError(_("Payments can only be recorded on Confirmed invoices."))

        if amount is not None:
            if amount <= Decimal('0.00'):
                self.add_error('amount', _("Payment amount must be greater than zero."))
            elif amount > self.invoice.amount_due:
                self.add_error(
                    'amount',
                    _(f"Overpayment not allowed. Payment amount (${amount}) exceeds remaining balance (${self.invoice.amount_due}).")
                )
        return cleaned_data
