from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import staff_or_admin_required
from accounts.models import User
from automation.clock import get_today

from .dashboard import get_finance_dashboard_context
from .forms import PaymentForm
from .models import Invoice, Payment
from .services import generate_invoices


@staff_or_admin_required
def finance_dashboard(request):
    """Executive finance intelligence dashboard for staff and admin."""
    context = get_finance_dashboard_context(request)
    return render(request, 'dashboard.html', context)

@staff_or_admin_required
def invoice_list(request):
    """
    Lists invoices with SQL aggregate amount_paid/amount_due (zero N+1 queries),
    filters (status including Paid / Outstanding / Overdue, customer, date range),
    search, and pagination.
    """
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip().lower()
    customer_filter = request.GET.get('customer', '').strip()
    overdue_filter = request.GET.get('overdue', '').strip()
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()

    today = get_today()

    # Base queryset with SQL aggregate payment totals (prevents N+1 queries)
    invoices = Invoice.objects.select_related('customer', 'subscription__plan').with_payment_totals()

    if search_query:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search_query) |
            Q(customer__email__icontains=search_query) |
            Q(customer__first_name__icontains=search_query) |
            Q(customer__last_name__icontains=search_query) |
            Q(subscription__subscription_number__icontains=search_query)
        )

    # Status filter supporting: Paid / Outstanding / Overdue + Draft / Confirmed / Cancelled
    if status_filter == 'paid':
        invoices = invoices.filter(Q(status=Invoice.Status.PAID) | Q(annotated_amount_due=Decimal('0.00'))).exclude(status=Invoice.Status.CANCELLED)
    elif status_filter == 'overdue':
        invoices = invoices.filter(
            due_date__lt=today,
            annotated_amount_due__gt=Decimal('0.00')
        ).exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])
    elif status_filter == 'outstanding':
        invoices = invoices.filter(
            annotated_amount_due__gt=Decimal('0.00')
        ).exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])
    elif status_filter in [Invoice.Status.DRAFT, Invoice.Status.CONFIRMED, Invoice.Status.CANCELLED]:
        invoices = invoices.filter(status=status_filter)

    if customer_filter:
        invoices = invoices.filter(customer_id=customer_filter)

    if overdue_filter == 'yes' and status_filter != 'overdue':
        invoices = invoices.filter(
            due_date__lt=today,
            annotated_amount_due__gt=Decimal('0.00')
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

    # Count overdue invoices for navigation pill
    overdue_count = Invoice.objects.with_payment_totals().filter(
        due_date__lt=today,
        annotated_amount_due__gt=Decimal('0.00')
    ).exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED]).count()

    paginator = Paginator(invoices, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    customers = User.objects.filter(role=User.Role.CUSTOMER).order_by('email')

    status_choices = [
        ('all', 'All Statuses'),
        ('paid', 'Paid'),
        ('outstanding', 'Outstanding'),
        ('overdue', 'Overdue'),
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ]

    return render(request, 'invoicing/invoice_list.html', {
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'customer_filter': customer_filter,
        'overdue_filter': overdue_filter,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'customers': customers,
        'statuses': status_choices,
        'overdue_count': overdue_count,
        'today': today,
    })

@staff_or_admin_required
def overdue_invoices(request):
    """
    Dedicated view displaying overdue invoices with days overdue and debt aggregates.
    Overdue = due date passed AND not fully paid.
    """
    today = get_today()
    search_query = request.GET.get('q', '').strip()

    invoices = (
        Invoice.objects
        .select_related('customer', 'subscription__plan')
        .with_payment_totals()
        .filter(
            due_date__lt=today,
            annotated_amount_due__gt=Decimal('0.00')
        )
        .exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])
        .order_by('due_date')
    )

    if search_query:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search_query) |
            Q(customer__email__icontains=search_query) |
            Q(customer__first_name__icontains=search_query) |
            Q(customer__last_name__icontains=search_query) |
            Q(subscription__subscription_number__icontains=search_query)
        )

    total_overdue_debt = invoices.aggregate(total=Sum('annotated_amount_due'))['total'] or Decimal('0.00')
    total_overdue_count = invoices.count()

    paginator = Paginator(invoices, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'invoicing/overdue_list.html', {
        'page_obj': page_obj,
        'search_query': search_query,
        'today': today,
        'total_overdue_debt': total_overdue_debt,
        'total_overdue_count': total_overdue_count,
    })

@login_required
def payment_list(request):
    """
    Payments list with filters (method, date range).
    - Staff & Admin see all payments recorded in the system.
    - Customers see only their own payment history.
    """

    search_query = request.GET.get('q', '').strip()
    method_filter = request.GET.get('method', '').strip()
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()

    if request.user.is_customer:
        payments = Payment.objects.filter(invoice__customer=request.user)
    else:
        payments = Payment.objects.all()

    payments = payments.select_related('invoice__customer', 'invoice__subscription', 'recorded_by')

    if search_query:
        payments = payments.filter(
            Q(invoice__invoice_number__icontains=search_query) |
            Q(invoice__customer__email__icontains=search_query) |
            Q(note__icontains=search_query)
        )
    if method_filter:
        payments = payments.filter(method=method_filter)
    if start_date_str:
        try:
            start_d = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            payments = payments.filter(date__gte=start_d)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_d = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            payments = payments.filter(date__lte=end_d)
        except ValueError:
            pass

    total_amount = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    paginator = Paginator(payments, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'invoicing/payment_list.html', {
        'page_obj': page_obj,
        'search_query': search_query,
        'method_filter': method_filter,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'methods': Payment.Method.choices,
        'total_amount': total_amount,
    })

