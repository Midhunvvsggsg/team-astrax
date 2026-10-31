from datetime import date, datetime
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.db.models import Q
from accounts.decorators import staff_or_admin_required
from accounts.models import User
from .models import Invoice, InvoiceLine, Payment
from .forms import PaymentForm
from .services import generate_invoices

@staff_or_admin_required
def invoice_list(request):
    """Lists invoices with filters (status, customer, date range, overdue), search, and pagination."""
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    customer_filter = request.GET.get('customer', '').strip()
    overdue_filter = request.GET.get('overdue', '').strip()
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()

    invoices = Invoice.objects.select_related('customer', 'subscription__plan').prefetch_related('payments').all()

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

    if start_date_str:
        try:
            start_d = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            invoices = invoices.filter(issue_date__gte=start_d)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_d = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            invoices = invoices.filter(issue_date__lte=end_d)
        except ValueError:
            pass

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
        'start_date': start_date_str,
        'end_date': end_date_str,
        'customers': customers,
        'statuses': Invoice.Status.choices,
        'today': date.today(),
    })

def invoice_detail(request, pk):
    """
    Detailed view of an invoice:
    - Staff & Admin can view any invoice, transition status, send, and record payments.
    - Customer can ONLY view their own invoice (read-only).
    """
    if not request.user.is_authenticated:
        return redirect(f"/accounts/login/?next={request.path}")

    invoice = get_object_or_404(
        Invoice.objects.select_related('customer', 'subscription__plan')
        .prefetch_related('lines__product', 'lines__variant', 'lines__tax', 'payments__recorded_by'),
        pk=pk
    )

    # Customer isolation
    if request.user.is_customer and invoice.customer != request.user:
        raise PermissionDenied("You can only access your own invoices.")

    payment_form = None
    if not request.user.is_customer and invoice.status in [Invoice.Status.DRAFT, Invoice.Status.CONFIRMED]:
        payment_form = PaymentForm(invoice=invoice)

    return render(request, 'invoicing/invoice_detail.html', {
        'invoice': invoice,
        'lines': invoice.lines.all(),
        'payments': invoice.payments.all(),
        'payment_form': payment_form,
    })

def invoice_print(request, pk):
    """Clean, printer-friendly invoice layout."""
    if not request.user.is_authenticated:
        return redirect(f"/accounts/login/?next={request.path}")

    invoice = get_object_or_404(
        Invoice.objects.select_related('customer', 'subscription__plan')
        .prefetch_related('lines__product', 'lines__variant', 'lines__tax', 'payments'),
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
    """Transitions invoice status (Confirm, Cancel, Mark Paid)."""
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
def invoice_send(request, pk):
    """Sends invoice to the customer via email using the configured console email backend."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == 'POST':
        subject = f"Invoice {invoice.invoice_number} from Brightloop Software"
        body = (
            f"Dear {invoice.customer.full_name or invoice.customer.email},\n\n"
            f"Please find below the details for invoice {invoice.invoice_number}:\n\n"
            f"Subscription: {invoice.subscription.subscription_number} ({invoice.subscription.plan.name})\n"
            f"Billing Period: {invoice.period_start} to {invoice.period_end}\n"
            f"Issue Date: {invoice.issue_date}\n"
            f"Due Date: {invoice.due_date}\n"
            f"Status: {invoice.get_status_display()}\n\n"
            f"Subtotal: ${invoice.subtotal}\n"
            f"Discounts Applied: -${invoice.discount_total}\n"
            f"Taxes: +${invoice.tax_total}\n"
            f"Grand Total: ${invoice.grand_total}\n"
            f"Amount Paid: ${invoice.amount_paid}\n"
            f"Remaining Balance Due: ${invoice.amount_due}\n\n"
            f"Payment Terms: {invoice.subscription.get_payment_terms_display()}\n\n"
            f"You may review this invoice anytime in your customer self-service portal.\n\n"
            f"Best regards,\n"
            f"Brightloop Software Finance Team\n"
            f"billing@brightloop.local\n"
        )
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'billing@brightloop.local')
        send_mail(
            subject,
            body,
            from_email,
            [invoice.customer.email],
            fail_silently=False
        )

        invoice.sent_at = timezone.now()
        invoice.save(update_fields=['sent_at', 'updated_at'])
        messages.success(request, f"Invoice {invoice.invoice_number} successfully sent to {invoice.customer.email}.")

    return redirect('invoicing:invoice_detail', pk=invoice.pk)

@staff_or_admin_required
def payment_create(request, pk):
    """Records a payment against an invoice. If balance is covered, marks Paid."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == 'POST':
        form = PaymentForm(request.POST, invoice=invoice)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.invoice = invoice
            payment.recorded_by = request.user
            payment.save()

            messages.success(request, f"Payment of ${payment.amount} successfully recorded.")

            # Auto-transition to Paid if balance is fully paid
            invoice.refresh_from_db()
            if invoice.amount_due <= Decimal('0.00'):
                if invoice.status != Invoice.Status.PAID:
                    invoice.status = Invoice.Status.PAID
                    invoice.save(update_fields=['status', 'updated_at'])
                    messages.info(request, f"Invoice {invoice.invoice_number} is now fully paid!")
            elif invoice.status == Invoice.Status.DRAFT:
                # Having a partial payment confirms the invoice
                invoice.status = Invoice.Status.CONFIRMED
                invoice.save(update_fields=['status', 'updated_at'])
        else:
            for field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, f"Payment Error: {err}")

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
