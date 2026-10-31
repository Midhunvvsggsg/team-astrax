from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib import messages
from django.contrib.auth.views import (
    PasswordResetView,
    PasswordResetDoneView,
    PasswordResetConfirmView,
    PasswordResetCompleteView
)
from django.urls import reverse_lazy
from .models import User
from .forms import CustomerSignupForm, StaffCreationForm, LoginForm
from .decorators import admin_required, customer_required, staff_or_admin_required
from invoicing.dashboard import get_finance_dashboard_context

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

def logout_view(request):
    """Logs out the active session."""
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('login')

@customer_required
def customer_portal_view(request):
    """Customer self-service portal (Customer role only)."""
    subscriptions = request.user.subscriptions.select_related('plan').prefetch_related('lines').all()
    invoices = request.user.invoices.select_related('subscription__plan').all()
    return render(request, 'accounts/customer_portal.html', {
        'customer': request.user,
        'subscriptions': subscriptions,
        'invoices': invoices,
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
