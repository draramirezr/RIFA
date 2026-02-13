from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


class Command(BaseCommand):
    help = "Backup de MySQL usando mysqldump (recomendado para producción)."

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
            default=int(_env("BACKUP_RETENTION_DAYS", "14") or "14"),
            help="Días a mantener backups. Default: 14",
        )
        parser.add_argument(
            "--filename-prefix",
            dest="prefix",
            default="ganahoyrd",
            help="Prefijo de archivo.",
        )

    def handle(self, *args, **options):
        if _env("DB_ENGINE", "sqlite").lower() != "mysql":
            raise CommandError("DB_ENGINE no es mysql. Este comando es solo para MySQL.")

        mysqldump = shutil.which("mysqldump")
        if not mysqldump:
            raise CommandError(
                "No se encontró 'mysqldump' en el PATH. "
                "En Railway instala 'default-mysql-client' (Railpack/apt) o usa backup_data."
            )

        out_dir = options["dir"].strip()
        if out_dir:
            target_dir = Path(out_dir)
        else:
            target_dir = Path(getattr(settings, "MEDIA_ROOT", Path.cwd())) / "backups"
        target_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%d-%H%M%S")
        prefix = (options["prefix"] or "ganahoyrd").strip() or "ganahoyrd"
        out_file = target_dir / f"{prefix}-mysql-{stamp}.sql.gz"

        host = _env("DB_HOST", "127.0.0.1")
        port = _env("DB_PORT", "3306")
        user = _env("DB_USER", "root")
        password = _env("DB_PASSWORD", "")
        name = _env("DB_NAME", "")
        if not name:
            raise CommandError("DB_NAME está vacío.")

        cmd = [
            mysqldump,
            f"--host={host}",
            f"--port={port}",
            f"--user={user}",
            "--single-transaction",
            "--quick",
            "--skip-lock-tables",
            "--routines",
            "--events",
            "--triggers",
            "--hex-blob",
            "--default-character-set=utf8mb4",
            "--set-gtid-purged=OFF",
            name,
        ]

        # Optional SSL
        if _env("DB_SSL", "0") == "1":
            # Not all mysql clients support ssl-mode, but modern ones do.
            cmd.insert(1, "--ssl-mode=REQUIRED")

        env = dict(os.environ)
        if password:
            # Avoid passing password in args (shows in process list)
            env["MYSQL_PWD"] = password

        self.stdout.write(f"Creando MySQL backup en: {out_file}")
        with gzip.open(out_file, "wb", compresslevel=6) as gz:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            assert proc.stdout is not None
            for chunk in iter(lambda: proc.stdout.read(1024 * 64), b""):
                gz.write(chunk)
            _out, err = proc.communicate()
            if proc.returncode != 0:
                try:
                    out_file.unlink(missing_ok=True)  # type: ignore[attr-defined]
                except Exception:
                    pass
                raise CommandError(f"mysqldump falló: {err.decode('utf-8', 'ignore')}")

        size_mb = out_file.stat().st_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(f"Backup MySQL creado ({size_mb:.2f} MB)."))

        # Rotation
        retention_days = int(options["retention_days"] or 14)
        cutoff = now - timedelta(days=retention_days)
        removed = 0
        for p in target_dir.glob(f"{prefix}-mysql-*.sql.gz"):
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    p.unlink()
                    removed += 1
            except Exception:
                continue
        if removed:
            self.stdout.write(self.style.WARNING(f"Rotación: {removed} backups MySQL antiguos eliminados."))

