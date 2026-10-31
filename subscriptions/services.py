from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List

from .models import DiscountRule, Subscription


@dataclass
class ApplicableDiscount:
    """
    Represents an evaluated applicable discount with computed savings.
    Delegates attribute access to the underlying DiscountRule so callers
    can treat it like a DiscountRule while having access to calculated_amount.
    """
    rule: DiscountRule
    calculated_amount: Decimal
    eligible_amount: Decimal
    scope: str
    description: str

    def __eq__(self, other):
        if isinstance(other, DiscountRule):
            return self.rule.pk == other.pk
        if isinstance(other, ApplicableDiscount):
            return self.rule.pk == other.rule.pk
        return False

    def __getattr__(self, name):
        return getattr(self.rule, name)


def get_applicable_discounts(subscription: Subscription, billing_date: date = None) -> List[ApplicableDiscount]:
    """
    Evaluates all active discount rules against the subscription and specified billing date.

    Validations performed:
    1. Active status check (`is_active=True`).
    2. Date range check (`start_date <= billing_date <= end_date`).
    3. Usage limit check (`times_used < usage_limit`).
    4. Scope evaluation:
       - Whole Subscription: checks total subscription subtotal and total seats.
       - Specific Products: checks matching product lines subtotal and quantity.
    5. Minimum purchase amount and minimum quantity thresholds.
    6. Mathematical limits:
       - Percentage discounts cannot exceed 100% of eligible amount.
       - Fixed discounts are capped at eligible amount (cannot make total/line negative).

    Returns a list of ApplicableDiscount objects.
    """
    if billing_date is None:
        billing_date = date.today()

    active_rules = (
        DiscountRule.objects.filter(is_active=True)
        .prefetch_related('products')
        .order_by('id')
    )

    applicable_discounts = []
    lines = list(subscription.lines.all())

    for rule in active_rules:
        # 1. Date window checks
        if rule.start_date and billing_date < rule.start_date:
            continue
        if rule.end_date and billing_date > rule.end_date:
            continue

        # 2. Total usage limit check
        if rule.usage_limit is not None and rule.times_used >= rule.usage_limit:
            continue

        # 3. Scope evaluation
        if rule.scope == DiscountRule.Scope.WHOLE_SUBSCRIPTION:
            eligible_amount = subscription.subtotal
            eligible_quantity = subscription.total_quantity

            # Minimum requirements
            if eligible_quantity < rule.min_quantity:
                continue
            if eligible_amount < rule.min_purchase_amount:
                continue
            if eligible_amount <= Decimal('0.00'):
                continue

            # Calculate discount
            if rule.discount_type == DiscountRule.DiscountType.PERCENTAGE:
                amt = (eligible_amount * rule.value) / Decimal('100.00')
            else:
                amt = rule.value

            # Cap discount so it never exceeds eligible amount
            discount_amount = min(amt, eligible_amount).quantize(Decimal('0.01'))
            desc = f"{rule.name} ({rule.value}% on subscription)" if rule.discount_type == DiscountRule.DiscountType.PERCENTAGE else f"{rule.name} (${rule.value} on subscription)"

            applicable_discounts.append(
                ApplicableDiscount(
                    rule=rule,
                    calculated_amount=discount_amount,
                    eligible_amount=eligible_amount,
                    scope=rule.scope,
                    description=desc,
                )
            )

        elif rule.scope == DiscountRule.Scope.SPECIFIC_PRODUCTS:
            eligible_product_ids = set(rule.products.values_list('id', flat=True))
            matching_lines = [line for line in lines if line.product_id in eligible_product_ids]

            if not matching_lines:
                continue

            eligible_quantity = sum(line.quantity for line in matching_lines)
            eligible_amount = sum(line.line_subtotal for line in matching_lines)

            # Minimum requirements
            if eligible_quantity < rule.min_quantity:
                continue
            if eligible_amount < rule.min_purchase_amount:
                continue
            if eligible_amount <= Decimal('0.00'):
                continue

            # Calculate discount
            if rule.discount_type == DiscountRule.DiscountType.PERCENTAGE:
                amt = (eligible_amount * rule.value) / Decimal('100.00')
            else:
                amt = rule.value

            # Cap discount so it never exceeds matching lines amount (no negative line amounts)
            discount_amount = min(amt, eligible_amount).quantize(Decimal('0.01'))
            desc = f"{rule.name} ({rule.value}% on eligible products)" if rule.discount_type == DiscountRule.DiscountType.PERCENTAGE else f"{rule.name} (${rule.value} on eligible products)"

            applicable_discounts.append(
                ApplicableDiscount(
                    rule=rule,
                    calculated_amount=discount_amount,
                    eligible_amount=eligible_amount,
                    scope=rule.scope,
                    description=desc,
                )
            )

    return applicable_discounts
