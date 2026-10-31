from django.contrib import admin

from .models import (
    DiscountRule,
    QuotationTemplate,
    QuotationTemplateLine,
    Subscription,
    SubscriptionLine,
    SubscriptionStatusHistory,
)


class QuotationTemplateLineInline(admin.TabularInline):
    model = QuotationTemplateLine
    extra = 1

@admin.register(QuotationTemplate)
class QuotationTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'plan', 'validity_days', 'is_active', 'created_at')
    list_filter = ('is_active', 'plan')
    search_fields = ('name',)
    inlines = [QuotationTemplateLineInline]

class SubscriptionLineInline(admin.TabularInline):
    model = SubscriptionLine
    extra = 1

class SubscriptionStatusHistoryInline(admin.TabularInline):
    model = SubscriptionStatusHistory
    extra = 0
    readonly_fields = ('from_status', 'to_status', 'changed_by', 'changed_at', 'notes')
    can_delete = False

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('subscription_number', 'customer', 'plan', 'status', 'start_date', 'expiry_date', 'total_amount')
    list_filter = ('status', 'plan', 'payment_terms')
    search_fields = ('subscription_number', 'customer__email', 'plan__name')
    readonly_fields = ('subscription_number', 'created_at', 'updated_at')
    inlines = [SubscriptionLineInline, SubscriptionStatusHistoryInline]

@admin.register(DiscountRule)
class DiscountRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'discount_type', 'value', 'scope', 'times_used', 'usage_limit', 'is_active')
    list_filter = ('discount_type', 'scope', 'is_active')
    search_fields = ('name',)
    filter_horizontal = ('products',)
