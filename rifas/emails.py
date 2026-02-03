from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import TicketPurchase


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

    # Attach proof if reasonably small; otherwise keep URL only.
    try:
        f = purchase.proof_image
        if f and getattr(f, "size", 0) and f.size <= 6 * 1024 * 1024:
            f.open("rb")
            email.attach(
                filename=f.name.rsplit("/", 1)[-1],
                content=f.read(),
                mimetype="image/*",
            )
    except Exception:
        pass

    email.send(fail_silently=True)

