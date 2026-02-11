from django.db import models, transaction
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.utils.text import slugify
import secrets
from django.core.exceptions import ValidationError
from django.conf import settings

from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill, ResizeToFit

from .imagekit_processors import AutoTrim


def validate_video_file(file):
    """
    Basic validation for raffle promo videos.
    Accepts common mobile formats (MP4/WebM/MOV). Duration is validated in admin/form (best effort).
    """
    content_type = getattr(file, "content_type", "") or ""
    name = (getattr(file, "name", "") or "").lower()
    allowed_mimes = {
        "video/mp4",
        "video/webm",
        "video/quicktime",  # .mov (iPhone/Android sometimes)
        "video/x-m4v",      # .m4v
        "video/3gpp",       # .3gp
        "video/3gpp2",      # .3g2
    }
    allowed_exts = (".mp4", ".webm", ".mov", ".m4v", ".3gp", ".3g2")
    mime_ok = content_type in allowed_mimes
    ext_ok = any(name.endswith(ext) for ext in allowed_exts)
    # Some devices/browsers send empty or generic content types.
    if not (mime_ok or ext_ok):
        raise ValidationError("El video debe ser MP4, WebM o MOV.")

    # Reasonable hard size limit to protect server resources (duration is separate)
    hard_limit = 50 * 1024 * 1024  # 50MB
    if getattr(file, "size", 0) > hard_limit:
        raise ValidationError("El video es demasiado grande (máximo 50MB).")


def validate_history_media_file(file):
    """
    Media for raffle history:
    - Winner media (photo/video)
    - Delivery media (photo/video)
    """
    content_type = getattr(file, "content_type", "") or ""
    if content_type.startswith("image/"):
        hard_limit = 25 * 1024 * 1024  # 25MB
        if getattr(file, "size", 0) > hard_limit:
            raise ValidationError("La imagen es demasiado grande (máximo 25MB).")
        return
    name = (getattr(file, "name", "") or "").lower()
    allowed_mimes = {
        "video/mp4",
        "video/webm",
        "video/quicktime",
        "video/x-m4v",
        "video/3gpp",
        "video/3gpp2",
    }
    allowed_exts = (".mp4", ".webm", ".mov", ".m4v", ".3gp", ".3g2")
    if content_type in allowed_mimes or any(name.endswith(ext) for ext in allowed_exts):
        hard_limit = 80 * 1024 * 1024  # 80MB
        if getattr(file, "size", 0) > hard_limit:
            raise ValidationError("El video es demasiado grande (máximo 80MB).")
        return
    raise ValidationError("El archivo debe ser una imagen o un video (MP4/WebM/MOV).")


