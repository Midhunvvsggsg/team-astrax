from django.urls import path
from . import views

urlpatterns = [
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('portal/', views.customer_portal_view, name='customer_portal'),
    path('staff/', views.staff_management_view, name='staff_management'),

    # Password Reset
    path('password-reset/', views.ConsolePasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', views.ConsolePasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', views.ConsolePasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('reset/done/', views.ConsolePasswordResetCompleteView.as_view(), name='password_reset_complete'),
]
