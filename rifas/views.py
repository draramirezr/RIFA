from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.db import models
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_http_methods
import uuid

import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _
import logging

from .forms import (
    AdminPasswordRecoverForm,
    AdminRaffleCalculatorForm,
    AdminRafflePerformanceForm,
    AdminWinnerLookupForm,
    TicketLookupForm,
    TicketPurchaseForm,
)
from .emails import send_customer_purchase_received, send_purchase_notification
from .models import BankAccount, Raffle, SiteContent, Ticket, TicketPurchase, UserSecurity


PUBLIC_PAGE_CACHE_SECONDS = 60


def _client_ip(request) -> str:
    # Best-effort IP extraction behind proxies.
    xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return xff or (request.META.get("REMOTE_ADDR") or "unknown")


def _rate_limit(*, key: str, limit: int, window_seconds: int) -> bool:
    """
    Simple counter-based rate limit using Django cache.
    Returns True if allowed, False if limited.
    """
    try:
        now_count = cache.get(key)
        if now_count is None:
            cache.set(key, 1, timeout=window_seconds)
            return True
        now_count = int(now_count) + 1
        if now_count > int(limit):
            return False
        cache.set(key, now_count, timeout=window_seconds)
        return True
    except Exception:
        # Fail open to avoid blocking real users on cache issues.
        return True


@cache_page(PUBLIC_PAGE_CACHE_SECONDS)
def home(request):
    raffles = (
        Raffle.objects.filter(is_active=True)
        .annotate(sold_tickets_annot=models.Count("tickets"))
        .order_by("draw_date")
    )
    # `site` is provided globally via context processor (cached).
    return render(request, "rifas/home.html", {"raffles": raffles})


@cache_page(PUBLIC_PAGE_CACHE_SECONDS)
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
    bank_accounts = list(BankAccount.objects.filter(is_active=True).order_by("sort_order", "created_at")[:4])

    if request.method == "POST":
        ip = _client_ip(request)
        if not _rate_limit(key=f"rl:buy_ticket:{ip}", limit=8, window_seconds=60):
            return render(
                request,
                "rifas/buy_ticket.html",
                {
                    "raffle": raffle,
                    "form": TicketPurchaseForm(request.POST, request.FILES, raffle=raffle),
                    "offer": offer,
                    "bank_accounts": bank_accounts,
                    "purchase_token": uuid.uuid4().hex,
                    "rate_limited": True,
                },
                status=429,
            )
        # Idempotency token to prevent duplicate purchases on double-click / slow networks
        token = (request.POST.get("purchase_token") or "").strip()
        tokens = request.session.get("purchase_tokens") or {}
        if isinstance(tokens, dict) and token and token in tokens:
            try:
                return redirect("rifas:thanks", purchase_id=int(tokens[token]))
            except Exception:
                pass
        form = TicketPurchaseForm(request.POST, request.FILES, raffle=raffle)
        if form.is_valid():
            purchase: TicketPurchase = form.save(commit=False)
            purchase.raffle = raffle
            purchase.client_ip = request.META.get("REMOTE_ADDR")
            purchase.user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:400]
            purchase.save()
            # Mark token as used (keep small history)
            if token:
                if not isinstance(tokens, dict):
                    tokens = {}
                tokens[token] = purchase.id
                # prune oldest entries (insertion order in modern Python)
                try:
                    while len(tokens) > 30:
                        tokens.pop(next(iter(tokens)))
                except Exception:
                    pass
                request.session["purchase_tokens"] = tokens
            # Emails must NEVER block or break purchases (SMTP may be blocked in hosting).
            try:
                send_purchase_notification(request=request, purchase=purchase)
            except Exception:
                pass
            try:
                send_customer_purchase_received(purchase=purchase)
            except Exception:
                pass
            return redirect("rifas:thanks", purchase_id=purchase.id)
    else:
        form = TicketPurchaseForm(raffle=raffle)

    return render(
        request,
        "rifas/buy_ticket.html",
        {
            "raffle": raffle,
            "form": form,
            "offer": offer,
            "bank_accounts": bank_accounts,
            "purchase_token": uuid.uuid4().hex,
        },
    )


