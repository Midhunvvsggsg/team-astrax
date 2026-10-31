import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List

from django.db import transaction

from automation.clock import get_today
from subscriptions.models import DiscountRule, Subscription
from subscriptions.services import get_applicable_discounts

from .models import Invoice, InvoiceLine

logger = logging.getLogger(__name__)

def add_months(sourcedate: date, months: int = 1) -> date:
    """
    Advances a date by a given number of months, correctly clamping to month-end.
    E.g. Jan 31 to Feb 28 (or Feb 29 on leap years).
    """
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

def add_years(sourcedate: date, years: int = 1) -> date:
    """Advances a date by years, handling leap year Feb 29 clamp."""
    try:
        return sourcedate.replace(year=sourcedate.year + years)
    except ValueError:
        return date(sourcedate.year + years, 2, 28)

def advance_period_date(start_date: date, billing_period: str) -> date:
    """
    Advances a start date by the plan's billing frequency.
    Supports daily, weekly, monthly, and yearly intervals.
    """
    if billing_period == 'daily':
        return start_date + timedelta(days=1)
    elif billing_period == 'weekly':
        return start_date + timedelta(weeks=1)
    elif billing_period == 'monthly':
        return add_months(start_date, 1)
    elif billing_period == 'yearly':
        return add_years(start_date, 1)
    return add_months(start_date, 1)

def calculate_due_date(issue_date: date, payment_terms: str) -> date:
    """Calculates invoice payment due date from payment terms."""
    if payment_terms == '15_days':
        return issue_date + timedelta(days=15)
    elif payment_terms == '30_days':
        return issue_date + timedelta(days=30)
    elif payment_terms == 'net_60':
        return issue_date + timedelta(days=60)
    # Default or immediate
    return issue_date