@login_required
def invoice_detail(request, pk):
    """
    Detailed view of an invoice:
    - Staff & Admin can view any invoice, transition status, send, and record payments.
    - Customer can ONLY view their own invoice (returns 404 for other customers' invoices).
    """

    # Enforce strict customer isolation: query strictly scoped to request.user returning 404
    if request.user.is_customer:
        invoice = get_object_or_404(
            Invoice.objects.filter(customer=request.user)
            .with_payment_totals()
            .select_related('customer', 'subscription__plan')
            .prefetch_related('lines__product', 'lines__variant', 'lines__tax', 'payments__recorded_by'),
            pk=pk
        )
    else:
        invoice = get_object_or_404(
            Invoice.objects.with_payment_totals()
            .select_related('customer', 'subscription__plan')
            .prefetch_related('lines__product', 'lines__variant', 'lines__tax', 'payments__recorded_by'),
            pk=pk
        )

    # Payment form only on Confirmed invoices with remaining balance for staff/admin
    payment_form = None
    if not request.user.is_customer and invoice.status == Invoice.Status.CONFIRMED and invoice.amount_due > Decimal('0.00'):
        payment_form = PaymentForm(invoice=invoice)

    return render(request, 'invoicing/invoice_detail.html', {
        'invoice': invoice,
        'lines': invoice.lines.all(),
        'payments': invoice.payments.all(),
        'payment_form': payment_form,
    })

@login_required
def invoice_print(request, pk):
    """Clean, printer-friendly invoice layout."""

    # Enforce strict customer isolation: query strictly scoped to request.user returning 404
    if request.user.is_customer:
        invoice = get_object_or_404(
            Invoice.objects.filter(customer=request.user)
            .with_payment_totals()
            .select_related('customer', 'subscription__plan')
            .prefetch_related('lines__product', 'lines__variant', 'lines__tax', 'payments'),
            pk=pk
        )
    else:
        invoice = get_object_or_404(
            Invoice.objects.with_payment_totals()
            .select_related('customer', 'subscription__plan')
            .prefetch_related('lines__product', 'lines__variant', 'lines__tax', 'payments'),
            pk=pk
        )

    return render(request, 'invoicing/invoice_print.html', {
        'invoice': invoice,
        'lines': invoice.lines.all(),
    })

@staff_or_admin_required
@require_POST
def invoice_transition(request, pk):
    """Transitions invoice status (Confirm, Cancel, Mark Paid)."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('new_status', '').strip()
        try:
            if new_status == Invoice.Status.PAID and invoice.amount_due > Decimal('0.00'):
                Payment.objects.create(
                    invoice=invoice,
                    amount=invoice.amount_due,
                    date=get_today(),
                    method=Payment.Method.BANK_TRANSFER,
                    note=f"Full settlement payment recorded via 'Mark as Paid' by {request.user.email}",
                    recorded_by=request.user,
                )
            invoice.transition_to(new_status)
            messages.success(request, f"Invoice {invoice.invoice_number} status updated to {invoice.get_status_display()}.")
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

    return redirect('invoicing:invoice_detail', pk=invoice.pk)

@staff_or_admin_required
@require_POST
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
@require_POST
def payment_create(request, pk):
    """
    Records a payment against an invoice.
    Enforces:
    - Payments only on Confirmed invoices.
    - Rejects negative amounts, zero, and overpayment.
    - Transitions invoice to Paid only when total payments >= grand total.
    """
    invoice = get_object_or_404(Invoice, pk=pk)

    if request.method == 'POST':
        if invoice.status != Invoice.Status.CONFIRMED:
            messages.error(request, "Payments can only be recorded on Confirmed invoices.")
            return redirect('invoicing:invoice_detail', pk=invoice.pk)

        form = PaymentForm(request.POST, invoice=invoice)
        if form.is_valid():
            try:
                payment = form.save(commit=False)
                payment.invoice = invoice
                payment.recorded_by = request.user
                payment.save()

                invoice.refresh_from_db()
                if invoice.status == Invoice.Status.PAID:
                    messages.success(
                        request,
                        f"Payment of ${payment.amount} recorded successfully. Invoice {invoice.invoice_number} is now Paid in Full!"
                    )
                else:
                    messages.success(
                        request,
                        f"Partial payment of ${payment.amount} recorded. Remaining balance due: ${invoice.amount_due}."
                    )
            except ValidationError as e:
                msg = e.message if hasattr(e, 'message') else str(e)
                messages.error(request, f"Payment error: {msg}")
        else:
            for field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, f"Payment error: {err}")

    return redirect('invoicing:invoice_detail', pk=invoice.pk)

@staff_or_admin_required
@require_POST
def run_billing(request):
    """Runs billing engine on demand for all active subscriptions."""
    if request.method == 'POST':
        as_of_str = request.POST.get('as_of_date', '').strip()
        if as_of_str:
            try:
                as_of_date = datetime.strptime(as_of_str, '%Y-%m-%d').date()
            except ValueError:
                as_of_date = get_today()
        else:
            as_of_date = get_today()

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
