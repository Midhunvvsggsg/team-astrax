from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required

from .clock import advance_demo_date, get_today, reset_demo_date, set_demo_date
from .models import AutomationRun, Reminder
from .services import execute_daily_jobs


@admin_required
def automation_dashboard_view(request):
    """Admin dashboard for automated daily operations."""
    runs = AutomationRun.objects.select_related('initiated_by').all()
    paginator = Paginator(runs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    recent_reminders = Reminder.objects.select_related('recipient', 'subscription', 'invoice')[:10]

    return render(request, 'automation/dashboard.html', {
        'page_obj': page_obj,
        'recent_reminders': recent_reminders,
        'total_runs': runs.count(),
    })


@admin_required
@require_POST
def run_daily_jobs_view(request):
    """Executes the daily automated jobs on demand."""
    run = execute_daily_jobs(
        trigger=AutomationRun.Trigger.MANUAL,
        user=request.user,
        as_of_date=get_today(),
    )

    msg = (
        f"Automation Run #{run.id} finished ({run.get_status_display()} in {run.duration_seconds}s): "
        f"{run.invoices_created} invoices generated, "
        f"{run.renewals_made} subscriptions renewed, "
        f"{run.subscriptions_closed} expired subscriptions closed, "
        f"{run.reminders_sent} reminders dispatched."
    )

    if run.status == AutomationRun.Status.SUCCESS:
        messages.success(request, msg)
    elif run.status == AutomationRun.Status.PARTIAL:
        messages.warning(request, msg + " Note: Some steps reported errors.")
    else:
        messages.error(request, f"Automation Run #{run.id} failed: {run.errors}")

    return redirect(request.POST.get('next') or 'automation:dashboard')


@admin_required
@require_POST
def demo_clock_advance_view(request):
    """Advances the Demo Clock by 1 day, 1 week, or 1 month."""
    if not getattr(settings, 'DEMO_MODE', False):
        raise PermissionDenied("Demo clock is disabled because DEMO_MODE is off.")

    amount = request.POST.get('amount', '1_day')
    new_date = advance_demo_date(amount)
    messages.success(request, f"Demo clock advanced to {new_date.strftime('%Y-%m-%d')}.")
    return redirect(request.POST.get('next') or 'home')


@admin_required
@require_POST
def demo_clock_set_view(request):
    """Sets the Demo Clock to an explicit target date."""
    if not getattr(settings, 'DEMO_MODE', False):
        raise PermissionDenied("Demo clock is disabled because DEMO_MODE is off.")

    target_str = request.POST.get('target_date', '').strip()
    try:
        target_date = datetime.strptime(target_str, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, f"Invalid date format: {target_str}. Please use YYYY-MM-DD.")
        return redirect(request.POST.get('next') or 'home')

    set_demo_date(target_date)
    messages.success(request, f"Demo clock set to {target_date.strftime('%Y-%m-%d')}.")
    return redirect(request.POST.get('next') or 'home')


@admin_required
@require_POST
def demo_clock_reset_view(request):
    """Resets the Demo Clock back to real-world time."""
    if not getattr(settings, 'DEMO_MODE', False):
        raise PermissionDenied("Demo clock is disabled because DEMO_MODE is off.")

    real_date = reset_demo_date()
    messages.info(request, f"Demo clock reset to real time ({real_date.strftime('%Y-%m-%d')}).")
    return redirect(request.POST.get('next') or 'home')


@admin_required
@require_POST
def demo_clock_run_jobs_view(request):
    """Executes daily automated jobs against the simulated demo date."""
    if not getattr(settings, 'DEMO_MODE', False):
        raise PermissionDenied("Demo clock is disabled because DEMO_MODE is off.")

    current_today = get_today()
    run = execute_daily_jobs(
        trigger=AutomationRun.Trigger.MANUAL,
        user=request.user,
        as_of_date=current_today,
    )

    msg = (
        f"Daily jobs executed for {current_today.strftime('%Y-%m-%d')} (AutomationRun #{run.id}): "
        f"{run.invoices_created} invoices generated, "
        f"{run.renewals_made} renewals, "
        f"{run.subscriptions_closed} subscriptions closed, "
        f"{run.reminders_sent} reminders dispatched."
    )
    if run.status == AutomationRun.Status.SUCCESS:
        messages.success(request, msg)
    elif run.status == AutomationRun.Status.PARTIAL:
        messages.warning(request, msg + " (with warnings)")
    else:
        messages.error(request, f"Automation failed: {run.errors}")

    return redirect(request.POST.get('next') or 'home')
