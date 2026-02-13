from __future__ import annotations

from django.http import HttpRequest

from .models import AuditEvent, Raffle, Ticket, TicketPurchase


def _client_ip(request: HttpRequest) -> str:
    return (
        (request.META.get("HTTP_X_REAL_IP") or "")
        or (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
        or (request.META.get("REMOTE_ADDR") or "")
    )[:64]


def _user_agent(request: HttpRequest) -> str:
    return (request.META.get("HTTP_USER_AGENT") or "")[:255]


def log_event(
    *,
    request: HttpRequest,
    action: str,
    raffle: Raffle | None = None,
    purchase: TicketPurchase | None = None,
    ticket: Ticket | None = None,
    from_status: str = "",
    to_status: str = "",
    notes: str = "",
    extra: dict | None = None,
) -> None:
    """
    Best-effort audit log (never throws).
    """
    try:
        AuditEvent.objects.create(
            actor=getattr(request, "user", None) if getattr(request, "user", None) and request.user.is_authenticated else None,
            action=action,
            raffle=raffle,
            purchase=purchase,
            ticket=ticket,
            from_status=from_status or "",
            to_status=to_status or "",
            notes=notes or "",
            extra=extra or {},
            ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except Exception:
        return

