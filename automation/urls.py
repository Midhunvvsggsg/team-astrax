from django.urls import path

from . import views

app_name = 'automation'

urlpatterns = [
    path('', views.automation_dashboard_view, name='dashboard'),
    path('run-now/', views.run_daily_jobs_view, name='run_now'),
    path('clock/advance/', views.demo_clock_advance_view, name='clock_advance'),
    path('clock/set/', views.demo_clock_set_view, name='clock_set'),
    path('clock/reset/', views.demo_clock_reset_view, name='clock_reset'),
    path('clock/run-jobs/', views.demo_clock_run_jobs_view, name='clock_run_jobs'),
]
