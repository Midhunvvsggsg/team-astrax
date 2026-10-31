from django.urls import path

from . import views

app_name = 'catalog'

urlpatterns = [
    # Products
    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.product_create, name='product_create'),
    path('products/<int:pk>/', views.product_detail, name='product_detail'),
    path('products/<int:pk>/edit/', views.product_edit, name='product_edit'),
    path('products/<int:pk>/delete/', views.product_delete, name='product_delete'),

    # Product Variants
    path('products/<int:product_pk>/variants/create/', views.variant_create, name='variant_create'),
    path('variants/<int:pk>/edit/', views.variant_edit, name='variant_edit'),
    path('variants/<int:pk>/delete/', views.variant_delete, name='variant_delete'),

    # Taxes
    path('taxes/', views.tax_list, name='tax_list'),
    path('taxes/create/', views.tax_create, name='tax_create'),
    path('taxes/<int:pk>/edit/', views.tax_edit, name='tax_edit'),
    path('taxes/<int:pk>/delete/', views.tax_delete, name='tax_delete'),

    # Plans
    path('plans/', views.plan_list, name='plan_list'),
    path('plans/create/', views.plan_create, name='plan_create'),
    path('plans/<int:pk>/edit/', views.plan_edit, name='plan_edit'),
    path('plans/<int:pk>/delete/', views.plan_delete, name='plan_delete'),
]
