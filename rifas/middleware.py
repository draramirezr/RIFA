from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from .models import UserSecurity


class AdminForcePasswordChangeMiddleware:
    """
    If an authenticated admin user is flagged for password change, force them to visit
    the admin password change screen until they update their password.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        path = request.path or ""

        # Only enforce inside Django admin
        if user and user.is_authenticated and path.startswith("/admin/"):
            try:
                sec = UserSecurity.objects.select_related("user").get(user=user)
            except UserSecurity.DoesNotExist:
                sec = None

            if sec and sec.force_password_change:
                # If password already changed, clear flag.
                if sec.password_hash_at_force and user.password != sec.password_hash_at_force:
                    sec.force_password_change = False
                    sec.save(update_fields=["force_password_change", "password_hash_at_force", "forced_at"])
                else:
                    # Allow these paths to avoid loops
                    allowed = {
                        reverse("admin:password_change"),
                        reverse("admin:password_change_done"),
                        reverse("admin:logout"),
                    }
                    # Also allow the password change POST endpoint (same URL), and admin JS i18n
                    if path not in allowed and not path.startswith("/admin/jsi18n/"):
                        return redirect("admin:password_change")

        return self.get_response(request)

