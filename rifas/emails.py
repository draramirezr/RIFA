from __future__ import annotations

from email.utils import parseaddr
import mimetypes
import threading
import base64
import json
import logging
import urllib.request
import urllib.error

from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.utils import timezone

from .models import TicketPurchase


logger = logging.getLogger(__name__)


def _split_name_email(value: str) -> tuple[str, str]:
    """
    Accepts either:
    - "Name <email@x.com>"
    - "email@x.com"
    Returns (name, email).
    """
    name, email = parseaddr((value or "").strip())
    return (name or "").strip(), (email or "").strip()


def _make_html_email(*, subject: str, to: list[str], text: str, html: str, from_email: str | None = None) -> EmailMultiAlternatives:
    msg = EmailMultiAlternatives(subject=subject, body=text, to=to, from_email=from_email)
    msg.attach_alternative(html, "text/html")
    return msg


def _email_shell(*, title: str, lead: str, body_html: str, cta_text: str | None = None, cta_url: str | None = None) -> str:
    """
    Minimal, modern HTML shell with inline styles (email-client friendly).
    """
    safe_cta = ""
    if cta_text and cta_url:
        safe_cta = f"""
          <div style="margin-top:18px;">
            <a href="{cta_url}" style="display:inline-block;background:#10B981;color:#061017;text-decoration:none;font-weight:700;padding:12px 16px;border-radius:12px;">
              {cta_text}
            </a>
          </div>
        """
    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#0b1020;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;">
    <div style="max-width:600px;margin:0 auto;padding:20px;">
      <div style="background:#0f172a;border:1px solid rgba(255,255,255,.08);border-radius:18px;overflow:hidden;">
        <div style="padding:18px 18px 14px;background:linear-gradient(90deg,#10B981,#059669);color:#061017;">
          <div style="font-size:14px;font-weight:800;letter-spacing:.5px;">GanaHoyRD</div>
          <div style="font-size:20px;font-weight:900;margin-top:2px;">{title}</div>
        </div>
        <div style="padding:18px;color:#e5e7eb;">
          <div style="font-size:14px;line-height:1.55;color:#cbd5e1;">{lead}</div>
          <div style="margin-top:14px;font-size:14px;line-height:1.65;">
            {body_html}
          </div>
          {safe_cta}
          <div style="margin-top:18px;border-top:1px solid rgba(255,255,255,.08);padding-top:12px;font-size:12px;color:#94a3b8;">
            Si no solicitaste esto, puedes ignorar este correo.
          </div>
        </div>
      </div>
      <div style="text-align:center;margin-top:14px;font-size:12px;color:#64748b;">
        © {timezone.now().year} GanaHoyRD
      </div>
    </div>
  </body>