def thanks(request, purchase_id: int):
    # Prevent IDOR: do not allow enumerating other purchases by ID.
    # Only allow if:
    # - staff user, OR
    # - this purchase was created in this session (tracked by purchase_tokens).
    if not (getattr(request, "user", None) and request.user.is_authenticated and request.user.is_staff):
        tokens = request.session.get("purchase_tokens") or {}
        allowed_ids = set()
        if isinstance(tokens, dict):
            for v in tokens.values():
                try:
                    allowed_ids.add(int(v))
                except Exception:
                    continue
        if int(purchase_id) not in allowed_ids:
            raise Http404()
    purchase = get_object_or_404(TicketPurchase, pk=purchase_id)
    return render(request, "rifas/thanks.html", {"purchase": purchase})


@require_http_methods(["GET", "POST"])
def my_tickets(request):
    form = TicketLookupForm(request.POST or None)
    purchases = []
    raffle = None
    if request.method == "POST":
        ip = _client_ip(request)
        if not _rate_limit(key=f"rl:my_tickets:{ip}", limit=20, window_seconds=60):
            return render(
                request,
                "rifas/my_tickets.html",
                {"form": form, "purchases": [], "raffle": None, "rate_limited": True},
                status=429,
            )
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
        {"form": form, "purchases": purchases, "raffle": raffle},
    )


def raffle_history(request):
    now = timezone.now()
    # Avoid N+1: annotate sold tickets and winner name in one query.
    from django.db.models import OuterRef, Subquery

    winner_name_sq = Subquery(
        Ticket.objects.filter(raffle_id=OuterRef("pk"), number=OuterRef("winner_ticket_number"))
        .values("purchase__full_name")[:1]
    )
    finished = (
        Raffle.objects.filter(models.Q(draw_date__lte=now) | models.Q(is_active=False))
        .filter(show_in_history=True)
        .annotate(sold_tickets_annot=models.Count("tickets"))
        .annotate(winner_display_name_annot=winner_name_sq)
        .order_by("-draw_date")
    )
    return render(request, "rifas/history.html", {"finished": finished})


