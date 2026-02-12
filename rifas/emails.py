from __future__ import annotations

import mimetypes
import threading

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import TicketPurchase


def _send_async(email: EmailMessage) -> None:
    """
    Send email in a background thread so web requests don't hang
    if SMTP is slow/unreachable.
    """

    def _runner():
        try:
            email.send(fail_silently=True)
        except Exception:
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