def generate_invoices(as_of_date: date = None) -> Dict[str, Any]:
    """
    Finds ALL Active subscriptions with an unbilled period due on or before as_of_date,
    INCLUDING missed past periods (catch up one invoice per missed period, in chronological order).

    Idempotent: DB unique constraint on (subscription, period_start) and status checks guarantee
    running this twice produces ZERO duplicate invoices.

    Returns:
        dict: {'created': int, 'skipped': int, 'failed': int, 'invoices': list, 'errors': list}
    """
    if as_of_date is None:
        as_of_date = get_today()

    active_subscriptions = (
        Subscription.objects.filter(status=Subscription.Status.ACTIVE)
        .select_related('customer', 'plan')
        .prefetch_related('lines__product', 'lines__variant', 'lines__tax')
        .order_by('id')
    )

    created_invoices: List[Invoice] = []
    skipped_count = 0
    failed_count = 0
    errors = []

    for sub in active_subscriptions:
        # Check if already past expiry
        if sub.expiry_date and as_of_date > sub.expiry_date and sub.is_expired:
            # Stop billing expired subscriptions
            continue

        try:
            with transaction.atomic():
                billed_starts = set(sub.invoices.values_list('period_start', flat=True))
                current_start = sub.start_date
                sub_created_any = False

                # Catch up on all missed periods up to as_of_date
                while current_start <= as_of_date:
                    # Stop if past subscription expiry date
                    if sub.expiry_date and current_start > sub.expiry_date:
                        break

                    next_start = advance_period_date(current_start, sub.plan.billing_period)
                    current_end = next_start - timedelta(days=1)
                    if sub.expiry_date and current_end > sub.expiry_date:
                        current_end = sub.expiry_date

                    # Check if this period was already billed
                    if current_start in billed_starts:
                        current_start = next_start
                        continue

                    # Unbilled period found! Build invoice.
                    issue_date = as_of_date
                    due_date = calculate_due_date(issue_date, sub.payment_terms)

                    # 1. Fetch applicable discounts for this billing period
                    applicable_discounts = get_applicable_discounts(sub, billing_date=current_start)

                    # 2. Build invoice lines and compute subtotals
                    subtotal = Decimal('0.00')
                    tax_total = Decimal('0.00')
                    discount_total = Decimal('0.00')

                    # Separate discounts by scope
                    product_discounts = [d for d in applicable_discounts if d.scope == DiscountRule.Scope.SPECIFIC_PRODUCTS]
                    whole_sub_discounts = [d for d in applicable_discounts if d.scope == DiscountRule.Scope.WHOLE_SUBSCRIPTION]

                    # Create invoice container first (Draft status)
                    invoice = Invoice(
                        subscription=sub,
                        customer=sub.customer,
                        period_start=current_start,
                        period_end=current_end,
                        issue_date=issue_date,
                        due_date=due_date,
                        status=Invoice.Status.DRAFT,
                    )
                    invoice.save()

                    lines_to_create = []

                    for sub_line in sub.lines.all():
                        line_base = sub_line.line_subtotal
                        subtotal += line_base

                        # Check for matching product-specific discount
                        line_discount = Decimal('0.00')
                        for pd in product_discounts:
                            if sub_line.product in pd.products.all():
                                if pd.discount_type == DiscountRule.DiscountType.PERCENTAGE:
                                    line_discount += (line_base * pd.value) / Decimal('100.00')
                                else:
                                    line_discount += pd.value

                        # Cap line discount at line base amount (never negative)
                        line_discount = min(line_discount, line_base).quantize(Decimal('0.01'))
                        discounted_line_base = line_base - line_discount

                        # Compute line tax from defined tax rules
                        line_tax = Decimal('0.00')
                        if sub_line.tax:
                            line_tax = sub_line.tax.calculate_tax(discounted_line_base)
                        tax_total += line_tax

                        line_total_amount = (discounted_line_base + line_tax).quantize(Decimal('0.01'))

                        desc = sub_line.product.name
                        if sub_line.variant:
                            desc += f" [{sub_line.variant.attribute}: {sub_line.variant.value}]"

                        lines_to_create.append(
                            InvoiceLine(
                                invoice=invoice,
                                description=desc,
                                product=sub_line.product,
                                variant=sub_line.variant,
                                quantity=sub_line.quantity,
                                unit_price=sub_line.unit_price,
                                tax=sub_line.tax,
                                tax_amount=line_tax,
                                discount_amount=line_discount,
                                amount=line_total_amount,
                            )
                        )
                        discount_total += line_discount

                    # Whole-subscription discount calculation
                    for wsd in whole_sub_discounts:
                        if wsd.discount_type == DiscountRule.DiscountType.PERCENTAGE:
                            sub_disc = (subtotal * wsd.value) / Decimal('100.00')
                        else:
                            sub_disc = wsd.value
                        sub_disc = min(sub_disc, subtotal - discount_total).quantize(Decimal('0.01'))
                        discount_total += sub_disc

                    # Bulk create invoice lines
                    InvoiceLine.objects.bulk_create(lines_to_create)

                    # Compute grand total
                    grand_total = (subtotal - discount_total + tax_total).quantize(Decimal('0.01'))
                    if grand_total < Decimal('0.00'):
                        grand_total = Decimal('0.00')

                    invoice.subtotal = subtotal
                    invoice.discount_total = discount_total
                    invoice.tax_total = tax_total
                    invoice.grand_total = grand_total
                    invoice.save(update_fields=['subtotal', 'discount_total', 'tax_total', 'grand_total', 'updated_at'])

                    # Increment usage count for each applied discount rule exactly once
                    for app_disc in applicable_discounts:
                        rule = app_disc.rule
                        rule.times_used += 1
                        rule.save(update_fields=['times_used', 'updated_at'])

                    # Mark billed and advance
                    billed_starts.add(current_start)
                    created_invoices.append(invoice)
                    sub_created_any = True
                    current_start = next_start

                if not sub_created_any:
                    skipped_count += 1

        except Exception as e:
            logger.exception(f"Failed to generate invoices for subscription {sub.subscription_number}: {e}")
            failed_count += 1
            errors.append((sub.subscription_number, str(e)))

    return {
        'created': len(created_invoices),
        'skipped': skipped_count,
        'failed': failed_count,
        'invoices': created_invoices,
        'errors': errors,
    }
