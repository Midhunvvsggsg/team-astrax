from datetime import date, datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from accounts.decorators import staff_or_admin_required
from accounts.models import User
from .models import Invoice, InvoiceLine
from .services import generate_invoices

@staff_or_admin_required
def invoice_list(request):
    """Lists invoices with filters (status, customer, overdue), search, and pagination."""
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    customer_filter = request.GET.get('customer', '').strip()
    overdue_filter = request.GET.get('overdue', '').strip()

    invoices = Invoice.objects.select_related('customer', 'subscription__plan').all()

    if search_query:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search_query) |
            Q(customer__email__icontains=search_query) |
            Q(customer__first_name__icontains=search_query) |
            Q(customer__last_name__icontains=search_query) |
            Q(subscription__subscription_number__icontains=search_query)
        )
    if status_filter:
        invoices = invoices.filter(status=status_filter)
    if customer_filter:
        invoices = invoices.filter(customer_id=customer_filter)
    if overdue_filter == 'yes':
        today = date.today()
        invoices = invoices.filter(
            due_date__lt=today
        ).exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])

    paginator = Paginator(invoices, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    customers = User.objects.filter(role=User.Role.CUSTOMER).order_by('email')

    return render(request, 'invoicing/invoice_list.html', {
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'customer_filter': customer_filter,
        'overdue_filter': overdue_filter,
        'customers': customers,
        'statuses': Invoice.Status.choices,
        'today': date.today(),
    })

def invoice_detail(request, pk):
    """
    Detailed view of an invoice:
    - Staff & Admin can view any invoice and transition status.
    - Customer can ONLY view their own invoice (read-only).
    """
    if not request.user.is_authenticated:
        return redirect(f"/accounts/login/?next={request.path}")

    invoice = get_object_or_404(
        Invoice.objects.select_related('customer', 'subscription__plan')
        .prefetch_related('lines__product', 'lines__variant', 'lines__tax'),
        pk=pk
    )

    # Customer isolation
    if request.user.is_customer and invoice.customer != request.user:
        raise PermissionDenied("You can only access your own invoices.")

    return render(request, 'invoicing/invoice_detail.html', {
        'invoice': invoice,
        'lines': invoice.lines.all(),
    })

def invoice_print(request, pk):
    """Clean, printer-friendly invoice layout."""
    if not request.user.is_authenticated:
        return redirect(f"/accounts/login/?next={request.path}")

    invoice = get_object_or_404(
        Invoice.objects.select_related('customer', 'subscription__plan')
        .prefetch_related('lines__product', 'lines__variant', 'lines__tax'),
        pk=pk
    )

    if request.user.is_customer and invoice.customer != request.user:
        raise PermissionDenied("You can only access your own invoices.")

    return render(request, 'invoicing/invoice_print.html', {
        'invoice': invoice,
        'lines': invoice.lines.all(),
    })

@staff_or_admin_required
def invoice_transition(request, pk):
    """Transitions invoice status (Confirm, Cancel, etc.)"""
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('new_status', '').strip()
        try:
            invoice.transition_to(new_status)
            messages.success(request, f"Invoice {invoice.invoice_number} status updated to {invoice.get_status_display()}.")
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

    return redirect('invoicing:invoice_detail', pk=invoice.pk)

@staff_or_admin_required
def run_billing(request):
    """Runs billing engine on demand for all active subscriptions."""
    if request.method == 'POST':
        as_of_str = request.POST.get('as_of_date', '').strip()
        if as_of_str:
            try:
                as_of_date = datetime.strptime(as_of_str, '%Y-%m-%d').date()
            except ValueError:
                as_of_date = date.today()
        else:
            as_of_date = date.today()

        results = generate_invoices(as_of_date=as_of_date)

        msg = (
            f"Billing run completed as of {as_of_date}: "
            f"{results['created']} new invoice(s) generated, "
            f"{results['skipped']} subscription(s) up-to-date, "
            f"{results['failed']} failure(s)."
        )
        if results['created'] > 0:
            messages.success(request, msg)
        else:
            messages.info(request, msg + " All active subscriptions are already billed.")

    return redirect('invoicing:invoice_list')
