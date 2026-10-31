from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required

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
