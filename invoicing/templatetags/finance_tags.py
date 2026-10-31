from decimal import Decimal, InvalidOperation
from typing import Any, Union

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name='currency')
def currency(value: Any, mode: Union[str, bool, None] = None) -> str:
    """
    Standard currency formatter for AstraFlow.
    - Default mode: Formats with thousands separators and 2 decimal places ($5,966,696.42).
    - Compact mode (e.g. |currency:"compact"): For very large numbers (>= $1M), displays
      compact notation like $16.3M or $1.2B with the full formatted amount in a `title` tooltip.
    """
    if value is None or value == "":
        val = Decimal('0.00')
    elif isinstance(value, Decimal):
        val = value
    elif isinstance(value, (int, float)):
        val = Decimal(str(value))
    else:
        # Clean any preexisting currency symbols or formatting commas
        cleaned = str(value).replace('$', '').replace(',', '').strip()
        try:
            val = Decimal(cleaned)
        except (InvalidOperation, ValueError, TypeError):
            return str(value)

    sign = '-' if val < 0 else ''
    abs_val = abs(val)
    full_str = f"{sign}${abs_val:,.2f}"

    is_compact = str(mode).lower() in ('compact', 'kpi', 'card', 'true', '1')

    if is_compact:
        if abs_val >= Decimal('1000000000'):
            compact_num = (abs_val / Decimal('1000000000')).quantize(Decimal('0.1'))
            compact_str = f"{sign}${compact_num:,.1f}B"
            return mark_safe(f'<span title="{full_str}">{compact_str}</span>')
        elif abs_val >= Decimal('1000000'):
            compact_num = (abs_val / Decimal('1000000')).quantize(Decimal('0.1'))
            compact_str = f"{sign}${compact_num:,.1f}M"
            return mark_safe(f'<span title="{full_str}">{compact_str}</span>')
        else:
            return mark_safe(f'<span title="{full_str}">{full_str}</span>')

    return full_str


@register.filter(name='money')
def money(value: Any, mode: Union[str, bool, None] = None) -> str:
    """Alias for currency filter."""
    return currency(value, mode)
