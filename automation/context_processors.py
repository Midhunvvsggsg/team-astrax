from django.conf import settings

from .clock import get_demo_clock_status


def demo_clock_context(request):
    """Exposes demo clock status and control flags to all templates."""
    enabled = getattr(settings, 'DEMO_MODE', False)
    if not enabled:
        return {'demo_clock': {'enabled': False, 'is_active': False}}

    return {'demo_clock': get_demo_clock_status()}
