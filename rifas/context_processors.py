from __future__ import annotations

from django.core.cache import cache

from .models import SiteContent


def site_content(request):
    """
    Provide SiteContent globally to templates as `site`.
    Cached to avoid a DB hit on every request.
    """
    key = "site_content_solo_v1"
    site = cache.get(key)
    if site is None:
        site = SiteContent.get_solo()
        cache.set(key, site, 60)  # 60s cache
    return {"site": site}