class Raffle(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField(blank=True)
    draw_date = models.DateTimeField()
    price_per_ticket = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    max_tickets = models.PositiveIntegerField(null=True, blank=True, help_text="Opcional. Límite total de boletos.")
    min_purchase_quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Mínimo de boletos pagados por compra (ej: 1, 5, 10).",
    )
    image = models.ImageField(
        upload_to="raffles/",
        blank=True,
        null=True,
        help_text=(
            "Opcional. Imagen principal.\n"
            "Recomendado: vertical 3:4 (ej: 1200×1600). Mínimo sugerido: 900×1200."
        ),
    )
    # Normalized image for the public carousel (3:4), generated on demand and cached.
    image_carousel = ImageSpecField(
        source="image",
        processors=[AutoTrim(tolerance=12), ResizeToFill(900, 1200)],
        format="JPEG",
        options={"quality": 85},
    )
    video = models.FileField(
        upload_to="raffles/videos/",
        blank=True,
        null=True,
        validators=[validate_video_file],
        help_text="Opcional. Video promocional (MP4/MOV recomendado en H.264, máximo 20 segundos).",
    )
    # History (post-finish)
    show_in_history = models.BooleanField(
        default=True,
        help_text="Si está desactivado, esta rifa no se mostrará en el historial público.",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Opcional. Fecha/hora real cuando se culminó (si difiere de la fecha de sorteo).",
    )
    history_cover_image = models.ImageField(
        upload_to="raffles/history/",
        blank=True,
        null=True,
        help_text=(
            "Opcional. Portada para el historial (una sola foto).\n"
            "Recomendado: horizontal 16:9 (ej: 1200×675 o 1920×1080)."
        ),
    )
    winner_name = models.CharField(
        max_length=120,
        blank=True,
        help_text="Opcional. Si se deja vacío, se toma automáticamente del comprador del boleto ganador.",
    )
    winner_ticket_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Opcional. Número de boleto ganador (para mostrar en el historial).",
    )
    winner_media = models.FileField(
        upload_to="raffles/history/winner/",
        blank=True,
        null=True,
        validators=[validate_history_media_file],
        help_text=(
            "Opcional. Foto o video del ganador(a).\n"
            "Foto recomendado: 3:4 (ej: 1200×1600). Video recomendado: MP4 (H.264)."
        ),
    )
    winner_notes = models.TextField(
        blank=True,
        help_text="Opcional. Observación del ganador(a) (ej: ciudad, IG, etc.).",
    )
    delivery_media = models.FileField(
        upload_to="raffles/history/delivery/",
        blank=True,
        null=True,
        validators=[validate_history_media_file],
        help_text=(
            "Opcional. Foto o video de la entrega del premio.\n"
            "Foto recomendado: 3:4 (ej: 1200×1600). Video recomendado: MP4 (H.264)."
        ),
    )
    delivery_notes = models.TextField(
        blank=True,
        help_text="Opcional. Observación de la entrega (ej: fecha, lugar).",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rifa"
        verbose_name_plural = "Rifas"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:200] or "rifa"
            slug = base
            n = 2
            while Raffle.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        # If admin marks raffle inactive and finished_at is not set, store timestamp.
        if self.pk and not self.is_active and self.finished_at is None:
            self.finished_at = timezone.now()
        super().save(*args, **kwargs)

    @staticmethod
    def _is_video_name(name: str) -> bool:
        n = (name or "").lower()
        return n.endswith(".mp4") or n.endswith(".webm")

    @property
    def winner_is_video(self) -> bool:
        f = getattr(self, "winner_media", None)
        return bool(f and getattr(f, "name", None) and self._is_video_name(f.name))

    @property
    def delivery_is_video(self) -> bool:
        f = getattr(self, "delivery_media", None)
        return bool(f and getattr(f, "name", None) and self._is_video_name(f.name))

    @property
    def winner_ticket_display(self) -> str:
        """
        Winner ticket number formatted with left zero padding based on max_tickets
        (same logic as Ticket.display_number).
        """
        n = getattr(self, "winner_ticket_number", None)
        if not n:
            return ""
        try:
            max_tickets = int(getattr(self, "max_tickets", 0) or 0)
        except Exception:
            max_tickets = 0
        width = max(3, len(str(max_tickets))) if max_tickets else 0
        if width:
            return f"{int(n):0{width}d}"
        return str(int(n))

    @property
    def winner_display_name(self) -> str:
        """
        Winner name to show publicly.
        Priority:
        1) Manual winner_name (if set)
        2) Ticket purchaser full_name for winner_ticket_number (if available)
        """
        if (self.winner_name or "").strip():
            return (self.winner_name or "").strip()
        n = getattr(self, "winner_ticket_number", None)
        if not n:
            return ""
        try:
            from django.apps import apps

            Ticket = apps.get_model("rifas", "Ticket")
            t = (
                Ticket.objects.select_related("purchase")
                .filter(raffle_id=self.id, number=int(n))
                .order_by("-id")
                .first()
            )
            if t and getattr(t, "purchase", None):
                return (getattr(t.purchase, "full_name", "") or "").strip()
        except Exception:
            return ""
        return ""

    @property
    def is_finished(self) -> bool:
        return self.draw_date <= timezone.now()

    @property
    def sold_tickets(self) -> int:
        # Tickets are created when purchases are approved.
        # Use annotation (if present) to avoid N+1 queries in lists.
        annotated = getattr(self, "sold_tickets_annot", None)
        if annotated is not None:
            return int(annotated or 0)
        return self.tickets.count()

    @property
    def sold_percent(self) -> int:
        if not self.max_tickets:
            return 0
        if self.max_tickets <= 0:
            return 0
        return min(100, int((self.sold_tickets / self.max_tickets) * 100))

    @property
    def is_sold_out(self) -> bool:
        return bool(self.max_tickets) and self.sold_tickets >= (self.max_tickets or 0)

    def close_if_sold_out(self):
        """
        Business rule: the raffle runs / ends when 100% of tickets are sold.
        When sold out, mark it inactive.
        """
        if self.is_sold_out and self.is_active:
            self.is_active = False
            if self.finished_at is None:
                self.finished_at = timezone.now()
                self.save(update_fields=["is_active", "finished_at", "updated_at"])
            else:
                self.save(update_fields=["is_active", "updated_at"])

    def get_active_offer(self):
        """
        Returns the best active offer for this raffle (highest bonus).
        """
        now = timezone.now()
        qs = self.offers.filter(is_active=True).filter(
            models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now),
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now),
        )
        return qs.order_by("-bonus_quantity", "-buy_quantity", "-created_at").first()


