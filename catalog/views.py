from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import admin_required, staff_or_admin_required

from .forms import PlanForm, ProductForm, ProductVariantForm, TaxForm
from .models import Plan, Product, ProductVariant, Tax

# ==============================================================================
# Products & Variants Views
# ==============================================================================

@staff_or_admin_required
def product_list(request):
    """View products list with search, filter, and pagination (Staff & Admin)."""
    search_query = request.GET.get('q', '').strip()
    type_filter = request.GET.get('type', '').strip()
    status_filter = request.GET.get('status', '').strip()

    products = Product.objects.prefetch_related('variants').all()

    if search_query:
        products = products.filter(
            Q(name__icontains=search_query) | Q(notes__icontains=search_query)
        )
    if type_filter:
        products = products.filter(product_type=type_filter)
    if status_filter:
        is_active = status_filter == 'active'
        products = products.filter(is_active=is_active)

    paginator = Paginator(products, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'catalog/product_list.html', {
        'page_obj': page_obj,
        'search_query': search_query,
        'type_filter': type_filter,
        'status_filter': status_filter,
        'product_types': Product.ProductType.choices,
    })

@staff_or_admin_required
def product_detail(request, pk):
    """Detailed view of product and its variants with calculated final prices."""
    product = get_object_or_404(Product.objects.prefetch_related('variants'), pk=pk)
    variant_form = ProductVariantForm() if request.user.is_admin else None

    return render(request, 'catalog/product_detail.html', {
        'product': product,
        'variants': product.variants.all(),
        'variant_form': variant_form,
    })

