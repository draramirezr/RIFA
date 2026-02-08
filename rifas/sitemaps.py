from __future__ import annotations

from django.db import models
from django.urls import reverse
from django.contrib.sitemaps import Sitemap
from django.utils import timezone

from .models import Raffle


class StaticViewSitemap(Sitemap):
    priority = 0.6
    changefreq = "weekly"

    def items(self):
        return ["rifas:home", "rifas:raffle_history", "rifas:terms"]

    def location(self, item):
        return reverse(item)


class RaffleSitemap(Sitemap):
    priority = 0.8
    changefreq = "daily"

    def items(self):
        # Index active raffles + those visible in history (finished entries).
        return (
            Raffle.objects.filter(models.Q(is_active=True) | models.Q(show_in_history=True))
            .order_by("-updated_at")
        )

    def location(self, obj: Raffle):
        return reverse("rifas:raffle_detail", args=[obj.slug])

    def lastmod(self, obj: Raffle):
        # Best effort for search engines.
        return getattr(obj, "updated_at", None) or getattr(obj, "created_at", None) or timezone.now()


sitemaps = {
    "static": StaticViewSitemap,
    "raffles": RaffleSitemap,
}