class TicketPurchase(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        APPROVED = "approved", "Aprobado"
        REJECTED = "rejected", "Rechazado"

    raffle = models.ForeignKey(Raffle, on_delete=models.PROTECT, related_name="purchases")
    full_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=40, db_index=True)
    email = models.EmailField(blank=True)
    bank_account = models.ForeignKey(
        "BankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchases",
        help_text="Banco usado para la transferencia (seleccionado por el cliente).",
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)], help_text="Boletos pagados.")
    bonus_quantity = models.PositiveIntegerField(default=0, help_text="Boletos de oferta (gratis).")
    total_tickets = models.PositiveIntegerField(default=0, help_text="Total de boletos asignados (pagados + oferta).")
    total_amount = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    proof_image = models.ImageField(upload_to="payments/")
    public_reference = models.CharField(
        max_length=12,
        unique=True,
        blank=True,
        help_text="Código para que el cliente consulte su compra (recomendado).",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    admin_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)

    class Meta:
        verbose_name = "Compra de boleto"
        verbose_name_plural = "Compras de boletos"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["raffle", "phone"], name="idx_purchase_raffle_phone"),
            models.Index(fields=["status", "created_at"], name="idx_purchase_status_created"),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} - {self.raffle.title} ({self.quantity})"

    def save(self, *args, **kwargs):
        # Always keep total_amount consistent with raffle price * quantity
        if self.raffle_id and self.quantity:
            self.total_amount = int(self.raffle.price_per_ticket or 0) * int(self.quantity or 0)
        if not self.public_reference:
            self.public_reference = self._generate_reference()
        # Keep total_tickets consistent
        self.total_tickets = int(self.quantity or 0) + int(self.bonus_quantity or 0)
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_reference() -> str:
        # Short, URL-safe, human friendly
        return secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:12].upper()

    def generate_tickets_if_needed(self):
        """
        Create sequential ticket numbers for this purchase if approved.
        Safe to call multiple times (idempotent).
        """
        if self.status != self.Status.APPROVED:
            return
        if self.tickets.count() >= self.total_tickets:
            return

        with transaction.atomic():
            # Validate capacity
            if self.raffle.max_tickets:
                remaining = self.raffle.max_tickets - self.raffle.tickets.count()
                if remaining <= 0:
                    raise ValueError("No quedan boletos disponibles para esta rifa.")
                if (self.total_tickets - self.tickets.count()) > remaining:
                    raise ValueError("No hay suficientes boletos disponibles para completar esta compra.")

            # Lock raffle tickets to avoid duplicate numbers under concurrency (MySQL/InnoDB).
            last = (
                Ticket.objects.select_for_update()
                .filter(raffle=self.raffle)
                .order_by("-number")
                .first()
            )
            start = (last.number if last else 0) + 1
            to_create = []
            existing = self.tickets.count()
            needed = max(0, self.total_tickets - existing)
            for i in range(needed):
                to_create.append(
                    Ticket(
                        raffle=self.raffle,
                        purchase=self,
                        number=start + i,
                    )
                )
            Ticket.objects.bulk_create(to_create)
            # If this approval completes the raffle, close it.
            self.raffle.close_if_sold_out()

    def apply_offer(self):
        offer = self.raffle.get_active_offer()
        bonus = 0
        if offer:
            bonus = offer.bonus_for(self.quantity)
        self.bonus_quantity = bonus
        self.total_tickets = int(self.quantity or 0) + int(bonus or 0)

    def approve(self, notes: str = ""):
        self.apply_offer()
        self.status = self.Status.APPROVED
        self.admin_notes = notes
        self.decided_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "admin_notes",
                "decided_at",
                "total_amount",
                "public_reference",
                "bonus_quantity",
                "total_tickets",
            ]
        )
        self.generate_tickets_if_needed()

    def reject(self, notes: str = ""):
        self.status = self.Status.REJECTED
        self.admin_notes = notes
        self.decided_at = timezone.now()
        self.save(update_fields=["status", "admin_notes", "decided_at"])


