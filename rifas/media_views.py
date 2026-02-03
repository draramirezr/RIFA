from __future__ import annotations

import os

from django.conf import settings
from django.http import Http404
from django.views.static import serve


ALLOWED_PUBLIC_MEDIA_PREFIXES = ("raffles/", "banks/")


def public_media(request, path: str):
    """
    Serve ONLY public media files (raffle images, bank logos).
    Payment proofs remain private and are NOT served.

    Enable in production with env: SERVE_PUBLIC_MEDIA=1
    """
    if not settings.SERVE_PUBLIC_MEDIA and not settings.DEBUG:
        raise Http404()

    # Normalize slashes and prevent traversal
    path = (path or "").replace("\\", "/")
    if path.startswith("../") or "/../" in path:
        raise Http404()

    if not any(path.startswith(p) for p in ALLOWED_PUBLIC_MEDIA_PREFIXES):
        raise Http404()

    # Extra safety: ensure file stays inside MEDIA_ROOT
    full = os.path.normpath(os.path.join(str(settings.MEDIA_ROOT), path))
    if not full.startswith(os.path.normpath(str(settings.MEDIA_ROOT))):
        raise Http404()

    return serve(request, path, document_root=settings.MEDIA_ROOT)

