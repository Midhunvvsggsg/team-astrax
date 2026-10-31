from django.urls import path

from . import views

app_name = 'invoicing'

urlpatterns = [
    path('', views.invoice_list, name='invoice_list'),
    path('dashboard/', views.finance_dashboard, name='finance_dashboard'),
    path('overdue/', views.overdue_invoices, name='overdue_list'),
    path('payments/', views.payment_list, name='payment_list'),
    path('run-billing/', views.run_billing, name='run_billing'),
    path('reconciliation/', views.reconciliation_dashboard, name='reconciliation_dashboard'),
    path('reconciliation/<int:pk>/', views.reconciliation_detail, name='reconciliation_detail'),
    path('reconciliation/rows/<int:row_pk>/approve/', views.reconciliation_row_approve, name='reconciliation_row_approve'),
    path('reconciliation/rows/<int:row_pk>/manual/', views.reconciliation_row_manual, name='reconciliation_row_manual'),
    path('reports/', views.monthly_report_list, name='report_list'),
    path('reports/generate/', views.monthly_report_generate, name='report_generate'),
    path('reports/<int:pk>/', views.monthly_report_detail, name='report_detail'),
    path('reports/<int:pk>/csv/', views.monthly_report_download_csv, name='report_csv'),
    path('<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('<int:pk>/print/', views.invoice_print, name='invoice_print'),
    path('<int:pk>/transition/', views.invoice_transition, name='invoice_transition'),
    path('<int:pk>/send/', views.invoice_send, name='invoice_send'),
    path('<int:pk>/payments/add/', views.payment_create, name='payment_create'),
]
