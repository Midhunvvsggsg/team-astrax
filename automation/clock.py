import calendar
from datetime import date, timedelta
from typing import Any, Dict

from django.conf import settings


def add_months(sourcedate: date, months: int = 1) -> date:
    """
    Advances a date by a given number of months, clamping to month-end.
    E.g. Jan 31 to Feb 28 (or Feb 29 on leap years).
    """
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def get_today() -> date:
    """
    Centralized system date provider.
    Returns the simulated demo date when DEMO_MODE=1 and demo clock is active.
    Otherwise returns the real date.today().
    """
    if not getattr(settings, 'DEMO_MODE', False):
        return date.today()

    try:
        from .models import DemoClock
        clock = DemoClock.objects.filter(id=1, is_active=True).first()
        if clock and clock.simulated_date:
            return clock.simulated_date
    except Exception:
        # Fallback if table does not exist or during early app initialization
        pass

    return date.today()


def set_demo_date(target_date: date) -> date:
    """Sets an explicit simulated date for the demo clock and activates it."""
    if not getattr(settings, 'DEMO_MODE', False):
        return date.today()

    from .models import DemoClock
    clock = DemoClock.get_instance()
    clock.simulated_date = target_date
    clock.is_active = True
    clock.save()
    return clock.simulated_date


def advance_demo_date(amount: str) -> date:
    """
    Advances the demo clock from current get_today():
    - '1_day': +1 day
    - '1_week': +7 days
    - '1_month': +1 month (clamped to month-end)
    """
    if not getattr(settings, 'DEMO_MODE', False):
        return date.today()

    current = get_today()
    if amount == '1_day':
        new_date = current + timedelta(days=1)
    elif amount == '1_week':
        new_date = current + timedelta(days=7)
    elif amount == '1_month':
        new_date = add_months(current, 1)
    else:
        new_date = current

    return set_demo_date(new_date)


def reset_demo_date() -> date:
    """Resets the demo clock back to real-time (deactivates simulation)."""
    if getattr(settings, 'DEMO_MODE', False):
        try:
            from .models import DemoClock
            clock = DemoClock.get_instance()
            clock.is_active = False
            clock.simulated_date = None
            clock.save()
        except Exception:
            pass
    return date.today()


def get_demo_clock_status() -> Dict[str, Any]:
    """Provides current status and metadata for UI and context processors."""
    enabled = getattr(settings, 'DEMO_MODE', False)
    if not enabled:
        return {
            'enabled': False,
            'is_active': False,
            'current_date': date.today(),
            'real_date': date.today(),
            'is_simulated': False,
        }

    real_today = date.today()
    current = get_today()
    is_active = False
    try:
        from .models import DemoClock
        clock = DemoClock.objects.filter(id=1).first()
        is_active = bool(clock and clock.is_active and clock.simulated_date)
    except Exception:
        pass

    return {
        'enabled': True,
        'is_active': is_active,
        'current_date': current,
        'real_date': real_today,
        'is_simulated': is_active and (current != real_today),
    }
