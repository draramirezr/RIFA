from __future__ import annotations

import mimetypes
import threading
import base64
import json
import logging
import urllib.request
import urllib.error

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import TicketPurchase


logger = logging.getLogger(__name__)


def _send_via_sendgrid_api(email: EmailMessage) -> None:
    """
    Send email through SendGrid Web API (HTTPS). Useful when SMTP is blocked.
    Requires SENDGRID_API_KEY and SENDGRID_USE_API=1.
    """
    api_key = (getattr(settings, "SENDGRID_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("Missing SENDGRID_API_KEY")

    from_email = (getattr(email, "from_email", "") or "").strip() or (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "")
    to_emails = list(getattr(email, "to", []) or [])
    if not to_emails:
        return

    payload: dict = {
        "personalizations": [{"to": [{"email": e} for e in to_emails]}],
        "from": {"email": from_email},
        "subject": getattr(email, "subject", "") or "",
        "content": [{"type": "text/plain", "value": getattr(email, "body", "") or ""}],
    }

    attachments = []
    for att in getattr(email, "attachments", []) or []:
        # Django stores as (filename, content, mimetype)
        try:
            filename, content, mimetype = att
        except Exception:
            continue
        if not filename or content is None:
            continue
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content
        attachments.append(
            {
                "content": base64.b64encode(content_bytes).decode("ascii"),
                "type": mimetype or "application/octet-stream",
                "filename": filename,
                "disposition": "attachment",
            }
        )
    if attachments:
        payload["attachments"] = attachments

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    timeout = int(getattr(settings, "SENDGRID_API_TIMEOUT", 10) or 10)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # 202 Accepted is success.
            if int(getattr(resp, "status", 0) or 0) not in (200, 202):
                raise RuntimeError(f"SendGrid API unexpected status: {getattr(resp,'status',None)}")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")
        except Exception:
            body = ""
        raise RuntimeError(f"SendGrid API HTTPError {e.code}: {body}") from e


def _send_async(email: EmailMessage) -> None:
    """
    Send email in a background thread so web requests don't hang
    if SMTP is slow/unreachable.
    """

    def _runner():
        try:
            if getattr(settings, "SENDGRID_USE_API", False) and getattr(settings, "SENDGRID_API_KEY", ""):
                _send_via_sendgrid_api(email)
            else:
                email.send(fail_silently=True)
        except Exception as e:
            if getattr(settings, "EMAIL_LOG_ERRORS", False):
                logger.warning("Email send failed: %s", e, exc_info=True)
            return

    t = threading.Thread(target=_runner, daemon=True)
    t.start()


def _should_send_customer_emails() -> bool:
    return bool(getattr(settings, "SEND_CUSTOMER_EMAILS", False))


def _safe_attach_image(email: EmailMessage, purchase: TicketPurchase) -> None:
    """
    Attach proof image if reasonably small; otherwise keep URL only.
    """
    try:
        f = purchase.proof_image
        if not f or not getattr(f, "name", ""):
            return
        if getattr(f, "size", 0) and f.size > 6 * 1024 * 1024:
            return
        f.open("rb")
        mimetype, _enc = mimetypes.guess_type(f.name)
        email.attach(
            filename=f.name.rsplit("/", 1)[-1],
            content=f.read(),
            mimetype=mimetype or "image/*",
        )
    except Exception:
        return


def send_purchase_notification(*, request, purchase: TicketPurchase) -> None:
    """
    Sends a purchase notification (with proof) to the configured admin inbox.
    In dev, emails are printed to the console if EMAIL_BACKEND is console backend.
    """
    if not getattr(settings, "SEND_PURCHASE_EMAILS", False):
        return
    to_email = (getattr(settings, "PURCHASE_NOTIFY_EMAIL", "") or "").strip()
    if not to_email:
        return

    raffle = purchase.raffle
    subject = f"Nueva compra pendiente - {raffle.title} (#{purchase.id})"

    proof_url = ""
    try:
        proof_url = request.build_absolute_uri(purchase.proof_image.url)
    except Exception:
        proof_url = ""

    lines = [
        f"Fecha: {timezone.localtime(purchase.created_at):%d/%m/%Y %I:%M %p}",
        f"Rifa: {raffle.title}",
        f"Compra ID: {purchase.id}",
        f"Código consulta: {purchase.public_reference}",
        f"Nombre: {purchase.full_name}",
        f"Teléfono: {purchase.phone}",
        f"Email: {purchase.email or '-'}",
        f"Cantidad (pagados): {purchase.quantity}",
        f"Total: RD$ {purchase.total_amount}",
    ]
    if proof_url:
        lines.append(f"Comprobante (URL): {proof_url}")

    body = "\n".join(lines) + "\n"

    email = EmailMessage(subject=subject, body=body, to=[to_email])

    _safe_attach_image(email, purchase)

    _send_async(email)


def send_customer_purchase_received(*, purchase: TicketPurchase) -> None:
    """
    Customer email when purchase is created (pending).
    """
    if not _should_send_customer_emails():
        return
    to_email = (purchase.email or "").strip()
    if not to_email:
        return

    subject = f"Recibimos tu compra - {purchase.raffle.title}"
    body = "\n".join(
        [
            "¡Gracias por participar en GanaHoyRD!",
            "",
            f"Rifa: {purchase.raffle.title}",
            f"Código de compra: {purchase.public_reference}",
            f"Cantidad (pagados): {purchase.quantity}",
            f"Total: RD$ {purchase.total_amount}",
            "",
            "Estado: PENDIENTE (estamos verificando tu comprobante).",
            "Puedes consultar tu compra en “Mis boletos” usando tu teléfono y el código.",
            "",
            "— GanaHoyRD",
        ]
    )
    _send_async(EmailMessage(subject=subject, body=body, to=[to_email]))


def send_customer_purchase_status(*, purchase: TicketPurchase) -> None:
    """
    Customer email when purchase is approved/rejected.
    """
    if not _should_send_customer_emails():
        return
    to_email = (purchase.email or "").strip()
    if not to_email:
        return

    status = purchase.status
    raffle = purchase.raffle
    subject = f"Actualización de tu compra - {raffle.title}"

    lines = [
        "Actualización de tu compra en GanaHoyRD",
        "",
        f"Rifa: {raffle.title}",
        f"Código de compra: {purchase.public_reference}",
    ]

    if status == TicketPurchase.Status.APPROVED:
        # Ticket numbers are created when approved.
        nums = []
        try:
            for t in purchase.tickets.order_by("number").all()[:200]:
                nums.append(getattr(t, "display_number", str(t.number)))
        except Exception:
            nums = []

        lines += [
            "",
            "Estado: APROBADA",
            f"Boletos pagados: {purchase.quantity}",
            f"Boletos gratis: {purchase.bonus_quantity}",
            f"Total boletos: {purchase.total_tickets}",
        ]
        if nums:
            lines += [
                "",
                "Tus números de boletos:",
                ", ".join(nums) + ("" if len(nums) < 200 else " ..."),
            ]
    elif status == TicketPurchase.Status.REJECTED:
        lines += [
            "",
            "Estado: RECHAZADA",
        ]
        notes = (purchase.admin_notes or "").strip()
        if notes:
            lines += ["Motivo:", notes]
    else:
        # Shouldn't happen here, but keep safe.
        lines += ["", f"Estado: {status}"]

    lines += ["", "— GanaHoyRD"]
    _send_async(EmailMessage(subject=subject, body="\n".join(lines), to=[to_email]))

