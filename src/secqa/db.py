"""Connection helpers and a minimal, ordered SQL migration runner."""

import argparse
import contextlib
import logging
from contextlib import contextmanager
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from secqa.config import get_settings

log = logging.getLogger(__name__)


@contextmanager
def connect(url: str | None = None, vector: bool = True):
    with psycopg.connect(url or get_settings().database_url) as conn:
        if vector:
            # The extension doesn't exist yet on the very first migration run.
            with contextlib.suppress(psycopg.ProgrammingError):
                register_vector(conn)
        yield conn


def migrate(url: str | None = None, migrations_dir: Path | None = None) -> list[str]:
    migrations_dir = migrations_dir or get_settings().migrations_dir
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:  # never report success on an empty/missing directory (wrong SECQA_HOME)
        raise FileNotFoundError(f"no migrations found in {migrations_dir}")
    applied_now = []
    with connect(url, vector=False) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        # Serialize concurrent migrators (e.g. two pods starting at once).
        conn.execute("SELECT pg_advisory_xact_lock(4242)")
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in files:
            if path.name in done:
                continue
            log.info("applying migration %s", path.name)
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
        conn.commit()
    return applied_now


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Apply SQL migrations")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    applied = migrate(args.database_url)
    print(f"applied {len(applied)} migration(s): {applied}")


if __name__ == "__main__":
    main()
