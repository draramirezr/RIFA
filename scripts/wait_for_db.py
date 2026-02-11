from __future__ import annotations

import os
import sys
import time


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


def main() -> int:
    engine = _env("DB_ENGINE", "sqlite").lower()
    if engine != "mysql":
        return 0

    host = _env("DB_HOST", "127.0.0.1")
    port = int(_env("DB_PORT", "3306") or "3306")
    user = _env("DB_USER", "root")
    password = _env("DB_PASSWORD", "")
    db = _env("DB_NAME", "rifa_db")
    use_ssl = _env("DB_SSL", "0") == "1"

    timeout_seconds = int(_env("DB_WAIT_TIMEOUT", "90") or "90")
    sleep_seconds = float(_env("DB_WAIT_SLEEP", "2") or "2")

    try:
        import pymysql  # type: ignore
    except Exception as e:
        print(f"[wait_for_db] PyMySQL not available: {e}", file=sys.stderr)
        return 1

    start = time.time()
    last_err: str | None = None

    while True:
        try:
            conn = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=db,
                charset="utf8mb4",
                connect_timeout=15,
                read_timeout=30,
                write_timeout=30,
                ssl={} if use_ssl else None,
            )
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            finally:
                conn.close()
            print("[wait_for_db] MySQL OK")
            return 0
        except Exception as e:
            last_err = str(e)
            elapsed = time.time() - start
            if elapsed >= timeout_seconds:
                print(f"[wait_for_db] Timeout waiting for MySQL: {last_err}", file=sys.stderr)
                return 1
            print(f"[wait_for_db] Waiting for MySQL... ({int(elapsed)}s) {last_err}", file=sys.stderr)
            time.sleep(sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