class Customer(models.Model):
    """
    Cliente (para campañas futuras).
    Se alimenta automáticamente desde las compras (TicketPurchase).
    """

    phone = models.CharField(max_length=40, unique=True, db_index=True)
    full_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)

    first_purchase_at = models.DateTimeField(null=True, blank=True)
    last_purchase_at = models.DateTimeField(null=True, blank=True)
    total_purchases = models.PositiveIntegerField(default=0)
    total_paid_tickets = models.PositiveIntegerField(default=0)
    total_bonus_tickets = models.PositiveIntegerField(default=0)
    total_amount = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        indexes = [
            models.Index(fields=["email"], name="idx_customer_email"),
            models.Index(fields=["last_purchase_at"], name="idx_customer_last_purchase"),
        ]

    def __str__(self) -> str:
        return f"{self.full_name or self.phone}"

    @classmethod
    def upsert_from_purchase(cls, purchase: "TicketPurchase") -> "Customer":
        phone = (getattr(purchase, "phone", "") or "").strip()
        if not phone:
            raise ValueError("La compra no tiene teléfono.")

        defaults = {
            "full_name": (getattr(purchase, "full_name", "") or "").strip(),
            "email": (getattr(purchase, "email", "") or "").strip(),
        }
        obj, created = cls.objects.get_or_create(phone=phone, defaults=defaults)

        # Update customer profile with latest known data (do not overwrite with blanks)
        changed = False
        if defaults["full_name"] and obj.full_name != defaults["full_name"]:
            obj.full_name = defaults["full_name"]
            changed = True
        if defaults["email"] and obj.email != defaults["email"]:
            obj.email = defaults["email"]
            changed = True

        # Aggregate stats
        created_at = getattr(purchase, "created_at", None)
        if created_at:
            if obj.first_purchase_at is None or created_at < obj.first_purchase_at:
                obj.first_purchase_at = created_at
                changed = True
            if obj.last_purchase_at is None or created_at > obj.last_purchase_at:
                obj.last_purchase_at = created_at
                changed = True

        # Recompute aggregates from DB for correctness (fast enough per save; indexed).
        agg = (
            TicketPurchase.objects.filter(phone=phone)
            .aggregate(
                total_purchases=models.Count("id"),
                total_paid=models.Sum("quantity"),
                total_bonus=models.Sum("bonus_quantity"),
                total_amount=models.Sum("total_amount"),
            )
        )
        obj.total_purchases = int(agg.get("total_purchases") or 0)
        obj.total_paid_tickets = int(agg.get("total_paid") or 0)
        obj.total_bonus_tickets = int(agg.get("total_bonus") or 0)
        obj.total_amount = int(agg.get("total_amount") or 0)
        changed = True

        if changed:
            obj.save()
        return obj


