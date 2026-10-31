from django.urls import path
from . import views

app_name = 'invoicing'

urlpatterns = [
    path('', views.invoice_list, name='invoice_list'),
    path('overdue/', views.overdue_invoices, name='overdue_list'),
    path('payments/', views.payment_list, name='payment_list'),
    path('run-billing/', views.run_billing, name='run_billing'),
    path('<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('<int:pk>/print/', views.invoice_print, name='invoice_print'),
    path('<int:pk>/transition/', views.invoice_transition, name='invoice_transition'),
    path('<int:pk>/send/', views.invoice_send, name='invoice_send'),
    path('<int:pk>/payments/add/', views.payment_create, name='payment_create'),
]
