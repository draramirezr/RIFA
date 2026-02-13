from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


class Command(BaseCommand):
    help = "Restaura un backup .sql.gz de MySQL usando el cliente 'mysql' (PELIGROSO)."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Ruta al archivo .sql o .sql.gz")

    def handle(self, *args, **options):
        if _env("DB_ENGINE", "sqlite").lower() != "mysql":
            raise CommandError("DB_ENGINE no es mysql. Este comando es solo para MySQL.")

        mysql = shutil.which("mysql")
        if not mysql:
            raise CommandError(
                "No se encontró 'mysql' en el PATH. "
                "Instala 'default-mysql-client' o restaura desde una máquina que lo tenga."
            )

        path = Path(options["path"]).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(str(path))

        host = _env("DB_HOST", "127.0.0.1")
        port = _env("DB_PORT", "3306")
        user = _env("DB_USER", "root")
        password = _env("DB_PASSWORD", "")
        name = _env("DB_NAME", "")
        if not name:
            raise CommandError("DB_NAME está vacío.")

        cmd = [
            mysql,
            f"--host={host}",
            f"--port={port}",
            f"--user={user}",
            name,
        ]
        if _env("DB_SSL", "0") == "1":
            cmd.insert(1, "--ssl-mode=REQUIRED")

        env = dict(os.environ)
        if password:
            env["MYSQL_PWD"] = password

        self.stdout.write(self.style.WARNING("Iniciando restauración MySQL (esto puede sobrescribir datos)."))
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        assert proc.stdin is not None
        try:
            if path.suffixes[-2:] == [".sql", ".gz"]:
                with gzip.open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 64), b""):
                        proc.stdin.write(chunk)
            elif path.suffix == ".sql":
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 64), b""):
                        proc.stdin.write(chunk)
            else:
                raise CommandError("Formato no soportado. Usa .sql o .sql.gz")

            proc.stdin.close()
            out, err = proc.communicate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            raise

        if proc.returncode != 0:
            raise CommandError(err.decode("utf-8", "ignore"))

        self.stdout.write(self.style.SUCCESS("Restauración MySQL completada."))

