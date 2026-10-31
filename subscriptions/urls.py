from django.urls import path
from . import views

app_name = 'subscriptions'

urlpatterns = [
    # Subscriptions
    path('', views.subscription_list, name='subscription_list'),
    path('create/', views.subscription_create, name='subscription_create'),
    path('from-template/', views.subscription_create_from_template, name='subscription_from_template'),
    path('<int:pk>/', views.subscription_detail, name='subscription_detail'),
    path('<int:pk>/edit/', views.subscription_edit, name='subscription_edit'),
    path('<int:pk>/delete/', views.subscription_delete, name='subscription_delete'),
    path('<int:pk>/transition/', views.subscription_transition, name='subscription_transition'),
    path('<int:pk>/renew/', views.subscription_renew, name='subscription_renew'),

    # Subscription Lines
    path('<int:subscription_pk>/lines/create/', views.subscription_line_create, name='line_create'),
    path('lines/<int:pk>/delete/', views.subscription_line_delete, name='line_delete'),

    # Quotation Templates
    path('templates/', views.template_list, name='template_list'),
    path('templates/create/', views.template_create, name='template_create'),
    path('templates/<int:pk>/', views.template_detail, name='template_detail'),
    path('templates/<int:pk>/edit/', views.template_edit, name='template_edit'),
    path('templates/<int:pk>/delete/', views.template_delete, name='template_delete'),
    path('templates/<int:template_pk>/lines/create/', views.template_line_create, name='template_line_create'),
    path('templates/lines/<int:pk>/delete/', views.template_line_delete, name='template_line_delete'),

    # Discount Rules
    path('discounts/', views.discount_list, name='discount_list'),
    path('discounts/create/', views.discount_create, name='discount_create'),
    path('discounts/<int:pk>/', views.discount_detail, name='discount_detail'),
    path('discounts/<int:pk>/edit/', views.discount_edit, name='discount_edit'),
    path('discounts/<int:pk>/delete/', views.discount_delete, name='discount_delete'),
]
