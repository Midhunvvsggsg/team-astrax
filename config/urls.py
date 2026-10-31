from django.contrib import admin
from django.urls import include, path

from accounts.views import home_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home_view, name='home'),
    path('dashboard/', home_view, name='dashboard'),
    path('accounts/', include('accounts.urls')),
    path('catalog/', include('catalog.urls')),
    path('subscriptions/', include('subscriptions.urls')),
    path('invoices/', include('invoicing.urls')),
    path('automation/', include('automation.urls')),
]
