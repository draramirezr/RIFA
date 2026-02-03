from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.db import models
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import TicketLookupForm, TicketPurchaseForm
from .emails import send_purchase_notification
from .models import BankAccount, Raffle, SiteContent, TicketPurchase


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
