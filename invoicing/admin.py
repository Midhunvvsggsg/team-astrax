from django.contrib import admin

from .models import BankStatement, BankStatementRow, Invoice, InvoiceLine


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    readonly_fields = ('description', 'product', 'variant', 'quantity', 'unit_price', 'tax', 'tax_amount', 'discount_amount', 'amount')
    can_delete = False

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'invoice_number', 'customer', 'subscription',
        'period_start', 'period_end', 'due_date',
        'subtotal', 'discount_total', 'tax_total', 'grand_total',
        'status', 'is_overdue'
    )
    list_filter = ('status', 'issue_date', 'due_date')
    search_fields = ('invoice_number', 'customer__email', 'subscription__subscription_number')
    readonly_fields = ('invoice_number', 'subtotal', 'discount_total', 'tax_total', 'grand_total', 'created_at', 'updated_at')
    inlines = [InvoiceLineInline]


class BankStatementRowInline(admin.TabularInline):
    model = BankStatementRow
    extra = 0
    readonly_fields = ('row_number', 'date', 'amount', 'reference', 'confidence', 'status', 'matched_invoice', 'payment', 'row_hash')
    can_delete = False


@admin.register(BankStatement)
class BankStatementAdmin(admin.ModelAdmin):
    list_display = ('filename', 'uploaded_at', 'uploaded_by', 'total_rows', 'auto_reconciled_count', 'needs_review_count', 'unmatched_count', 'duplicate_count')
    readonly_fields = ('uploaded_at', 'total_rows', 'auto_reconciled_count', 'needs_review_count', 'unmatched_count', 'duplicate_count')
    inlines = [BankStatementRowInline]

