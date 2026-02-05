from __future__ import annotations

import os

from django.conf import settings
from django.http import Http404
from django.views.static import serve


ALLOWED_PUBLIC_MEDIA_PREFIXES = ("raffles/", "banks/")
ALLOWED_PRIVATE_MEDIA_PREFIXES = ("payments/",)


def _normalize_and_validate_path(path: str) -> str:
    path = (path or "").replace("\\", "/")
    if path.startswith("../") or "/../" in path:
        raise Http404()
    return path


def _safe_serve(request, path: str, *, cache_seconds: int):
    # Extra safety: ensure file stays inside MEDIA_ROOT
    full = os.path.normpath(os.path.join(str(settings.MEDIA_ROOT), path))
    if not full.startswith(os.path.normpath(str(settings.MEDIA_ROOT))):
        raise Http404()
    # Django 6's django.views.static.serve() doesn't accept cache_timeout.
    resp = serve(request, path, document_root=settings.MEDIA_ROOT)
    # Set cache headers explicitly.
    if cache_seconds and cache_seconds > 0:
        resp["Cache-Control"] = f"public, max-age={int(cache_seconds)}"
    return resp


def _private_media(request, path: str):
    # Do NOT redirect; return 404 to avoid leaking existence.
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_staff", False):
        raise Http404()
    return _safe_serve(request, path, cache_seconds=60)


def media_serve(request, path: str):
    """
    Serve media in production without exposing private uploads.

    - Public: raffle images and bank logos (raffles/, banks/)
    - Private: payment proofs (payments/) only for staff users (admin)
    """
    path = _normalize_and_validate_path(path)

    if any(path.startswith(p) for p in ALLOWED_PUBLIC_MEDIA_PREFIXES):
        return _safe_serve(request, path, cache_seconds=60 * 60 * 24 * 30)  # 30 days

    if any(path.startswith(p) for p in ALLOWED_PRIVATE_MEDIA_PREFIXES):
        # Only staff can view payment proofs.
        return _private_media(request, path)

    raise Http404()

