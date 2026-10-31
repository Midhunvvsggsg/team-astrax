import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Case, Count, DecimalField, F, Sum, Value, When
from django.db.models.functions import Coalesce, TruncMonth

from accounts.models import User
from catalog.models import Plan
from invoicing.models import Invoice, Payment
from subscriptions.models import Subscription


def get_finance_dashboard_context(request):
    """
    Computes all finance metrics using pure SQL aggregates (no Python loops),
    prepares chart datasets, and paginates overdue invoices and recent payments.
    """
    today = date.today()

    # Parse GET filters
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()
    status_filter = request.GET.get('subscription_status', '').strip()
    customer_filter = request.GET.get('customer', '').strip()
    plan_filter = request.GET.get('plan', '').strip()

    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    # 1. Base Subscription queryset
    subs_qs = Subscription.objects.select_related('customer', 'plan')
    if status_filter:
        subs_qs = subs_qs.filter(status=status_filter)
    if customer_filter:
        subs_qs = subs_qs.filter(customer_id=customer_filter)
    if plan_filter:
        subs_qs = subs_qs.filter(plan_id=plan_filter)
    if start_date:
        subs_qs = subs_qs.filter(start_date__gte=start_date)
    if end_date:
        subs_qs = subs_qs.filter(start_date__lte=end_date)

    # 2. Base Invoice queryset (with payment totals SQL aggregate)
    invoices_qs = Invoice.objects.select_related('customer', 'subscription__plan').with_payment_totals()
    if customer_filter:
        invoices_qs = invoices_qs.filter(customer_id=customer_filter)
    if plan_filter:
        invoices_qs = invoices_qs.filter(subscription__plan_id=plan_filter)
    if status_filter:
        invoices_qs = invoices_qs.filter(subscription__status=status_filter)
    if start_date:
        invoices_qs = invoices_qs.filter(issue_date__gte=start_date)
    if end_date:
        invoices_qs = invoices_qs.filter(issue_date__lte=end_date)

    # 3. Base Payment queryset
    payments_qs = Payment.objects.select_related('invoice__customer', 'invoice__subscription__plan', 'recorded_by')
    if customer_filter:
        payments_qs = payments_qs.filter(invoice__customer_id=customer_filter)
    if plan_filter:
        payments_qs = payments_qs.filter(invoice__subscription__plan_id=plan_filter)
    if status_filter:
        payments_qs = payments_qs.filter(invoice__subscription__status=status_filter)
    if start_date:
        payments_qs = payments_qs.filter(date__gte=start_date)
    if end_date:
        payments_qs = payments_qs.filter(date__lte=end_date)

    # CARD 1: Active subscriptions (SQL COUNT)
    active_subscriptions_count = subs_qs.filter(status=Subscription.Status.ACTIVE).count()

    # CARD 2: Revenue received (SQL SUM)
    raw_revenue = payments_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    revenue_received = Decimal(str(raw_revenue)).quantize(Decimal('0.01'))

    # CARD 3: Amount still owed (SQL SUM of balance due on unpaid, non-cancelled invoices)
    raw_owed = (
        invoices_qs
        .exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])
        .aggregate(total=Sum('annotated_amount_due'))['total'] or Decimal('0.00')
    )
    amount_still_owed = Decimal(str(raw_owed)).quantize(Decimal('0.01'))

    # CARD 4: Overdue invoice count (SQL COUNT)
    overdue_invoice_count = (
        invoices_qs
        .filter(due_date__lt=today, annotated_amount_due__gt=Decimal('0.00'))
        .exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])
        .count()
    )

    # CARD 5: Monthly Recurring Revenue (MRR) - SQL aggregate with Case/When and Coalesce
    active_subs_mrr_qs = (
        subs_qs.filter(status=Subscription.Status.ACTIVE)
        .annotate(
            sub_base=Coalesce(
                Sum(F('lines__quantity') * F('lines__unit_price')),
                F('plan__price'),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        )
        .annotate(
            mrr_val=Case(
                When(plan__billing_period='daily', then=F('sub_base') * Value(Decimal('30.00'))),
                When(plan__billing_period='weekly', then=F('sub_base') * Value(Decimal('4.3333'))),
                When(plan__billing_period='monthly', then=F('sub_base') * Value(Decimal('1.00'))),
                When(plan__billing_period='yearly', then=F('sub_base') / Value(Decimal('12.00'))),
                default=F('sub_base'),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        )
    )
    raw_mrr = active_subs_mrr_qs.aggregate(total=Sum('mrr_val'))['total'] or Decimal('0.00')
    monthly_recurring_revenue = Decimal(str(raw_mrr)).quantize(Decimal('0.01'))

    # CHART 1: Revenue by Month (SQL TruncMonth + Sum)
    monthly_payments = (
        payments_qs
        .annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(rev=Sum('amount'))
        .order_by('month')
    )
    rev_dict = {p['month']: p['rev'] for p in monthly_payments if p['month']}

    if len(rev_dict) < 6 and not start_date and not end_date:
        # Default to 6-month trailing window
        chart_months = []
        first_of_month = today.replace(day=1)
        for i in range(5, -1, -1):
            # approximate month back
            prev_m = (first_of_month - timedelta(days=i*30)).replace(day=1)
            chart_months.append(prev_m)
    else:
        chart_months = sorted(rev_dict.keys())

    revenue_chart_labels = [m.strftime('%b %Y') for m in chart_months]
    revenue_chart_data = [float(rev_dict.get(m, Decimal('0.00'))) for m in chart_months]

    # CHART 2: Active Subscriptions by Plan (SQL Count + Group By)
    plan_distribution = (
        subs_qs.filter(status=Subscription.Status.ACTIVE)
        .values('plan__name')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    plan_chart_labels = [p['plan__name'] for p in plan_distribution]
    plan_chart_data = [p['count'] for p in plan_distribution]

    # TABLE 1: Overdue Invoices
    overdue_invoices_qs = (
        invoices_qs
        .filter(due_date__lt=today, annotated_amount_due__gt=Decimal('0.00'))
        .exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])
        .order_by('due_date')
    )
    page_overdue = request.GET.get('page_overdue', 1)
    paginator_overdue = Paginator(overdue_invoices_qs, 5)
    overdue_page_obj = paginator_overdue.get_page(page_overdue)

    # TABLE 2: Recent Payments
    recent_payments_qs = payments_qs.order_by('-date', '-created_at')
    page_payments = request.GET.get('page_payments', 1)
    paginator_payments = Paginator(recent_payments_qs, 5)
    payments_page_obj = paginator_payments.get_page(page_payments)

    # Dropdown choices for filter bar
    all_customers = User.objects.filter(role=User.Role.CUSTOMER).order_by('email')
    all_plans = Plan.objects.order_by('name')
    subscription_statuses = Subscription.Status.choices

    return {
        'today': today,
        # Metric Cards
        'active_subscriptions_count': active_subscriptions_count,
        'revenue_received': revenue_received,
        'amount_still_owed': amount_still_owed,
        'overdue_invoice_count': overdue_invoice_count,
        'monthly_recurring_revenue': monthly_recurring_revenue,

        # Charts JSON
        'revenue_chart_labels': json.dumps(revenue_chart_labels),
        'revenue_chart_data': json.dumps(revenue_chart_data),
        'plan_chart_labels': json.dumps(plan_chart_labels),
        'plan_chart_data': json.dumps(plan_chart_data),

        # Tables with pagination
        'overdue_page_obj': overdue_page_obj,
        'payments_page_obj': payments_page_obj,

        # Filter Options & Active Values
        'all_customers': all_customers,
        'all_plans': all_plans,
        'subscription_statuses': subscription_statuses,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'status_filter': status_filter,
        'customer_filter': customer_filter,
        'plan_filter': plan_filter,
    }