@staff_member_required
@require_http_methods(["GET", "POST"])
def admin_raffle_calculator(request):
    """
    Admin-only calculator: estimate needed tickets given costs + desired margin
    and the raffle's ticket price.
    """
    from decimal import Decimal, ROUND_CEILING
    from math import ceil

    ctx = admin_site.each_context(request)
    form = AdminRaffleCalculatorForm(request.POST or None)
    result = None

    if request.method == "POST" and form.is_valid():
        raffle: Raffle = form.cleaned_data["raffle"]
        price = int(getattr(raffle, "price_per_ticket", 0) or 0)
        if price <= 0:
            form.add_error("raffle", "La rifa debe tener un precio por boleto mayor que 0.")
        else:
            product_cost = int(form.cleaned_data["product_cost"] or 0)
            shipping_cost = int(form.cleaned_data["shipping_cost"] or 0)
            advertising_cost = int(form.cleaned_data["advertising_cost"] or 0)
            other_costs = int(form.cleaned_data["other_costs"] or 0)
            margin_pct: Decimal = form.cleaned_data["desired_margin_percent"] or Decimal("0")

            total_cost = product_cost + shipping_cost + advertising_cost + other_costs
            multiplier = Decimal("1") + (Decimal(margin_pct) / Decimal("100"))
            revenue_needed = (Decimal(total_cost) * multiplier).to_integral_value(rounding=ROUND_CEILING)
            revenue_needed_int = int(revenue_needed)

            break_even_tickets = ceil(total_cost / price) if total_cost > 0 else 0
            tickets_needed = ceil(revenue_needed_int / price) if revenue_needed_int > 0 else 0

            expected_revenue = tickets_needed * price
            expected_profit = expected_revenue - total_cost

            max_tickets = int(getattr(raffle, "max_tickets", 0) or 0)

            # Offers affect how many TOTAL tickets get issued (paid + bonus),
            # which matters for max_tickets capacity.
            offer = raffle.get_active_offer()
            bonus_tickets = int(offer.bonus_for(tickets_needed)) if offer else 0
            total_issued = int(tickets_needed) + int(bonus_tickets)

            capacity_ok = (max_tickets <= 0) or (total_issued <= max_tickets)

            # If max_tickets is set, compute max paid tickets possible under offer:
            # find largest paid_qty such that paid_qty + bonus_for(paid_qty) <= max_tickets.
            max_paid_possible = None
            max_revenue_possible = None
            if max_tickets > 0 and offer:
                lo, hi = 0, max_tickets
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    tot = mid + int(offer.bonus_for(mid))
                    if tot <= max_tickets:
                        lo = mid
                    else:
                        hi = mid - 1
                max_paid_possible = int(lo)
                max_revenue_possible = int(max_paid_possible * price)

            result = {
                "raffle": raffle,
                "price": price,
                "total_cost": total_cost,
                "other_costs": other_costs,
                "margin_pct": margin_pct,
                "revenue_needed": revenue_needed_int,
                "break_even_tickets": break_even_tickets,
                "tickets_needed": tickets_needed,
                "bonus_tickets": bonus_tickets,
                "total_issued": total_issued,
                "offer": offer,
                "expected_revenue": expected_revenue,
                "expected_profit": expected_profit,
                "max_tickets": max_tickets,
                "capacity_ok": capacity_ok,
                "max_paid_possible": max_paid_possible,
                "max_revenue_possible": max_revenue_possible,
            }

            # Save calculation if requested
            if request.POST.get("save") == "1":
                try:
                    from django.urls import reverse

                    from .models import RaffleCalculation

                    calc = RaffleCalculation.objects.create(
                        raffle=raffle,
                        created_by=request.user if request.user.is_authenticated else None,
                        ticket_price=price,
                        product_cost=product_cost,
                        shipping_cost=shipping_cost,
                        advertising_cost=advertising_cost,
                        other_costs=other_costs,
                        desired_margin_percent=margin_pct,
                        total_cost=total_cost,
                        revenue_needed=revenue_needed_int,
                        break_even_tickets=break_even_tickets,
                        paid_tickets_needed=tickets_needed,
                        bonus_tickets=bonus_tickets,
                        total_issued=total_issued,
                        expected_revenue=expected_revenue,
                        expected_profit=expected_profit,
                        max_tickets=max_tickets,
                        offer_buy_quantity=int(getattr(offer, "buy_quantity", 0) or 0),
                        offer_bonus_quantity=int(getattr(offer, "bonus_quantity", 0) or 0),
                        offer_min_paid_quantity=int(getattr(offer, "min_paid_quantity", 0) or 0),
                    )
                    messages.success(request, "Cálculo guardado.")
                    return redirect(reverse("admin:rifas_rafflecalculation_change", args=[calc.id]))
                except Exception:
                    messages.warning(request, "No se pudo guardar el cálculo (intenta de nuevo).")

    ctx.update({"title": "Calculadora de boletos", "form": form, "result": result})
    return render(request, "admin/raffle_calculator.html", ctx)


