from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.db import models
from django.utils import timezone
from django.views.decorators.http import require_http_methods

import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils.translation import gettext as _

from .forms import AdminPasswordRecoverForm, TicketLookupForm, TicketPurchaseForm
from .emails import send_purchase_notification
from .models import BankAccount, Raffle, SiteContent, TicketPurchase, UserSecurity


def home(request):
    raffles = (
        Raffle.objects.filter(is_active=True)
        .annotate(sold_tickets_annot=models.Count("tickets"))
        .order_by("draw_date")
    )
    site = SiteContent.get_solo()
    return render(request, "rifas/home.html", {"raffles": raffles, "site": site})


def raffle_detail(request, slug: str):
    # Allow viewing inactive/finished raffles (needed for Historial).
    try:
        raffle = get_object_or_404(
            Raffle.objects.annotate(sold_tickets_annot=models.Count("tickets")).prefetch_related("images"),
            slug=slug,
        )
    except Http404:
        return render(request, "rifas/not_found.html", status=404)
    offer = raffle.get_active_offer()
    show_conditions = (raffle.min_purchase_quantity and raffle.min_purchase_quantity > 1) or bool(offer)
    return render(
        request,
        "rifas/raffle_detail.html",
        {"raffle": raffle, "offer": offer, "show_conditions": show_conditions},
    )


@require_http_methods(["GET", "POST"])
def buy_ticket(request, slug: str):
    raffle = get_object_or_404(Raffle, slug=slug, is_active=True)
    if raffle.is_sold_out:
        return render(request, "rifas/sold_out.html", {"raffle": raffle}, status=403)

    offer = raffle.get_active_offer()
    site = SiteContent.get_solo()
    bank_accounts = list(BankAccount.objects.filter(is_active=True).order_by("sort_order", "created_at")[:4])

    if request.method == "POST":
        form = TicketPurchaseForm(request.POST, request.FILES, raffle=raffle)
        if form.is_valid():
            purchase: TicketPurchase = form.save(commit=False)
            purchase.raffle = raffle
            purchase.client_ip = request.META.get("REMOTE_ADDR")
            purchase.user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:400]
            purchase.save()
            send_purchase_notification(request=request, purchase=purchase)
            return redirect("rifas:thanks", purchase_id=purchase.id)
    else:
        form = TicketPurchaseForm(raffle=raffle)

    return render(
        request,
        "rifas/buy_ticket.html",
        {"raffle": raffle, "form": form, "offer": offer, "site": site, "bank_accounts": bank_accounts},
    )


def thanks(request, purchase_id: int):
    purchase = get_object_or_404(TicketPurchase, pk=purchase_id)
    return render(request, "rifas/thanks.html", {"purchase": purchase})


@require_http_methods(["GET", "POST"])
def my_tickets(request):
    site = SiteContent.get_solo()
    form = TicketLookupForm(request.POST or None)
    purchases = []
    raffle = None
    if request.method == "POST" and form.is_valid():
        raffle = form.cleaned_data["raffle"]
        phone = form.cleaned_data["phone"]
        ref = form.cleaned_data["reference"]
        qs = (
            TicketPurchase.objects.filter(raffle=raffle, phone__icontains=phone)
            .prefetch_related("tickets")
            .order_by("-created_at")
        )
        if ref:
            qs = qs.filter(public_reference=ref)
        purchases = list(qs)

    return render(
        request,
        "rifas/my_tickets.html",
        {"form": form, "purchases": purchases, "raffle": raffle, "site": site},
    )


def raffle_history(request):
    site = SiteContent.get_solo()
    now = timezone.now()
    finished = (
        Raffle.objects.filter(models.Q(draw_date__lte=now) | models.Q(is_active=False))
        .filter(show_in_history=True)
        .order_by("-draw_date")
    )
    return render(request, "rifas/history.html", {"finished": finished, "site": site})


def terms(request):
    site = SiteContent.get_solo()
    return render(request, "rifas/terms.html", {"site": site})


@require_http_methods(["GET", "POST"])
def admin_password_reset(request):
    """
    Admin password recovery:
    - User submits email
    - If a staff user exists, we generate a temporary password and email it
    - Force password change at next admin login

    Security:
    - Same response whether user exists or not
    - Basic throttling by IP + email (best-effort)
    """
    form = AdminPasswordRecoverForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        email = (form.cleaned_data["email"] or "").strip().lower()
        ip = (request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or "unknown").strip()

        # Throttle: 5 attempts per 10 minutes per IP and per email
        ip_key = f"pwreset:ip:{ip}"
        em_key = f"pwreset:em:{email}"
        for key in (ip_key, em_key):
            n = int(cache.get(key, 0) or 0)
            if n >= 5:
                messages.error(request, _("Demasiados intentos. Intenta de nuevo en unos minutos."))
                return render(request, "admin/password_reset.html", {"form": form})
        cache.set(ip_key, int(cache.get(ip_key, 0) or 0) + 1, 600)
        cache.set(em_key, int(cache.get(em_key, 0) or 0) + 1, 600)

        User = get_user_model()
        user = (
            User.objects.filter(is_active=True, is_staff=True)
            .filter(email__iexact=email)
            .order_by("id")
            .first()
        )

        # Always show generic success message to avoid user enumeration.
        success_msg = _(
            "Si el correo existe en el sistema, te enviaremos una contraseña temporal."
        )

        if user:
            temp_pwd = ("RIFA-" + secrets.token_urlsafe(9)).replace("-", "").replace("_", "")[:12]
            user.set_password(temp_pwd)
            user.save(update_fields=["password"])

            sec, _created = UserSecurity.objects.get_or_create(user=user)
            sec.force_password_change = True
            sec.password_hash_at_force = user.password  # hash of temporary password
            sec.forced_at = None  # will be set by model save
            sec.save()

            subject = "Recuperación de contraseña (Admin) - Sistema de Rifas"
            body = (
                "Se solicitó recuperación de contraseña para el panel administrador.\n\n"
                f"Usuario: {getattr(user, 'username', '')}\n"
                f"Contraseña temporal: {temp_pwd}\n\n"
                "Instrucciones:\n"
                "- Entra a /admin/ con esta contraseña temporal.\n"
                "- El sistema te obligará a cambiarla inmediatamente.\n"
                "- Si tú no solicitaste esto, contacta al administrador.\n"
            )
            try:
                send_mail(subject, body, None, [user.email], fail_silently=False)
            except Exception:
                # Keep response generic; logs will show SMTP errors in Railway.
                pass

        messages.success(request, success_msg)
        return redirect("/admin/login/")

    return render(request, "admin/password_reset.html", {"form": form})
