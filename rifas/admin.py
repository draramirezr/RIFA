from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import BankAccount, Raffle, RaffleImage, RaffleOffer, SiteContent, Ticket, TicketPurchase

# Admin UI (Spanish)
admin.site.site_header = "Administración de Rifas"
admin.site.site_title = "Admin - Rifas"
admin.site.index_title = "Panel de administración"


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
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [RaffleImageInline, RaffleOfferInline]

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


@admin.register(TicketPurchase)
class TicketPurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "raffle",
        "full_name",
        "phone",
        "quantity",
        "bonus_quantity",
        "total_tickets",
        "total_amount",
        "status",
        "created_at",
    )
    list_filter = ("status", "raffle")
    search_fields = ("full_name", "phone", "email", "raffle__title")
    readonly_fields = (
        "created_at",
        "decided_at",
        "total_amount",
        "public_reference",
        "bonus_quantity",
        "total_tickets",
        "client_ip",
        "user_agent",
    )
    actions = [approve_purchases, reject_purchases]

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


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("bank_name", "account_number", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("bank_name", "account_number")


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("raffle", "number", "purchase", "created_at")
    list_filter = ("raffle",)
    search_fields = ("purchase__full_name", "purchase__phone", "raffle__title")
