from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg"))


def should_transcode_to_mp4(uploaded) -> bool:
    """
    Return True for formats that commonly fail to render video frames in browsers
    (MOV/QuickTime, 3GP, etc.). MP4/WebM are usually fine, but some MP4s (HEVC)
    can still fail; we keep this conservative.
    """
    name = (getattr(uploaded, "name", "") or "").lower()
    ctype = (getattr(uploaded, "content_type", "") or "").lower()
    if ctype in {"video/quicktime", "video/3gpp", "video/3gpp2"}:
        return True
    if name.endswith((".mov", ".3gp", ".3g2", ".m4v")):
        return True
    return False


def transcode_to_mp4(
    uploaded,
    *,
    max_seconds: int = 20,
    max_output_bytes: int = 50 * 1024 * 1024,
    width: int = 720,
    timeout_seconds: int = 120,
) -> ContentFile:
    """
    Transcode an uploaded video to MP4 (H.264 + AAC) for maximum browser compatibility.
    Returns a ContentFile ready to assign to a Django FileField.
    """
    if not ffmpeg_available():
        raise ValidationError(
            "Este video necesita conversión para verse en la web, pero ffmpeg no está instalado en el servidor. "
            "En Railway agrega la variable NIXPACKS_PKGS=ffmpeg y redeploy, o sube un MP4 (H.264)."
        )

    in_name = (getattr(uploaded, "name", "") or "video").rsplit("/", 1)[-1]
    base = in_name.rsplit(".", 1)[0]
    out_name = f"{base}.mp4"

    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "in")
        out_path = os.path.join(td, "out.mp4")

        # Write input to disk
        try:
            uploaded.seek(0)
        except Exception:
            pass
        with open(in_path, "wb") as f:
            for chunk in getattr(uploaded, "chunks", None)() if callable(getattr(uploaded, "chunks", None)) else [uploaded.read()]:
                f.write(chunk)

        # ffmpeg command:
        # - limit duration to max_seconds
        # - scale down to width (keep aspect)
        # - encode for compatibility and fast start
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            in_path,
            "-t",
            str(int(max_seconds)),
            "-vf",
            f"scale='min({int(width)},iw)':-2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            out_path,
        ]

        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            raise ValidationError("La conversión del video tardó demasiado. Intenta con un video más corto (máx 20s).")
        except subprocess.CalledProcessError as e:
            err = (e.stderr or b"").decode("utf-8", errors="ignore")[-600:]
            raise ValidationError(f"No se pudo convertir el video. Detalle: {err}")

        size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        if not size:
            raise ValidationError("No se pudo convertir el video (salida vacía).")
        if size > max_output_bytes:
            raise ValidationError("El video convertido quedó muy grande. Intenta con menor resolución.")

        with open(out_path, "rb") as f:
            data = f.read()

    cf = ContentFile(data)
    cf.name = out_name
    return cf

