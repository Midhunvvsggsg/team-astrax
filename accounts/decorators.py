from functools import wraps
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.mixins import AccessMixin

def role_required(allowed_roles):
    """
    Decorator to enforce role-based access on the server.
    - If unauthenticated: redirects to login.
    - If role is not allowed: raises PermissionDenied (HTTP 403) or redirects with error.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"/accounts/login/?next={request.path}")
            if request.user.role not in allowed_roles:
                raise PermissionDenied("You do not have permission to access this resource.")
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

def admin_required(view_func):
    """Restricts view to ADMIN role only."""
    return role_required(['admin'])(view_func)

def staff_or_admin_required(view_func):
    """Restricts view to ADMIN or STAFF roles."""
    return role_required(['admin', 'staff'])(view_func)

def customer_required(view_func):
    """Restricts view to CUSTOMER role only."""
    return role_required(['customer'])(view_func)


class RoleRequiredMixin(AccessMixin):
    """CBV Mixin to enforce role-based access control on the server."""
    allowed_roles = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in self.allowed_roles:
            raise PermissionDenied("You do not have permission to access this resource.")
        return super().dispatch(request, *args, **kwargs)

class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin']

class StaffOrAdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin', 'staff']

class CustomerRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['customer']