@staff_member_required
@require_http_methods(["GET", "POST"])
def admin_raffle_performance(request):
    """
    Admin-only report: performance of a raffle over a date range (optional) and bank filter (optional).
    Metrics are based on APPROVED purchases only.
    """
    from datetime import datetime, time, timedelta

    from django.db.models import Count, Sum, Value
    from django.db.models.functions import Coalesce, TruncDate

    ctx = admin_site.each_context(request)
    form = AdminRafflePerformanceForm(request.POST or None)
    result = None

    if request.method == "POST" and form.is_valid():
        raffle: Raffle = form.cleaned_data["raffle"]
        bank = form.cleaned_data.get("bank_account")
        d_from = form.cleaned_data.get("date_from")
        d_to = form.cleaned_data.get("date_to")

        if not d_from:
            try:
                d_from = timezone.localdate(getattr(raffle, "created_at", None) or timezone.now())
            except Exception:
                d_from = timezone.localdate()
        if not d_to:
            d_to = timezone.localdate()

        tz = timezone.get_current_timezone()
        start_dt = timezone.make_aware(datetime.combine(d_from, time.min), tz)
        end_dt = timezone.make_aware(datetime.combine(d_to + timedelta(days=1), time.min), tz)

        qs = TicketPurchase.objects.filter(
            raffle=raffle,
            status=TicketPurchase.Status.APPROVED,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        )
        if bank:
            qs = qs.filter(bank_account=bank)

        totals = qs.aggregate(
            purchases=Coalesce(Count("id"), 0),
            revenue=Coalesce(Sum("total_amount"), 0),
            paid_tickets=Coalesce(Sum("quantity"), 0),
            bonus_tickets=Coalesce(Sum("bonus_quantity"), 0),
            total_tickets=Coalesce(Sum("total_tickets"), 0),
        )

        by_bank = []
        if not bank:
            by_bank = list(
                qs.values("bank_account__bank_name")
                .annotate(
                    bank_name=Coalesce("bank_account__bank_name", Value("Sin banco")),
                    purchases=Coalesce(Count("id"), 0),
                    revenue=Coalesce(Sum("total_amount"), 0),
                    paid_tickets=Coalesce(Sum("quantity"), 0),
                )
                .order_by("-revenue", "-purchases")
            )

        by_day = list(
            qs.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(
                purchases=Coalesce(Count("id"), 0),
                revenue=Coalesce(Sum("total_amount"), 0),
                paid_tickets=Coalesce(Sum("quantity"), 0),
            )
            .order_by("day")
        )

        result = {
            "raffle": raffle,
            "bank": bank,
            "date_from": d_from,
            "date_to": d_to,
            "totals": totals,
            "by_bank": by_bank,
            "by_day": by_day,
        }

    ctx.update({"title": "Rendimiento de rifa", "form": form, "result": result})
    return render(request, "admin/raffle_performance.html", ctx)


@cache_page(PUBLIC_PAGE_CACHE_SECONDS)
def terms(request):
    return render(request, "rifas/terms.html", {})


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

            subject = "Recuperación de contraseña (Admin) - GanaHoyRD"
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
                from .emails import send_admin_temporary_password

                inferred = ""
                try:
                    inferred = request.build_absolute_uri("/").rstrip("/")
                except Exception:
                    inferred = ""
                ok, err = send_admin_temporary_password(
                    to_email=user.email,
                    username=getattr(user, "username", "") or "",
                    temp_password=temp_pwd,
                    site_url=(getattr(settings, "SITE_URL", "") or inferred),
                )
                if not ok and getattr(settings, "EMAIL_LOG_ERRORS", False):
                    logging.getLogger(__name__).warning("Admin password reset email failed: %s", err)
            except Exception:
                # Keep response generic; logs will show SMTP errors in Railway.
                pass

        messages.success(request, success_msg)
        return redirect("/admin/login/")

    return render(request, "admin/password_reset.html", {"form": form})


def _mask_phone_last4(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if not digits:
        return ""
    if len(digits) <= 4:
        return "*" * len(digits)
    return digits[:-4] + "****"


@staff_member_required
@require_http_methods(["GET", "POST"])
def admin_winner_search(request):
    """
    Admin-only tool to look up the winning ticket.
    - Search by ticket number (supports left-zero padded input)
    - Optional raffle filter to disambiguate repeated numbers across raffles
    - Phone is masked by default with a "Ver" toggle.
    """
    form = AdminWinnerLookupForm(request.POST or None)
    searched = request.method == "POST"
    results = []

    if searched and form.is_valid():
        raffle = form.cleaned_data.get("raffle")
        number = form.cleaned_data.get("ticket_number")
        qs = Ticket.objects.select_related("raffle", "purchase").filter(number=number)
        if raffle:
            qs = qs.filter(raffle=raffle)
        for t in qs.order_by("-created_at")[:50]:
            p = t.purchase
            phone = getattr(p, "phone", "") or ""
            results.append(
                {
                    "ticket": t,
                    "purchase": p,
                    "masked_phone": _mask_phone_last4(phone),
                    "full_phone": phone,
                }
            )

    ctx = admin_site.each_context(request)
    ctx.update(
        {
            "title": "Buscar boleto ganador",
            "form": form,
            "searched": searched,
            "results": results,
        }
    )
    return render(request, "admin/winner_search.html", ctx)
