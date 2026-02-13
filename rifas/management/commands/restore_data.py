from __future__ import annotations

import gzip
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Restaura un backup creado por backup_data (loaddata)."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Ruta al archivo .json o .json.gz")
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Ejecuta flush antes de cargar (PELIGROSO, borra datos).",
        )

    def handle(self, *args, **options):
        path = Path(options["path"]).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(str(path))

        if options.get("flush"):
            self.stdout.write(self.style.WARNING("Ejecutando flush..."))
            subprocess.check_call([sys.executable, "manage.py", "flush", "--noinput"])

        self.stdout.write(f"Cargando backup: {path}")
        if path.suffixes[-2:] == [".json", ".gz"]:
            # Django loaddata NO acepta stdin. Descomprimimos a un .json temporal.
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
                    tmp_path = Path(tmp.name)
                    with gzip.open(path, "rb") as f:
                        for chunk in iter(lambda: f.read(1024 * 64), b""):
                            tmp.write(chunk)

                subprocess.check_call([sys.executable, "manage.py", "loaddata", str(tmp_path)])
            finally:
                if tmp_path:
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass

            self.stdout.write(self.style.SUCCESS("Restauración completada."))
            return

        if path.suffix == ".json":
            subprocess.check_call([sys.executable, "manage.py", "loaddata", str(path)])
            self.stdout.write(self.style.SUCCESS("Restauración completada."))
            return

        raise ValueError("Formato no soportado. Usa .json o .json.gz")

