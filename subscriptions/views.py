from datetime import date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import Q
from django.db import transaction
from accounts.decorators import staff_or_admin_required, admin_required, customer_required
from accounts.models import User
from catalog.models import Product, ProductVariant, Plan
from .models import QuotationTemplate, QuotationTemplateLine, Subscription, SubscriptionLine, SubscriptionStatusHistory, DiscountRule
from .forms import (
    QuotationTemplateForm,
    QuotationTemplateLineForm,
    SubscriptionForm,
    SubscriptionLineForm,
    CreateFromTemplateForm,
    DiscountRuleForm,
)

# ==============================================================================
# Subscription Management Views (Staff & Admin)
# ==============================================================================

@staff_or_admin_required
def subscription_list(request):
    """List subscriptions with filters (status, customer, plan) and search."""
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    customer_filter = request.GET.get('customer', '').strip()

    subscriptions = Subscription.objects.select_related('customer', 'plan', 'created_by').prefetch_related('lines__tax', 'lines__product', 'lines__variant').all()

    if search_query:
        subscriptions = subscriptions.filter(
            Q(subscription_number__icontains=search_query) |
            Q(customer__email__icontains=search_query) |
            Q(customer__first_name__icontains=search_query) |
            Q(customer__last_name__icontains=search_query) |
            Q(plan__name__icontains=search_query)
        )
    if status_filter:
        subscriptions = subscriptions.filter(status=status_filter)
    if customer_filter:
        subscriptions = subscriptions.filter(customer_id=customer_filter)

    paginator = Paginator(subscriptions, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    customers = User.objects.filter(role=User.Role.CUSTOMER).order_by('email')

    return render(request, 'subscriptions/subscription_list.html', {
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'customer_filter': customer_filter,
        'customers': customers,
        'statuses': Subscription.Status.choices,
    })

@login_required
def subscription_detail(request, pk):
    """
    Detailed view of subscription:
    - Staff & Admin can view any subscription and execute actions.
    - Customer can ONLY view their own subscription (returns 404 for other customers).
    - Exposes permitted lifecycle actions (pause, resume, renew, close) for customer.
    """

    # Enforce strict customer isolation: query strictly scoped to request.user returning 404
    if request.user.is_customer:
        subscription = get_object_or_404(
            Subscription.objects.filter(customer=request.user)
            .select_related('customer', 'plan', 'created_by')
            .prefetch_related('lines__product', 'lines__variant', 'lines__tax', 'status_history__changed_by'),
            pk=pk
        )
    else:
        subscription = get_object_or_404(
            Subscription.objects.select_related('customer', 'plan', 'created_by')
            .prefetch_related('lines__product', 'lines__variant', 'lines__tax', 'status_history__changed_by'),
            pk=pk
        )

    line_form = SubscriptionLineForm() if (request.user.is_staff_member or request.user.is_admin) and subscription.status in [Subscription.Status.DRAFT, Subscription.Status.QUOTATION] else None

    # Check which actions are available based on current status and plan flags
    available_actions = []
    customer_actions = []
    today = date.today()
    curr = subscription.status

    if request.user.is_staff_member or request.user.is_admin:
        if curr == Subscription.Status.DRAFT:
            available_actions.append(('quotation', 'Send Quotation', 'btn-primary'))
            available_actions.append(('confirmed', 'Confirm', 'btn-primary'))
            available_actions.append(('closed', 'Cancel Draft', 'btn-danger'))
        elif curr == Subscription.Status.QUOTATION:
            available_actions.append(('confirmed', 'Accept & Confirm', 'btn-primary'))
            available_actions.append(('closed', 'Reject / Close', 'btn-danger'))
        elif curr == Subscription.Status.CONFIRMED:
            available_actions.append(('active', 'Activate Subscription', 'btn-primary'))
            available_actions.append(('closed', 'Cancel', 'btn-danger'))
        elif curr == Subscription.Status.ACTIVE:
            if subscription.plan.pausable:
                available_actions.append(('paused', 'Pause Subscription', 'btn-secondary'))
            if not subscription.expiry_date or today >= subscription.expiry_date or subscription.plan.closable_early:
                available_actions.append(('closed', 'Close Subscription', 'btn-danger'))
            if subscription.plan.renewable:
                available_actions.append(('renew', 'Renew Subscription', 'btn-primary'))
        elif curr == Subscription.Status.PAUSED:
            available_actions.append(('active', 'Resume Subscription', 'btn-primary'))
            available_actions.append(('closed', 'Close Subscription', 'btn-danger'))
        elif curr == Subscription.Status.CLOSED:
            if subscription.plan.renewable:
                available_actions.append(('renew', 'Renew Subscription', 'btn-primary'))
    elif request.user.is_customer:
        # Customer-facing actions strictly constrained by plan flags
        if curr == Subscription.Status.ACTIVE:
            if subscription.plan.pausable:
                customer_actions.append(('pause', 'Pause Subscription', 'btn-secondary'))
            if not subscription.expiry_date or today >= subscription.expiry_date or subscription.plan.closable_early:
                customer_actions.append(('close', 'Cancel / Close Subscription', 'btn-danger'))
            if subscription.plan.renewable:
                customer_actions.append(('renew', 'Renew Subscription', 'btn-primary'))
        elif curr == Subscription.Status.PAUSED:
            customer_actions.append(('resume', 'Resume Subscription', 'btn-primary'))
            customer_actions.append(('close', 'Close Subscription', 'btn-danger'))
        elif curr == Subscription.Status.CLOSED:
            if subscription.plan.renewable:
                customer_actions.append(('renew', 'Renew Subscription', 'btn-primary'))

    return render(request, 'subscriptions/subscription_detail.html', {
        'subscription': subscription,
        'lines': subscription.lines.all(),
        'history': subscription.status_history.all(),
        'line_form': line_form,
        'available_actions': available_actions,
        'customer_actions': customer_actions,
    })


@customer_required
@require_POST
def customer_subscription_action(request, pk):
    """
    Executes customer-requested lifecycle actions (pause, resume, renew, close)
    strictly allowed by plan flags on their own subscription.
    Returns 404 if subscription belongs to another customer.
    """
    subscription = get_object_or_404(
        Subscription.objects.select_related('plan').filter(customer=request.user),
        pk=pk
    )
    if request.method == 'POST':
        action = request.POST.get('action', '').strip().lower()
        today = date.today()

        if action == 'pause':
            if not subscription.plan.pausable:
                messages.error(request, f"Plan '{subscription.plan.name}' does not permit pausing.")
            elif subscription.status != Subscription.Status.ACTIVE:
                messages.error(request, "Only active subscriptions can be paused.")
            else:
                try:
                    subscription.transition_to(Subscription.Status.PAUSED, user=request.user, notes="Paused by customer via portal.")
                    messages.success(request, f"Subscription {subscription.subscription_number} has been paused.")
                except ValidationError as e:
                    messages.error(request, str(e.message if hasattr(e, 'message') else e))

        elif action == 'resume':
            if subscription.status != Subscription.Status.PAUSED:
                messages.error(request, "Only paused subscriptions can be resumed.")
            else:
                try:
                    subscription.transition_to(Subscription.Status.ACTIVE, user=request.user, notes="Resumed by customer via portal.")
                    messages.success(request, f"Subscription {subscription.subscription_number} has been resumed.")
                except ValidationError as e:
                    messages.error(request, str(e.message if hasattr(e, 'message') else e))

        elif action == 'renew':
            if not subscription.plan.renewable:
                messages.error(request, f"Plan '{subscription.plan.name}' does not permit renewal.")
            else:
                try:
                    subscription.renew(user=request.user, notes="Renewed by customer via portal.")
                    messages.success(request, f"Subscription {subscription.subscription_number} renewed until {subscription.expiry_date}.")
                except ValidationError as e:
                    messages.error(request, str(e.message if hasattr(e, 'message') else e))

        elif action == 'close':
            is_valid, msg = subscription.can_transition_to(Subscription.Status.CLOSED)
            if not is_valid:
                messages.error(request, msg)
            else:
                try:
                    subscription.transition_to(Subscription.Status.CLOSED, user=request.user, notes="Closed by customer via portal.")
                    messages.success(request, f"Subscription {subscription.subscription_number} has been closed.")
                except ValidationError as e:
                    messages.error(request, str(e.message if hasattr(e, 'message') else e))
        else:
            messages.error(request, f"Unrecognized action '{action}'.")

    next_url = request.POST.get('next', '')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect('subscriptions:subscription_detail', pk=subscription.pk)

@staff_or_admin_required
def subscription_create(request):
    """Create a new subscription from scratch (Staff & Admin)."""
    if request.method == 'POST':
        form = SubscriptionForm(request.POST)
        if form.is_valid():
            sub = form.save(commit=False)
            sub.created_by = request.user
            sub.save()
            # Initial status history
            SubscriptionStatusHistory.objects.create(
                subscription=sub,
                from_status='none',
                to_status=sub.status,
                changed_by=request.user,
                notes="Initial creation."
            )
            messages.success(request, f"Subscription {sub.subscription_number} created.")
            return redirect('subscriptions:subscription_detail', pk=sub.pk)
        else:
            messages.error(request, "Please correct the errors in the form.")
    else:
        form = SubscriptionForm()

    return render(request, 'subscriptions/subscription_form.html', {
        'form': form,
        'title': 'Create Subscription',
    })

@staff_or_admin_required
def subscription_edit(request, pk):
    """Edit subscription details (Only allowed in Draft/Quotation)."""
    sub = get_object_or_404(Subscription, pk=pk)

    if sub.status not in [Subscription.Status.DRAFT, Subscription.Status.QUOTATION]:
        messages.error(request, f"Cannot edit subscription in '{sub.get_status_display()}' status. Status changes must use action controls.")
        return redirect('subscriptions:subscription_detail', pk=sub.pk)

    if request.method == 'POST':
        form = SubscriptionForm(request.POST, instance=sub)
        if form.is_valid():
            form.save()
            messages.success(request, f"Subscription {sub.subscription_number} updated.")
            return redirect('subscriptions:subscription_detail', pk=sub.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = SubscriptionForm(instance=sub)

    return render(request, 'subscriptions/subscription_form.html', {
        'form': form,
        'subscription': sub,
        'title': f"Edit Subscription: {sub.subscription_number}",
    })

@staff_or_admin_required
def subscription_delete(request, pk):
    """Delete a subscription (Draft only)."""
    sub = get_object_or_404(Subscription, pk=pk)
    if sub.status != Subscription.Status.DRAFT:
        messages.error(request, "Only Draft subscriptions can be completely deleted. Active or past subscriptions must be Closed.")
        return redirect('subscriptions:subscription_detail', pk=sub.pk)

    if request.method == 'POST':
        num = sub.subscription_number
        sub.delete()
        messages.success(request, f"Draft subscription {num} deleted.")
        return redirect('subscriptions:subscription_list')

    return render(request, 'subscriptions/confirm_delete.html', {
        'object_name': f"Draft Subscription {sub.subscription_number}",
        'cancel_url': 'subscriptions:subscription_detail',
        'cancel_pk': sub.pk,
    })

@staff_or_admin_required
@require_POST
def subscription_transition(request, pk):
    """Execute status transition via POST with validation."""
    sub = get_object_or_404(Subscription, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('new_status', '').strip()
        notes = request.POST.get('notes', '').strip()

        # Enforce minimum quantity check before transitioning to Confirmed or Active
        if new_status in [Subscription.Status.CONFIRMED, Subscription.Status.ACTIVE]:
            try:
                sub.validate_minimum_quantity()
            except ValidationError as e:
                messages.error(request, str(e.message if hasattr(e, 'message') else e))
                return redirect('subscriptions:subscription_detail', pk=sub.pk)

        try:
            sub.transition_to(new_status, user=request.user, notes=notes)
            messages.success(request, f"Subscription {sub.subscription_number} status updated to {sub.get_status_display()}.")
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

    return redirect('subscriptions:subscription_detail', pk=sub.pk)

@staff_or_admin_required
@require_POST
def subscription_renew(request, pk):
    """Renew subscription (extends expiry date if renewable)."""
    sub = get_object_or_404(Subscription, pk=pk)
    if request.method == 'POST':
        notes = request.POST.get('notes', '').strip()
        try:
            sub.renew(user=request.user, notes=notes)
            messages.success(request, f"Subscription {sub.subscription_number} renewed until {sub.expiry_date}.")
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

    return redirect('subscriptions:subscription_detail', pk=sub.pk)

@staff_or_admin_required
@require_POST
def subscription_line_create(request, subscription_pk):
    """Add a product line to subscription."""
    sub = get_object_or_404(Subscription, pk=subscription_pk)

    if sub.status not in [Subscription.Status.DRAFT, Subscription.Status.QUOTATION]:
        messages.error(request, "Product lines can only be added when subscription is in Draft or Quotation status.")
        return redirect('subscriptions:subscription_detail', pk=sub.pk)

    if request.method == 'POST':
        form = SubscriptionLineForm(request.POST)
        if form.is_valid():
            line = form.save(commit=False)
            line.subscription = sub
            line.save()
            messages.success(request, f"Added line: {line.quantity}x {line.product.name}.")
        else:
            for field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, f"{field.capitalize()}: {err}")

    return redirect('subscriptions:subscription_detail', pk=sub.pk)

@staff_or_admin_required
@require_POST
def subscription_line_delete(request, pk):
    """Remove a product line from subscription."""
    line = get_object_or_404(SubscriptionLine, pk=pk)
    sub = line.subscription

    if sub.status not in [Subscription.Status.DRAFT, Subscription.Status.QUOTATION]:
        messages.error(request, "Lines cannot be removed from active or confirmed subscriptions.")
        return redirect('subscriptions:subscription_detail', pk=sub.pk)

    if request.method == 'POST':
        name = line.product.name
        line.delete()
        messages.success(request, f"Removed {name} from subscription.")

    return redirect('subscriptions:subscription_detail', pk=sub.pk)

# ==============================================================================
# Quotation Templates Views
# ==============================================================================

@staff_or_admin_required
def template_list(request):
    """List quotation templates."""
    templates = QuotationTemplate.objects.prefetch_related('lines').all()
    return render(request, 'subscriptions/template_list.html', {
        'templates': templates
    })

@staff_or_admin_required
def template_detail(request, pk):
    """View quotation template and its product lines."""
    template = get_object_or_404(
        QuotationTemplate.objects.prefetch_related('lines__product', 'lines__variant', 'lines__tax'),
        pk=pk
    )
    line_form = QuotationTemplateLineForm() if request.user.is_admin else None

    return render(request, 'subscriptions/template_detail.html', {
        'template': template,
        'lines': template.lines.all(),
        'line_form': line_form,
    })

@admin_required
def template_create(request):
    """Create a new quotation template (Admin only)."""
    if request.method == 'POST':
        form = QuotationTemplateForm(request.POST)
        if form.is_valid():
            tmpl = form.save()
            messages.success(request, f"Quotation template '{tmpl.name}' created.")
            return redirect('subscriptions:template_detail', pk=tmpl.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = QuotationTemplateForm()

    return render(request, 'subscriptions/template_form.html', {
        'form': form,
        'title': 'Create Quotation Template',
    })

@admin_required
def template_edit(request, pk):
    """Edit quotation template (Admin only)."""
    tmpl = get_object_or_404(QuotationTemplate, pk=pk)

    if request.method == 'POST':
        form = QuotationTemplateForm(request.POST, instance=tmpl)
        if form.is_valid():
            tmpl = form.save()
            messages.success(request, f"Quotation template '{tmpl.name}' updated.")
            return redirect('subscriptions:template_detail', pk=tmpl.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = QuotationTemplateForm(instance=tmpl)

    return render(request, 'subscriptions/template_form.html', {
        'form': form,
        'template': tmpl,
        'title': f"Edit Template: {tmpl.name}",
    })

@admin_required
def template_delete(request, pk):
    """Delete a quotation template (Admin only)."""
    tmpl = get_object_or_404(QuotationTemplate, pk=pk)
    if request.method == 'POST':
        name = tmpl.name
        tmpl.delete()
        messages.success(request, f"Template '{name}' was deleted.")
        return redirect('subscriptions:template_list')

    return render(request, 'subscriptions/confirm_delete.html', {
        'object_name': f"Quotation Template {tmpl.name}",
        'cancel_url': 'subscriptions:template_detail',
        'cancel_pk': tmpl.pk,
    })

@admin_required
@require_POST
def template_line_create(request, template_pk):
    """Add a product line to quotation template (Admin only)."""
    tmpl = get_object_or_404(QuotationTemplate, pk=template_pk)

    if request.method == 'POST':
        form = QuotationTemplateLineForm(request.POST)
        if form.is_valid():
            line = form.save(commit=False)
            line.template = tmpl
            line.save()
            messages.success(request, f"Added line {line.quantity}x {line.product.name} to template.")
        else:
            for field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, f"{field.capitalize()}: {err}")

    return redirect('subscriptions:template_detail', pk=tmpl.pk)

@admin_required
@require_POST
def template_line_delete(request, pk):
    """Delete a line from quotation template (Admin only)."""
    line = get_object_or_404(QuotationTemplateLine, pk=pk)
    tmpl_pk = line.template.pk
    if request.method == 'POST':
        line.delete()
        messages.success(request, "Template line removed.")
    return redirect('subscriptions:template_detail', pk=tmpl_pk)

@staff_or_admin_required
@transaction.atomic
def subscription_create_from_template(request):
    """
    Creates a new subscription pre-filled with plan and product lines from a QuotationTemplate.
    Sets status to 'Quotation' and expiry date according to template validity days.
    """
    initial_template_id = request.GET.get('template')
    initial_data = {}
    if initial_template_id:
        initial_data['template'] = initial_template_id

    if request.method == 'POST':
        form = CreateFromTemplateForm(request.POST)
        if form.is_valid():
            tmpl = form.cleaned_data['template']
            customer = form.cleaned_data['customer']
            start_date = form.cleaned_data['start_date']
            payment_terms = form.cleaned_data['payment_terms']

            if not tmpl.plan:
                messages.error(request, "The selected template does not have an associated plan.")
                return render(request, 'subscriptions/create_from_template.html', {'form': form})

            expiry_date = start_date + timedelta(days=tmpl.validity_days)

            # Create subscription in Quotation status
            sub = Subscription.objects.create(
                customer=customer,
                plan=tmpl.plan,
                start_date=start_date,
                expiry_date=expiry_date,
                payment_terms=payment_terms,
                status=Subscription.Status.QUOTATION,
                created_by=request.user,
            )

            # Copy template lines
            for tmpl_line in tmpl.lines.all():
                SubscriptionLine.objects.create(
                    subscription=sub,
                    product=tmpl_line.product,
                    variant=tmpl_line.variant,
                    quantity=tmpl_line.quantity,
                    unit_price=tmpl_line.unit_price,
                    tax=tmpl_line.tax,
                )

            # Record creation in status history
            SubscriptionStatusHistory.objects.create(
                subscription=sub,
                from_status='none',
                to_status=Subscription.Status.QUOTATION,
                changed_by=request.user,
                notes=f"Created from template '{tmpl.name}' (valid for {tmpl.validity_days} days)."
            )

            messages.success(
                request,
                f"Subscription quotation {sub.subscription_number} created successfully from template '{tmpl.name}'!"
            )
            return redirect('subscriptions:subscription_detail', pk=sub.pk)
        else:
            messages.error(request, "Please correct the form errors.")
    else:
        form = CreateFromTemplateForm(initial=initial_data)

    return render(request, 'subscriptions/create_from_template.html', {
        'form': form,
    })

# ==============================================================================
# Discount Rules Views (Admin CRUD, Staff View-Only)
# ==============================================================================

@staff_or_admin_required
def discount_list(request):
    """View discount rules with search and pagination (Staff & Admin)."""
    search_query = request.GET.get('q', '').strip()
    scope_filter = request.GET.get('scope', '').strip()
    status_filter = request.GET.get('status', '').strip()

    discounts = DiscountRule.objects.prefetch_related('products').all()

    if search_query:
        discounts = discounts.filter(name__icontains=search_query)
    if scope_filter:
        discounts = discounts.filter(scope=scope_filter)
    if status_filter:
        is_active = status_filter == 'active'
        discounts = discounts.filter(is_active=is_active)

    paginator = Paginator(discounts, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'subscriptions/discount_list.html', {
        'page_obj': page_obj,
        'search_query': search_query,
        'scope_filter': scope_filter,
        'status_filter': status_filter,
        'scopes': DiscountRule.Scope.choices,
    })

@staff_or_admin_required
def discount_detail(request, pk):
    """View discount rule details and eligible products (Staff & Admin)."""
    discount = get_object_or_404(DiscountRule.objects.prefetch_related('products'), pk=pk)
    return render(request, 'subscriptions/discount_detail.html', {
        'discount': discount,
        'eligible_products': discount.products.all(),
    })

@admin_required
def discount_create(request):
    """Create a new discount rule (Admin only)."""
    if request.method == 'POST':
        form = DiscountRuleForm(request.POST)
        if form.is_valid():
            rule = form.save()
            messages.success(request, f"Discount rule '{rule.name}' created successfully.")
            return redirect('subscriptions:discount_detail', pk=rule.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = DiscountRuleForm()

    return render(request, 'subscriptions/discount_form.html', {
        'form': form,
        'title': 'Create Discount Rule',
    })

@admin_required
def discount_edit(request, pk):
    """Edit existing discount rule (Admin only)."""
    rule = get_object_or_404(DiscountRule, pk=pk)

    if request.method == 'POST':
        form = DiscountRuleForm(request.POST, instance=rule)
        if form.is_valid():
            rule = form.save()
            messages.success(request, f"Discount rule '{rule.name}' updated successfully.")
            return redirect('subscriptions:discount_detail', pk=rule.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = DiscountRuleForm(instance=rule)

    return render(request, 'subscriptions/discount_form.html', {
        'form': form,
        'discount': rule,
        'title': f"Edit Discount: {rule.name}",
    })

@admin_required
def discount_delete(request, pk):
    """Delete a discount rule (Admin only)."""
    rule = get_object_or_404(DiscountRule, pk=pk)
    if request.method == 'POST':
        name = rule.name
        rule.delete()
        messages.success(request, f"Discount rule '{name}' was deleted.")
        return redirect('subscriptions:discount_list')

    return render(request, 'subscriptions/confirm_delete.html', {
        'object_name': f"Discount Rule {rule.name}",
        'cancel_url': 'subscriptions:discount_detail',
        'cancel_pk': rule.pk,
    })
