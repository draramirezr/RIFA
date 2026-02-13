from __future__ import annotations

import gzip
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Crea un backup comprimido (dumpdata) y rota backups antiguos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            dest="dir",
            default="",
            help="Directorio destino. Default: MEDIA_ROOT/backups",
        )
        parser.add_argument(
            "--retention-days",
            dest="retention_days",
            type=int,
            default=int(os.environ.get("BACKUP_RETENTION_DAYS", "14")),
            help="Días a mantener backups. Default: 14",
        )
        parser.add_argument(
            "--filename-prefix",
            dest="prefix",
            default="ganahoyrd",
            help="Prefijo de archivo.",
        )

    def handle(self, *args, **options):
        out_dir = options["dir"].strip()
        if out_dir:
            target_dir = Path(out_dir)
        else:
            target_dir = Path(getattr(settings, "MEDIA_ROOT", Path.cwd())) / "backups"

        target_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%d-%H%M%S")
        prefix = (options["prefix"] or "ganahoyrd").strip() or "ganahoyrd"

        out_file = target_dir / f"{prefix}-backup-{stamp}.json.gz"

        # Use a subprocess and force UTF-8 output to produce loaddata-friendly fixtures.
        cmd = [
            sys.executable,
            "manage.py",
            "dumpdata",
            "--natural-foreign",
            "--natural-primary",
            "--indent",
            "2",
            "--exclude",
            "sessions",
            "--exclude",
            "admin.logentry",
        ]

        self.stdout.write(f"Creando backup en: {out_file}")
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
                tmp_path = Path(tmp.name)
                # Ensure UTF-8 regardless of console/codepage.
                env = dict(os.environ)
                env["PYTHONIOENCODING"] = "utf-8"
                proc = subprocess.Popen(cmd, stdout=tmp, stderr=subprocess.PIPE, env=env)
                _out, err = proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(f"dumpdata falló: {err.decode('utf-8', 'ignore')}")

            with open(tmp_path, "rb") as src, gzip.open(out_file, "wb", compresslevel=6) as gz:
                for chunk in iter(lambda: src.read(1024 * 64), b""):
                    gz.write(chunk)
        except Exception:
            try:
                out_file.unlink(missing_ok=True)  # type: ignore[attr-defined]
            except Exception:
                pass
            raise
        finally:
            if tmp_path:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

        size_mb = out_file.stat().st_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(f"Backup creado ({size_mb:.2f} MB)."))

        # Rotation
        retention_days = int(options["retention_days"] or 14)
        cutoff = now - timedelta(days=retention_days)
        removed = 0
        for p in target_dir.glob(f"{prefix}-backup-*.json.gz"):
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    p.unlink()
                    removed += 1
            except Exception:
                continue
        if removed:
            self.stdout.write(self.style.WARNING(f"Rotación: {removed} backups antiguos eliminados."))

