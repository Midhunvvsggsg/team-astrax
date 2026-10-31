from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html

from .models import AutomationRun, Reminder
from .services import execute_daily_jobs


@admin.register(AutomationRun)
class AutomationRunAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'started_at',
        'trigger_badge',
        'status_badge',
        'invoices_created',
        'renewals_made',
        'subscriptions_closed',
        'reminders_sent',
        'duration_seconds',
        'initiated_by',
    )
    list_filter = ('trigger', 'status', 'started_at')
    readonly_fields = (
        'started_at',
        'completed_at',
        'trigger',
        'status',
        'invoices_created',
        'renewals_made',
        'subscriptions_closed',
        'reminders_sent',
        'duration_seconds',
        'errors',
        'initiated_by',
    )
    search_fields = ('errors', 'initiated_by__email')
    change_list_template = 'admin/automation/automationrun/change_list.html'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'run-daily-jobs/',
                self.admin_site.admin_view(self.trigger_run_now),
                name='automation_run_daily_jobs',
            ),
        ]
        return custom_urls + urls

    def trigger_run_now(self, request):
        """Admin 'Run now' handler to execute daily automated jobs immediately."""
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
            self.message_user(request, msg, level=messages.SUCCESS)
        elif run.status == AutomationRun.Status.PARTIAL:
            self.message_user(request, msg + " Note: Some steps reported errors.", level=messages.WARNING)
        else:
            self.message_user(request, f"Automation Run #{run.id} failed: {run.errors}", level=messages.ERROR)

        return redirect(reverse('admin:automation_automationrun_changelist'))

    def trigger_badge(self, obj):
        color = '#0284c7' if obj.trigger == 'manual' else '#64748b'
        return format_html(
            '<span style="background:{}; color:#fff; padding:2px 8px; border-radius:12px; font-weight:600; font-size:11px;">{}</span>',
            color,
            obj.get_trigger_display(),
        )
    trigger_badge.short_description = 'Trigger'

    def status_badge(self, obj):
        colors = {
            'success': '#16a34a',
            'partial': '#d97706',
            'failed': '#dc2626',
        }
        color = colors.get(obj.status, '#64748b')
        return format_html(
            '<span style="background:{}; color:#fff; padding:2px 8px; border-radius:12px; font-weight:600; font-size:11px;">{}</span>',
            color,
            obj.get_status_display(),
        )
    status_badge.short_description = 'Status'


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'reminder_type',
        'recipient',
        'target_object',
        'target_date',
        'sent_at',
    )
    list_filter = ('reminder_type', 'sent_at')
    search_fields = ('recipient__email', 'subscription__subscription_number', 'invoice__invoice_number', 'subject')
    readonly_fields = (
        'reminder_type',
        'recipient',
        'subscription',
        'invoice',
        'target_date',
        'sent_at',
        'subject',
        'message',
    )

    def target_object(self, obj):
        if obj.subscription:
            return f"Subscription {obj.subscription.subscription_number}"
        if obj.invoice:
            return f"Invoice {obj.invoice.invoice_number}"
        return "System"
    target_object.short_description = 'Target Record'