</html>"""


def _send_via_sendgrid_api(email: EmailMessage) -> None:
    """
    Send email through SendGrid Web API (HTTPS). Useful when SMTP is blocked.
    Requires SENDGRID_API_KEY and SENDGRID_USE_API=1.
    """
    api_key = (getattr(settings, "SENDGRID_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("Missing SENDGRID_API_KEY")

    from_raw = (getattr(email, "from_email", "") or "").strip() or (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "")
    from_name, from_email = _split_name_email(from_raw)
    if not from_email:
        raise RuntimeError("Missing from_email")

    to_raw = list(getattr(email, "to", []) or [])
    to_emails: list[str] = []
    for v in to_raw:
        _n, em = _split_name_email(str(v))
        if em:
            to_emails.append(em)
    if not to_emails:
        return

    payload: dict = {
        "personalizations": [{"to": [{"email": e} for e in to_emails]}],
        "from": {"email": from_email, **({"name": from_name} if from_name else {})},
        "subject": getattr(email, "subject", "") or "",
    }

    # Content: include plain + html if available (EmailMultiAlternatives).
    contents: list[dict] = []
    plain = getattr(email, "body", "") or ""
    if plain:
        contents.append({"type": "text/plain", "value": plain})
    for alt in getattr(email, "alternatives", []) or []:
        try:
            content, mimetype = alt
        except Exception:
            continue
        if mimetype == "text/html" and content:
            contents.append({"type": "text/html", "value": content})
    if not contents:
        contents = [{"type": "text/plain", "value": ""}]
    payload["content"] = contents

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


def _send_now(email: EmailMessage) -> tuple[bool, str]:
    """
    Send immediately (synchronous) so admin can show success/failure.
    Returns (ok, error_message).
    """
    try:
        if getattr(settings, "SENDGRID_USE_API", False) and getattr(settings, "SENDGRID_API_KEY", ""):
            _send_via_sendgrid_api(email)
            return True, ""
        # SMTP/backend
        sent = email.send(fail_silently=False)
        return (sent >= 1), ("" if sent else "No se pudo enviar (sent=0).")
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        if getattr(settings, "EMAIL_LOG_ERRORS", False):
            logger.warning("Email send failed: %s", msg, exc_info=True)
        return False, msg


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
        # Keep attachments small so sending is fast (SendGrid API base64 grows size).
        if getattr(f, "size", 0) and f.size > 1024 * 1024:
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

    html = _email_shell(
        title="Nueva compra pendiente",
        lead="Se registró una compra pendiente de aprobación.",
        body_html="<br>".join(
            [
                f"<b>Rifa:</b> {raffle.title}",
                f"<b>Código:</b> {purchase.public_reference}",
                f"<b>Nombre:</b> {purchase.full_name}",
                f"<b>Teléfono:</b> {purchase.phone}",
                f"<b>Email:</b> {purchase.email or '-'}",
                f"<b>Cantidad:</b> {purchase.quantity}",
                f"<b>Total:</b> RD$ {purchase.total_amount}",
                (f"<b>Comprobante:</b> <a style='color:#a7f3d0' href='{proof_url}'>Ver</a>" if proof_url else ""),
            ]
        ),
        cta_text="Ver en el admin",
        cta_url=(request.build_absolute_uri("/admin/") if request else None),
    )
    email = _make_html_email(subject=subject, to=[to_email], text=body, html=html)

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
    site_url = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    html = _email_shell(
        title="Recibimos tu compra",
        lead="Gracias por participar. Tu compra quedó en estado pendiente mientras verificamos el comprobante.",
        body_html="<br>".join(
            [
                f"<b>Rifa:</b> {purchase.raffle.title}",
                f"<b>Código:</b> {purchase.public_reference}",
                f"<b>Boletos pagados:</b> {purchase.quantity}",
                f"<b>Total:</b> RD$ {purchase.total_amount}",
                "<br><b>Estado:</b> PENDIENTE",
            ]
        ),
        cta_text="Ver Mis boletos",
        cta_url=(f"{site_url}/mis-boletos/" if site_url else None),
    )
    _send_async(_make_html_email(subject=subject, to=[to_email], text=body, html=html))


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

    nums: list[str] = []
    if status == TicketPurchase.Status.APPROVED:
        # Ticket numbers are created when approved.
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
    body_text = "\n".join(lines)
    site_url = (getattr(settings, "SITE_URL", "") or "").rstrip("/")

    if status == TicketPurchase.Status.APPROVED:
        nums_html = ""
        if nums:
            shown = ", ".join(nums) + ("" if len(nums) < 200 else " ...")
            nums_html = (
                "<br><br>"
                "<div style='padding:12px 14px;border-radius:14px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.10)'>"
                "<b>Tus números de boletos:</b><br>"
                f"<span style='font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;color:#e5e7eb'>{shown}</span>"
                "<div style='margin-top:8px;font-size:12px;color:#94a3b8'>"
                "Si no ves todos tus números aquí, entra a “Mis boletos” para ver la lista completa."
                "</div>"
                "</div>"
            )
        body_html = "<br>".join(
            [
                f"<b>Rifa:</b> {raffle.title}",
                f"<b>Código:</b> {purchase.public_reference}",
                "<br><b>Estado:</b> APROBADA",
                f"<b>Boletos pagados:</b> {purchase.quantity}",
                f"<b>Boletos gratis:</b> {purchase.bonus_quantity}",
                f"<b>Total boletos:</b> {purchase.total_tickets}",
            ]
        ) + nums_html
    elif status == TicketPurchase.Status.REJECTED:
        notes = (purchase.admin_notes or "").strip()
        body_html = "<br>".join(
            [
                f"<b>Rifa:</b> {raffle.title}",
                f"<b>Código:</b> {purchase.public_reference}",
                "<br><b>Estado:</b> RECHAZADA",
                (f"<br><b>Motivo:</b> {notes}" if notes else ""),
            ]
        )
    else:
        body_html = "<br>".join([f"<b>Rifa:</b> {raffle.title}", f"<b>Código:</b> {purchase.public_reference}", f"<br><b>Estado:</b> {status}"])

    html = _email_shell(
        title="Actualización de tu compra",
        lead="Te informamos el estado de tu compra.",
        body_html=body_html,
        cta_text="Ver Mis boletos",
        cta_url=(f"{site_url}/mis-boletos/" if site_url else None),
    )
    _send_async(_make_html_email(subject=subject, to=[to_email], text=body_text, html=html))


def send_winner_notification(*, raffle, purchase, ticket_display: str, site_url: str | None = None) -> None:
    """
    Email to the winner when raffle winner ticket is assigned in admin.
    """
    if not getattr(settings, "SEND_WINNER_EMAILS", True):
        return
    to_email = (getattr(purchase, "email", "") or "").strip()
    if not to_email:
        return

    subject = f"¡Felicidades! Ganaste la rifa - {getattr(raffle, 'title', '')}"
    text = "\n".join(
        [
            "¡Felicidades!",
            "",
            "Has resultado ganador(a) en GanaHoyRD.",
            f"Rifa: {getattr(raffle, 'title', '')}",
            f"Boleto ganador: #{ticket_display}",
            "",
            "Nos pondremos en contacto contigo para coordinar la entrega.",
            "— GanaHoyRD",
        ]
    )
    base = (site_url or getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    raffle_url = f"{base}/rifa/{getattr(raffle, 'slug', '')}/" if base else None
    html = _email_shell(
        title="¡Felicidades! Eres el ganador(a)",
        lead="Premios reales y ganadores reales. Gracias por participar.",
        body_html="<br>".join(
            [
                f"<b>Rifa:</b> {getattr(raffle, 'title', '')}",
                f"<b>Boleto ganador:</b> #{ticket_display}",
                "<br>Nos pondremos en contacto contigo para coordinar la entrega.",
            ]
        ),
        cta_text="Ver la rifa",
        cta_url=raffle_url,
    )
    _send_async(_make_html_email(subject=subject, to=[to_email], text=text, html=html))


def send_winner_notification_sync(*, raffle, purchase, ticket_display: str, site_url: str | None = None) -> tuple[bool, str]:
    """
    Synchronous winner email (returns success + error for admin feedback).
    """
    if not getattr(settings, "SEND_WINNER_EMAILS", True):
        return False, "Envio de correo al ganador deshabilitado (SEND_WINNER_EMAILS=0)."
    to_email = (getattr(purchase, "email", "") or "").strip()
    if not to_email:
        return False, "El ganador no tiene email."

    subject = f"¡Felicidades! Ganaste la rifa - {getattr(raffle, 'title', '')}"
    text = "\n".join(
        [
            "¡Felicidades!",
            "",
            "Has resultado ganador(a) en GanaHoyRD.",
            f"Rifa: {getattr(raffle, 'title', '')}",
            f"Boleto ganador: #{ticket_display}",
            "",
            "Nos pondremos en contacto contigo para coordinar la entrega.",
            "— GanaHoyRD",
        ]
    )
    base = (site_url or getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    raffle_url = f"{base}/rifa/{getattr(raffle, 'slug', '')}/" if base else None
    html = _email_shell(
        title="¡Felicidades! Eres el ganador(a)",
        lead="Premios reales y ganadores reales. Gracias por participar.",
        body_html="<br>".join(
            [
                f"<b>Rifa:</b> {getattr(raffle, 'title', '')}",
                f"<b>Boleto ganador:</b> #{ticket_display}",
                "<br>Nos pondremos en contacto contigo para coordinar la entrega.",
            ]
        ),
        cta_text="Ver la rifa",
        cta_url=raffle_url,
    )
    email = _make_html_email(subject=subject, to=[to_email], text=text, html=html)
    return _send_now(email)


def send_admin_temporary_password(*, to_email: str, username: str, temp_password: str, site_url: str | None = None) -> tuple[bool, str]:
    """
    Admin password recovery email (temporary password).
    Uses SendGrid API when configured.
    Returns (ok, error_message).
    """
    to_email = (to_email or "").strip()
    if not to_email:
        return False, "Missing to_email"

    base = (site_url or getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    admin_url = f"{base}/admin/" if base else None

    subject = "Recuperación de contraseña (Admin) - GanaHoyRD"
    text = "\n".join(
        [
            "Se solicitó recuperación de contraseña para el panel administrador.",
            "",
            f"Usuario: {username}",
            f"Contraseña temporal: {temp_password}",
            "",
            "Instrucciones:",
            "- Entra a /admin/ con esta contraseña temporal.",
            "- El sistema te obligará a cambiarla inmediatamente.",
            "- Si tú no solicitaste esto, contacta al administrador.",
            "",
            "— GanaHoyRD",
        ]
    )
    html = _email_shell(
        title="Recuperación de contraseña (Admin)",
        lead="Recibimos una solicitud para recuperar tu acceso al panel.",
        body_html="<br>".join(
            [
                f"<b>Usuario:</b> {username}",
                "<br><b>Contraseña temporal:</b>",
                f"<div style='margin-top:6px;padding:10px 12px;border-radius:12px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.10);font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;color:#e5e7eb;'>"
                f"{temp_password}"
                "</div>",
                "<br><b>Instrucciones:</b>",
                "1) Entra al Admin con esta contraseña temporal.",
                "2) El sistema te obligará a cambiarla inmediatamente.",
                "3) Si tú no solicitaste esto, contacta al administrador.",
            ]
        ),
        cta_text="Abrir Admin" if admin_url else None,
        cta_url=admin_url,
    )
    email = _make_html_email(subject=subject, to=[to_email], text=text, html=html)
    return _send_now(email)

