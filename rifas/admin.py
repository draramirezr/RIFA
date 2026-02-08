from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils.html import format_html
from django.utils import timezone

from .models import BankAccount, Customer, Raffle, RaffleImage, RaffleOffer, SiteContent, Ticket, TicketPurchase, UserSecurity

# Admin UI (Spanish)
admin.site.site_header = "GanaHoyRD — Administración"
admin.site.site_title = "GanaHoyRD — Admin"
admin.site.index_title = "Panel de administración"
# Keep admin usable on mobile: disable the left nav sidebar.
admin.site.enable_nav_sidebar = False


class RaffleOfferInline(admin.TabularInline):
    model = RaffleOffer
    extra = 0
    # El mínimo de compra se configura en la Rifa (separado de la oferta).
    fields = ("is_active", "buy_quantity", "bonus_quantity", "starts_at", "ends_at")


class RaffleImageInline(admin.TabularInline):
    model = RaffleImage
    extra = 0
    fields = ("image", "sort_order")
    ordering = ("sort_order", "created_at")


@admin.register(Raffle)
class RaffleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "draw_date",
        "price_per_ticket",
        "ticket_counter",
        "is_active",
        "show_in_history",
        "created_at",
    )
    list_filter = ("is_active", "show_in_history")
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [RaffleImageInline, RaffleOfferInline]
    actions = ["show_in_history_action", "hide_from_history_action"]

    @admin.action(description="Mostrar en historial")
    def show_in_history_action(self, request, queryset):
        queryset.update(show_in_history=True)

    @admin.action(description="Ocultar del historial")
    def hide_from_history_action(self, request, queryset):
        queryset.update(show_in_history=False)

    def save_model(self, request, obj, form, change):
        # Best-effort: validate raffle video duration <= 20s using metadata.
        # If it fails to read duration, we allow upload but recommend using MP4/WebM.
        if getattr(obj, "video", None):
            try:
                from mutagen import File as MutagenFile  # type: ignore

                f = obj.video.file
                try:
                    f.seek(0)
                except Exception:
                    pass
                meta = MutagenFile(f)
                length = float(getattr(getattr(meta, "info", None), "length", 0) or 0)
                if length and length > 20.0:
                    raise ValidationError("El video debe durar máximo 20 segundos.")
            except ValidationError:
                raise
            except Exception:
                # Don't block admin save for metadata issues.
                pass

            # No transcoding: you will upload MP4/MOV already compatible.
        return super().save_model(request, obj, form, change)

    @admin.display(description="Boletos (vendidos/total)")
    def ticket_counter(self, obj: Raffle):
        if not obj.max_tickets:
            return "—"
        return f"{obj.sold_tickets}/{obj.max_tickets} ({obj.sold_percent}%)"

    def save_formset(self, request, form, formset, change):
        """
        Enforce max 3 images total per raffle:
        - optional cover image (Raffle.image) counts as 1 if set
        - plus inline gallery images (RaffleImage)
        """
        if formset.model is RaffleImage:
            raffle: Raffle = form.instance
            cover_count = 1 if getattr(raffle, "image", None) else 0

            submitted = 0
            for f in formset.forms:
                if not hasattr(f, "cleaned_data"):
                    continue
                if f.cleaned_data.get("DELETE"):
                    continue
                img = f.cleaned_data.get("image") or getattr(f.instance, "image", None)
                if img:
                    submitted += 1

            if cover_count + submitted > 3:
                raise ValidationError(
                    "Máximo 3 fotos por artículo (incluye la imagen principal). "
                    f"Ahora mismo: principal={cover_count}, galería={submitted}."
                )

        return super().save_formset(request, form, formset, change)


@admin.action(description="Aprobar compras seleccionadas")
def approve_purchases(modeladmin, request, queryset):
    for purchase in queryset.select_related("raffle"):
        try:
            purchase.approve()
        except ValueError as e:
            purchase.reject(notes=str(e))
            modeladmin.message_user(
                request,
                f"Compra #{purchase.id} rechazada: {e}",
                level=messages.WARNING,
            )


@admin.action(description="Rechazar compras seleccionadas")
def reject_purchases(modeladmin, request, queryset):
    now = timezone.now()
    queryset.update(status=TicketPurchase.Status.REJECTED, decided_at=now)


class PhonePrefixFilter(admin.SimpleListFilter):
    title = "Prefijo"
    parameter_name = "phone_prefix"

    def lookups(self, request, model_admin):
        return [("809", "809"), ("829", "829"), ("849", "849")]

    def queryset(self, request, queryset):
        val = self.value()
        if not val:
            return queryset
        return queryset.filter(phone__startswith=val)


