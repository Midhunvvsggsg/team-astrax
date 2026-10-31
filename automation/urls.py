from django.urls import path

from . import views

app_name = 'automation'

urlpatterns = [
    path('', views.automation_dashboard_view, name='dashboard'),
    path('run-now/', views.run_daily_jobs_view, name='run_now'),
]
