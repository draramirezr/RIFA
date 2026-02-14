from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Resetea la contraseña de un usuario admin (staff/superuser) usando variables de entorno."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            dest="username",
            default="",
            help="Username objetivo. Si no se indica, usa el primer superuser activo.",
        )

    def handle(self, *args, **options):
        password = (os.environ.get("ADMIN_RESET_PASSWORD") or "").strip()
        if not password:
            raise CommandError("Falta ADMIN_RESET_PASSWORD en variables de entorno.")

        username = (options.get("username") or "").strip()
        User = get_user_model()

        if username:
            user = User.objects.filter(username=username).first()
            if not user:
                raise CommandError(f"No existe el usuario: {username}")
        else:
            user = User.objects.filter(is_active=True, is_superuser=True).order_by("id").first()
            if not user:
                user = User.objects.filter(is_active=True, is_staff=True).order_by("id").first()
            if not user:
                raise CommandError("No se encontró ningún usuario staff/superuser activo.")

        # Make sure user can login to admin
        try:
            user.is_active = True
            user.is_staff = True
        except Exception:
            pass

        user.set_password(password)
        user.save()

        # Force password change on next admin login (if our model exists)
        try:
            from rifas.models import UserSecurity

            sec, _created = UserSecurity.objects.get_or_create(user=user)
            sec.force_password_change = True
            sec.password_hash_at_force = user.password
            sec.save()
        except Exception:
            # If rifas.UserSecurity not available, still OK.
            pass

        self.stdout.write(self.style.SUCCESS(f"OK: contraseña reseteada para '{getattr(user, 'username', '')}'"))