@admin_required
def product_create(request):
    """Create a new product (Admin only)."""
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save()
            messages.success(request, f"Product '{product.name}' created successfully.")
            return redirect('catalog:product_detail', pk=product.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ProductForm()

    return render(request, 'catalog/product_form.html', {
        'form': form,
        'title': 'Create Product',
    })

@admin_required
def product_edit(request, pk):
    """Edit existing product (Admin only)."""
    product = get_object_or_404(Product, pk=pk)

    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            product = form.save()
            messages.success(request, f"Product '{product.name}' updated successfully.")
            return redirect('catalog:product_detail', pk=product.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ProductForm(instance=product)

    return render(request, 'catalog/product_form.html', {
        'form': form,
        'product': product,
        'title': f"Edit Product: {product.name}",
    })

@admin_required
def product_delete(request, pk):
    """Delete a product (Admin only)."""
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        name = product.name
        product.delete()
        messages.success(request, f"Product '{name}' was deleted.")
        return redirect('catalog:product_list')

    return render(request, 'catalog/confirm_delete.html', {
        'object_name': product.name,
        'cancel_url': 'catalog:product_detail',
        'cancel_pk': product.pk,
    })

@admin_required
def variant_create(request, product_pk):
    """Add a variant to a product (Admin only)."""
    product = get_object_or_404(Product, pk=product_pk)

    if request.method == 'POST':
        form = ProductVariantForm(request.POST)
        if form.is_valid():
            variant = form.save(commit=False)
            variant.product = product
            variant.save()
            messages.success(request, f"Variant '{variant.attribute}: {variant.value}' added (+${variant.extra_price}).")
        else:
            for field, errors in form.errors.items():
                for err in errors:
                    messages.error(request, f"{field.capitalize()}: {err}")

    return redirect('catalog:product_detail', pk=product.pk)

@admin_required
def variant_edit(request, pk):
    """Edit an existing variant (Admin only)."""
    variant = get_object_or_404(ProductVariant, pk=pk)

    if request.method == 'POST':
        form = ProductVariantForm(request.POST, instance=variant)
        if form.is_valid():
            variant = form.save()
            messages.success(request, f"Variant '{variant.attribute}: {variant.value}' updated.")
            return redirect('catalog:product_detail', pk=variant.product.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ProductVariantForm(instance=variant)

    return render(request, 'catalog/variant_form.html', {
        'form': form,
        'variant': variant,
        'product': variant.product,
    })

@admin_required
def variant_delete(request, pk):
    """Delete a variant (Admin only)."""
    variant = get_object_or_404(ProductVariant, pk=pk)
    product_pk = variant.product.pk
    if request.method == 'POST':
        variant.delete()
        messages.success(request, "Variant removed.")
        return redirect('catalog:product_detail', pk=product_pk)

    return render(request, 'catalog/confirm_delete.html', {
        'object_name': f"Variant {variant.attribute}: {variant.value}",
        'cancel_url': 'catalog:product_detail',
        'cancel_pk': product_pk,
    })

# ==============================================================================
# Tax Views
# ==============================================================================

@staff_or_admin_required
def tax_list(request):
    """View taxes list with search and pagination (Staff & Admin)."""
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()

    taxes = Tax.objects.all()

    if search_query:
        taxes = taxes.filter(name__icontains=search_query)
    if status_filter:
        is_active = status_filter == 'active'
        taxes = taxes.filter(is_active=is_active)

    paginator = Paginator(taxes, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'catalog/tax_list.html', {
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
    })

@admin_required
def tax_create(request):
    """Create a new tax rule (Admin only)."""
    if request.method == 'POST':
        form = TaxForm(request.POST)
        if form.is_valid():
            tax = form.save()
            messages.success(request, f"Tax rule '{tax.name}' created.")
            return redirect('catalog:tax_list')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = TaxForm()

    return render(request, 'catalog/tax_form.html', {
        'form': form,
        'title': 'Create Tax Rule',
    })

@admin_required
def tax_edit(request, pk):
    """Edit existing tax rule (Admin only)."""
    tax = get_object_or_404(Tax, pk=pk)

    if request.method == 'POST':
        form = TaxForm(request.POST, instance=tax)
        if form.is_valid():
            tax = form.save()
            messages.success(request, f"Tax rule '{tax.name}' updated.")
            return redirect('catalog:tax_list')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = TaxForm(instance=tax)

    return render(request, 'catalog/tax_form.html', {
        'form': form,
        'tax': tax,
        'title': f"Edit Tax: {tax.name}",
    })

@admin_required
def tax_delete(request, pk):
    """Delete a tax rule (Admin only)."""
    tax = get_object_or_404(Tax, pk=pk)
    if request.method == 'POST':
        tax.delete()
        messages.success(request, "Tax rule deleted.")
        return redirect('catalog:tax_list')

    return render(request, 'catalog/confirm_delete.html', {
        'object_name': f"Tax Rule {tax.name}",
        'cancel_url': 'catalog:tax_list',
    })

# ==============================================================================
# Plan Views
# ==============================================================================

@staff_or_admin_required
def plan_list(request):
    """View plans list with search, period filter, and pagination (Staff & Admin)."""
    search_query = request.GET.get('q', '').strip()
    period_filter = request.GET.get('period', '').strip()
    status_filter = request.GET.get('status', '').strip()

    plans = Plan.objects.all()

    if search_query:
        plans = plans.filter(name__icontains=search_query)
    if period_filter:
        plans = plans.filter(billing_period=period_filter)
    if status_filter:
        is_active = status_filter == 'active'
        plans = plans.filter(is_active=is_active)

    paginator = Paginator(plans, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'catalog/plan_list.html', {
        'page_obj': page_obj,
        'search_query': search_query,
        'period_filter': period_filter,
        'status_filter': status_filter,
        'billing_periods': Plan.BillingPeriod.choices,
    })

@admin_required
def plan_create(request):
    """Create a new subscription plan (Admin only)."""
    if request.method == 'POST':
        form = PlanForm(request.POST)
        if form.is_valid():
            plan = form.save()
            messages.success(request, f"Plan '{plan.name}' created successfully.")
            return redirect('catalog:plan_list')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = PlanForm()

    return render(request, 'catalog/plan_form.html', {
        'form': form,
        'title': 'Create Subscription Plan',
    })

@admin_required
def plan_edit(request, pk):
    """Edit existing plan (Admin only)."""
    plan = get_object_or_404(Plan, pk=pk)

    if request.method == 'POST':
        form = PlanForm(request.POST, instance=plan)
        if form.is_valid():
            plan = form.save()
            messages.success(request, f"Plan '{plan.name}' updated successfully.")
            return redirect('catalog:plan_list')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = PlanForm(instance=plan)

    return render(request, 'catalog/plan_form.html', {
        'form': form,
        'plan': plan,
        'title': f"Edit Plan: {plan.name}",
    })

@admin_required
def plan_delete(request, pk):
    """Delete a plan (Admin only)."""
    plan = get_object_or_404(Plan, pk=pk)
    if request.method == 'POST':
        plan.delete()
        messages.success(request, f"Plan '{plan.name}' deleted.")
        return redirect('catalog:plan_list')

    return render(request, 'catalog/confirm_delete.html', {
        'object_name': f"Plan {plan.name}",
        'cancel_url': 'catalog:plan_list',
    })
