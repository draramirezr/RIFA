from __future__ import annotations

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Customer, TicketPurchase, UserSecurity


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_security(sender, instance, created, **kwargs):
    if created:
        UserSecurity.objects.get_or_create(user=instance)


@receiver(post_save, sender=TicketPurchase)
def sync_customer_from_purchase(sender, instance: TicketPurchase, created, **kwargs):
    # Best-effort: keep Customers updated for campaigns.
    try:
        Customer.upsert_from_purchase(instance)
    except Exception:
        # Don't break purchases if customer sync fails.
        pass