@admin.register(TicketPurchase)
class TicketPurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "raffle",
        "full_name",
        "phone",
        "bank_account",
        "quantity",
        "promo_preview",
        "bonus_quantity",
        "total_tickets",
        "total_amount",
        "proof_link",
        "status",
        "created_at",
    )
    list_filter = ("status", "raffle", "bank_account", PhonePrefixFilter)
    search_fields = ("full_name", "phone", "email", "raffle__title", "bank_account__bank_name", "bank_account__account_number")
    search_help_text = "Busca por teléfono, nombre, rifa o banco."
    list_select_related = ("raffle", "bank_account")
    readonly_fields = (
        "created_at",
        "decided_at",
        "total_amount",
        "public_reference",
        "bonus_quantity",
        "total_tickets",
        "client_ip",
        "user_agent",
        "proof_preview",
    )
    actions = [approve_purchases, reject_purchases]

    @admin.display(description="Promoción (vista previa)")
    def promo_preview(self, obj: TicketPurchase):
        offer = obj.raffle.get_active_offer() if obj.raffle_id else None
        if not offer:
            return "—"
        est = offer.bonus_for(obj.quantity) if obj.quantity else 0
        if est:
            return f"{offer.buy_quantity}+{offer.bonus_quantity} (gratis estimado: {est})"
        return f"{offer.buy_quantity}+{offer.bonus_quantity}"

    @admin.display(description="Comprobante")
    def proof_link(self, obj: TicketPurchase):
        f = getattr(obj, "proof_image", None)
        if not f or not getattr(f, "name", ""):
            return "—"
        try:
            # If the DB points to a missing file (common on Railway without a Volume),
            # don't show a broken link.
            if not f.storage.exists(f.name):
                return "No disponible"
            url = f.url
        except Exception:
            return "No disponible"
        return format_html('<a href="{}" target="_blank" rel="noopener">Ver</a>', url)

    @admin.display(description="Vista previa del comprobante")
    def proof_preview(self, obj: TicketPurchase):
        f = getattr(obj, "proof_image", None)
        if not f or not getattr(f, "name", ""):
            return "—"
        try:
            if not f.storage.exists(f.name):
                return "No disponible"
            url = f.url
        except Exception:
            return "No disponible"
        return format_html(
            '<a href="{0}" target="_blank" rel="noopener">'
            '<img src="{0}" alt="comprobante" style="max-width:360px; width:100%; border-radius:12px; border:1px solid rgba(255,255,255,.15);" />'
            "</a>",
            url,
        )

    def save_model(self, request, obj, form, change):
        prev_status = None
        if change and obj.pk:
            prev_status = TicketPurchase.objects.filter(pk=obj.pk).values_list("status", flat=True).first()
        super().save_model(request, obj, form, change)
        if obj.status == TicketPurchase.Status.APPROVED and prev_status != TicketPurchase.Status.APPROVED:
            try:
                obj.apply_offer()
                obj.save(update_fields=["bonus_quantity", "total_tickets"])
                obj.generate_tickets_if_needed()
            except ValueError as e:
                obj.reject(notes=str(e))
                self.message_user(request, f"No se pudo aprobar: {e}", level=messages.ERROR)


@admin.register(SiteContent)
class SiteContentAdmin(admin.ModelAdmin):
    list_display = ("updated_at",)
    fieldsets = (
        ("Portada", {"fields": ("about_title", "about_body")}),
        ("Políticas (interno)", {"fields": ("policy_title", "policy_body")}),
        ("Métodos de pago (interno)", {"fields": ("payment_title", "payment_body")}),
        ("Detalles de pago", {"fields": ("payment_holder_name", "payment_account_type", "payment_currency")}),
        ("Términos y condiciones", {"fields": ("terms_title", "terms_body")}),
        ("CEO / Contacto", {"fields": ("ceo_name", "ceo_phone", "ceo_instagram_url", "ceo_tiktok_url")}),
    )


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("bank_name", "account_number", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("bank_name", "account_number")


@admin.action(description="Exportar a Excel (.xlsx)")
def export_customers_xlsx(modeladmin, request, queryset):
    try:
        import openpyxl  # type: ignore
    except Exception:
        modeladmin.message_user(
            request,
            "Falta la dependencia openpyxl para exportar a Excel.",
            level=messages.ERROR,
        )
        return None

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Clientes"

    # Export only customer profile data (no purchase metrics)
    headers = ["Nombre", "Teléfono", "Email", "Creado", "Actualizado"]
    ws.append(headers)

    for c in queryset.order_by("-last_purchase_at", "-updated_at").iterator(chunk_size=1000):
        ws.append(
            [
                c.full_name,
                c.phone,
                c.email,
                c.created_at.isoformat(sep=" ", timespec="seconds") if c.created_at else "",
                c.updated_at.isoformat(sep=" ", timespec="seconds") if c.updated_at else "",
            ]
        )

    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = 'attachment; filename="clientes_ganahoyrd.xlsx"'
    wb.save(resp)
    return resp


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "created_at", "updated_at")
    search_fields = ("full_name", "phone", "email")
    list_filter = ("created_at",)
    actions = [export_customers_xlsx]


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("raffle", "number_display", "purchase", "created_at")
    list_filter = ("raffle",)
    search_fields = ("purchase__full_name", "purchase__phone", "purchase__email", "raffle__title")
    list_select_related = ("raffle", "purchase")
    readonly_fields = ("number_display", "created_at")
    fields = ("raffle", "purchase", "number", "number_display", "created_at")

    @admin.display(description="Boleto")
    def number_display(self, obj: Ticket):
        return obj.display_number

    def get_search_results(self, request, queryset, search_term):
        qs, use_distinct = super().get_search_results(request, queryset, search_term)
        term = (search_term or "").strip()
        digits = "".join(ch for ch in term if ch.isdigit())
        # Search by ticket number (accept 001/0001/etc.)
        if digits:
            try:
                n = int(digits)
                qs = qs | queryset.filter(number=n)
            except Exception:
                pass
        # Search by phone digits (ignore separators)
        if len(digits) >= 7:
            qs = qs | queryset.filter(purchase__phone__icontains=digits)
        return qs, use_distinct


class UserSecurityInline(admin.StackedInline):
    model = UserSecurity
    can_delete = False
    extra = 0
    fields = ("force_password_change", "forced_at")
    readonly_fields = ("forced_at",)
    verbose_name_plural = "Seguridad"


# Extend Django's User admin to include "force password change"
User = get_user_model()
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    inlines = (UserSecurityInline,)