class UserSecurity(models.Model):
    """
    Flags for admin users security controls.
    Used to force password change on next admin login.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="security")
    force_password_change = models.BooleanField(
        default=False,
        help_text="Si está activo, el usuario será obligado a cambiar su contraseña al entrar.",
    )
    password_hash_at_force = models.CharField(
        max_length=128,
        blank=True,
        help_text="Hash de contraseña en el momento de activar el forzado (interno).",
    )
    forced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Seguridad del usuario"
        verbose_name_plural = "Seguridad de usuarios"

    def __str__(self) -> str:
        return f"Seguridad: {self.user}"

    def save(self, *args, **kwargs):
        # When forcing, remember current password hash to detect when it changes.
        if self.force_password_change:
            if not self.password_hash_at_force:
                self.password_hash_at_force = getattr(self.user, "password", "") or ""
            if self.forced_at is None:
                self.forced_at = timezone.now()
        else:
            # Clear metadata when not forcing
            self.password_hash_at_force = ""
            self.forced_at = None
        super().save(*args, **kwargs)


class SiteContent(models.Model):
    """
    Contenido editable para la portada (landing).
    Mantener un solo registro (singleton) editado desde el admin.
    """

    about_title = models.CharField(max_length=120, default="Quiénes somos")
    about_body = models.TextField(
        default="Somos una plataforma de rifas. Participa subiendo tu comprobante de transferencia."
    )

    policy_title = models.CharField(max_length=120, default="Políticas de la rifa")
    policy_body = models.TextField(
        default=(
            "La compra queda PENDIENTE hasta verificación del comprobante.\n"
            "No se aceptan comprobantes ilegibles o alterados.\n"
            "El sorteo se realiza en la fecha indicada en cada rifa."
        )
    )

    payment_title = models.CharField(max_length=120, default="Métodos de pago")
    payment_holder_name = models.CharField(max_length=120, default="Federico A. Grullon")
    payment_account_type = models.CharField(max_length=120, default="Cuenta de Ahorro")
    payment_currency = models.CharField(max_length=10, default="DOP")
    payment_body = models.TextField(
        default=(
            "Transferencia bancaria / Depósito.\n"
            "Zelle.\n"
            "PayPal."
        )
    )

    terms_title = models.CharField(max_length=120, default="Términos y condiciones")
    terms_body = models.TextField(
        default=(
            "Al comprar boletos aceptas nuestros términos y condiciones.\n"
            "La compra queda PENDIENTE hasta verificación del comprobante.\n"
            "No se aceptan comprobantes ilegibles o alterados.\n"
            "El sorteo se realiza en la fecha indicada en cada rifa."
        )
    )

    ceo_name = models.CharField(
        max_length=160,
        default="FEDERICO ANTONIO GRULLON DE LEON",
        help_text="Nombre del CEO (se muestra en el pie de página).",
    )
    ceo_phone = models.CharField(
        max_length=30,
        default="8296058290",
        help_text="Teléfono de contacto del CEO (se muestra en el pie de página).",
    )
    ceo_instagram_url = models.URLField(
        blank=True,
        default="",
        help_text="URL del Instagram del CEO (ej: https://instagram.com/usuario).",
    )
    ceo_tiktok_url = models.URLField(
        blank=True,
        default="",
        help_text="URL del TikTok del CEO (ej: https://tiktok.com/@usuario).",
    )
    site_logo = models.ImageField(
        upload_to="site/",
        blank=True,
        null=True,
        help_text="Logo del sitio. Recomendado: PNG cuadrado 512×512 (fondo transparente).",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Contenido del sitio"
        verbose_name_plural = "Contenido del sitio"

    def __str__(self) -> str:
        return "Contenido del sitio"

    @classmethod
    def get_solo(cls) -> "SiteContent":
        obj = cls.objects.order_by("-updated_at").first()
        if obj:
            return obj
        return cls.objects.create()


class BankAccount(models.Model):
    bank_name = models.CharField(max_length=80)
    account_number = models.CharField(max_length=40)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    # Logo placeholder (optional). User can upload later.
    logo = models.ImageField(
        upload_to="banks/",
        blank=True,
        null=True,
        help_text="Logo del banco. Recomendado: PNG cuadrado 512×512.",
    )
    # Normalized logo for payment method icons (square), generated on demand and cached.
    logo_icon = ImageSpecField(
        source="logo",
        processors=[AutoTrim(tolerance=12), ResizeToFit(256, 256)],
        format="PNG",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cuenta bancaria"
        verbose_name_plural = "Cuentas bancarias"
        ordering = ["sort_order", "created_at"]

    def __str__(self) -> str:
        return f"{self.bank_name} - {self.account_number}"

    def clean(self):
        # Enforce max 4 active accounts
        if self.is_active:
            qs = BankAccount.objects.filter(is_active=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.count() >= 4:
                raise ValidationError("Solo se permiten 4 cuentas bancarias activas.")


class Ticket(models.Model):
    raffle = models.ForeignKey(Raffle, on_delete=models.PROTECT, related_name="tickets")
    purchase = models.ForeignKey(TicketPurchase, on_delete=models.CASCADE, related_name="tickets")
    number = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Boleto"
        verbose_name_plural = "Boletos"
        constraints = [
            models.UniqueConstraint(fields=["raffle", "number"], name="uniq_raffle_ticket_number"),
        ]
        ordering = ["number"]

    def __str__(self) -> str:
        return f"{self.raffle.title} - #{self.display_number}"

    @property
    def display_number(self) -> str:
        """
        Human-friendly ticket number with left zero padding.
        Width is derived from raffle.max_tickets, with a minimum of 3 digits:
        - max_tickets <= 999  -> 001, 002, ...
        - max_tickets >= 1000 -> 0001, 0002, ...
        """
        try:
            max_tickets = int(getattr(self.raffle, "max_tickets", 0) or 0)
        except Exception:
            max_tickets = 0
        width = max(3, len(str(max_tickets))) if max_tickets else 0
        if width:
            return f"{int(self.number):0{width}d}"
        return str(self.number)


class RaffleImage(models.Model):
    raffle = models.ForeignKey(Raffle, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(
        upload_to="raffles/",
        help_text="Imagen adicional. Recomendado: vertical 3:4 (ej: 1200×1600). Mínimo sugerido: 900×1200.",
    )
    # Normalized image for the public carousel (3:4), generated on demand and cached.
    image_carousel = ImageSpecField(
        source="image",
        processors=[AutoTrim(tolerance=12), ResizeToFill(900, 1200)],
        format="JPEG",
        options={"quality": 85},
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Imagen de rifa"
        verbose_name_plural = "Imágenes de rifa"
        ordering = ["sort_order", "created_at"]

    def __str__(self) -> str:
        return f"Imagen - {self.raffle.title}"


class RaffleOffer(models.Model):
    raffle = models.ForeignKey(Raffle, on_delete=models.CASCADE, related_name="offers")
    min_paid_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Mínimo de boletos pagados para que aplique la oferta (0 = sin mínimo).",
    )
    buy_quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)], help_text="Compra N")
    bonus_quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)], help_text="Recibe M gratis")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Oferta"
        verbose_name_plural = "Ofertas"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.raffle.title}: compra {self.buy_quantity} y recibe {self.bonus_quantity}"

    def bonus_for(self, paid_qty: int) -> int:
        paid_qty = int(paid_qty or 0)
        if paid_qty <= 0:
            return 0
        min_required = int(self.min_paid_quantity or 0)
        if min_required and paid_qty < min_required:
            return 0
        return (paid_qty // int(self.buy_quantity)) * int(self.bonus_quantity)
