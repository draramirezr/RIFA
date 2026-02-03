from __future__ import annotations

import os

from django.conf import settings
from django.http import Http404
from django.contrib.admin.views.decorators import staff_member_required
from django.views.static import serve


ALLOWED_PUBLIC_MEDIA_PREFIXES = ("raffles/", "banks/")
ALLOWED_PRIVATE_MEDIA_PREFIXES = ("payments/",)


def _normalize_and_validate_path(path: str) -> str:
    path = (path or "").replace("\\", "/")
    if path.startswith("../") or "/../" in path:
        raise Http404()
    return path


def _safe_serve(request, path: str):
    # Extra safety: ensure file stays inside MEDIA_ROOT
    full = os.path.normpath(os.path.join(str(settings.MEDIA_ROOT), path))
    if not full.startswith(os.path.normpath(str(settings.MEDIA_ROOT))):
        raise Http404()
    return serve(request, path, document_root=settings.MEDIA_ROOT)


@staff_member_required
def _private_media(request, path: str):
    # Hide existence from non-staff users (the decorator redirects to admin login).
    return _safe_serve(request, path)


def media_serve(request, path: str):
    """
    Serve media in production without exposing private uploads.

    - Public: raffle images and bank logos (raffles/, banks/)
    - Private: payment proofs (payments/) only for staff users (admin)
    """
    path = _normalize_and_validate_path(path)

    if any(path.startswith(p) for p in ALLOWED_PUBLIC_MEDIA_PREFIXES):
        return _safe_serve(request, path)

    if any(path.startswith(p) for p in ALLOWED_PRIVATE_MEDIA_PREFIXES):
        # Only staff can view payment proofs.
        return _private_media(request, path)

    raise Http404()

