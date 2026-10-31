from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from invoicing.dashboard import get_finance_dashboard_context
from invoicing.models import Invoice, Payment

from .decorators import admin_required, customer_required, staff_or_admin_required
from .forms import (
    CustomerPasswordChangeForm,
    CustomerProfileForm,
    CustomerSignupForm,
    LoginForm,
    StaffCreationForm,
)
from .models import User


def home_view(request):
    """Landing page and routing hub based on authentication status and role."""
    if request.user.is_authenticated:
        if request.user.is_customer:
            return redirect('customer_portal')
        elif request.user.is_admin or request.user.is_staff_member:
            context = get_finance_dashboard_context(request)
            return render(request, 'dashboard.html', context)
    return render(request, 'home.html')

@staff_or_admin_required
def dashboard_view(request):
    """Finance dashboard view for staff and admin."""
    context = get_finance_dashboard_context(request)
    return render(request, 'dashboard.html', context)

def signup_view(request):
    """Customer registration view. Only creates customers."""
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        form = CustomerSignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f"Welcome to Brightloop, {user.first_name or user.email}! Your customer account has been created.")
            return redirect('customer_portal')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = CustomerSignupForm()

    return render(request, 'accounts/signup.html', {'form': form})

def login_view(request):
    """Login view supporting case-insensitive email authentication."""
    if request.user.is_authenticated:
        return redirect('home')

    next_url = request.GET.get('next', '')
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f"Signed in successfully as {user.get_role_display()}.")

            # Safe redirect
            if next_url and next_url.startswith('/'):
                return redirect(next_url)
            if user.is_customer:
                return redirect('customer_portal')
            return redirect('home')
        else:
            messages.error(request, "Invalid email or password.")
    else:
        form = LoginForm()

    return render(request, 'accounts/login.html', {'form': form, 'next': next_url})

@require_POST
def logout_view(request):
    """Logs out the active session (POST-only to prevent CSRF logout attacks)."""
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('login')

@customer_required
def customer_portal_view(request):
    """
    Customer self-service portal (Customer role only).
    - Subscriptions: status, plan, next billing date, expiry date, allowed actions.
    - Invoices: filter by status, view, print, balance due.
    - Payments: recent payment transactions.
    """
    subscriptions = (
        request.user.subscriptions
        .select_related('plan')
        .prefetch_related('invoices', 'lines__tax', 'lines__product', 'lines__variant')
        .all()
    )

    status_filter = request.GET.get('status', '').strip().lower()
    invoices = (
        request.user.invoices
        .select_related('subscription__plan')
        .with_payment_totals()
        .all()
    )

    today = date.today()
    if status_filter == 'paid':
        invoices = invoices.filter(
            Q(status=Invoice.Status.PAID) | Q(annotated_amount_due=Decimal('0.00'))
        ).exclude(status=Invoice.Status.CANCELLED)
    elif status_filter == 'overdue':
        invoices = invoices.filter(
            due_date__lt=today,
            annotated_amount_due__gt=Decimal('0.00')
        ).exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])
    elif status_filter == 'outstanding':
        invoices = invoices.filter(
            due_date__gte=today,
            annotated_amount_due__gt=Decimal('0.00')
        ).exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])
    elif status_filter in ['draft', 'confirmed', 'cancelled']:
        invoices = invoices.filter(status=status_filter)

    paginator = Paginator(invoices, 10)
    page_number = request.GET.get('page')
    invoices_page = paginator.get_page(page_number)

    # Customer's recent payment history
    recent_payments = (
        Payment.objects.filter(invoice__customer=request.user)
        .select_related('invoice')
        .order_by('-date', '-id')[:5]
    )

    status_options = [
        ('all', 'All Invoices'),
        ('paid', 'Paid'),
        ('outstanding', 'Outstanding'),
        ('overdue', 'Overdue'),
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
    ]

    return render(request, 'accounts/customer_portal.html', {
        'customer': request.user,
        'subscriptions': subscriptions,
        'invoices': invoices_page,
        'recent_payments': recent_payments,
        'status_filter': status_filter,
        'status_options': status_options,
        'today': today,
    })


@customer_required
def customer_profile_view(request):
    """Allows logged-in customers to update their name and email."""
    if request.method == 'POST':
        form = CustomerProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile details have been updated successfully.")
            return redirect('customer_profile')
        else:
            messages.error(request, "Please correct the errors in the profile form.")
    else:
        form = CustomerProfileForm(instance=request.user)

    return render(request, 'accounts/customer_profile.html', {
        'form': form,
        'customer': request.user,
    })


@customer_required
def customer_change_password_view(request):
    """Allows logged-in customers to change their account password."""
    if request.method == 'POST':
        form = CustomerPasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            new_pwd = form.cleaned_data['new_password']
            request.user.set_password(new_pwd)
            request.user.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, "Your password has been changed successfully.")
            return redirect('customer_portal')
        else:
            messages.error(request, "Please correct the password errors below.")
    else:
        form = CustomerPasswordChangeForm(user=request.user)

    return render(request, 'accounts/customer_change_password.html', {
        'form': form,
        'customer': request.user,
    })

@admin_required
def staff_management_view(request):
    """Admin-only view to list and create staff users."""
    staff_users = User.objects.filter(role=User.Role.STAFF).order_by('-date_joined')

    if request.method == 'POST':
        form = StaffCreationForm(request.POST)
        if form.is_valid():
            staff = form.save()
            messages.success(request, f"Staff account created successfully for {staff.email}.")
            return redirect('staff_management')
        else:
            messages.error(request, "Please correct the errors in the staff creation form.")
    else:
        form = StaffCreationForm()

    return render(request, 'accounts/staff_management.html', {
        'staff_users': staff_users,
        'form': form,
    })


# Password reset views using console backend
class ConsolePasswordResetView(PasswordResetView):
    template_name = 'accounts/password_reset.html'
    email_template_name = 'accounts/password_reset_email.html'
    subject_template_name = 'accounts/password_reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')

class ConsolePasswordResetDoneView(PasswordResetDoneView):
    template_name = 'accounts/password_reset_done.html'

class ConsolePasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')

class ConsolePasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'accounts/password_reset_complete.html'
