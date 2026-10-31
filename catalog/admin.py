from django.contrib import admin
from .models import Product, ProductVariant, Tax, Plan

class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'product_type', 'sales_price', 'cost_price', 'is_active', 'created_at')
    list_filter = ('product_type', 'is_active')
    search_fields = ('name', 'notes')
    inlines = [ProductVariantInline]

@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ('product', 'attribute', 'value', 'extra_price', 'final_price', 'is_active')
    list_filter = ('attribute', 'is_active')
    search_fields = ('product__name', 'attribute', 'value')

@admin.register(Tax)
class TaxAdmin(admin.ModelAdmin):
    list_display = ('name', 'percentage', 'fixed_amount', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)

@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'billing_period', 'minimum_quantity', 'available_from', 'available_to', 'is_active')
    list_filter = ('billing_period', 'is_active')
    search_fields = ('name',)
