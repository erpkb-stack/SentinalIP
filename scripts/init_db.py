#!/usr/bin/env python3
"""Create the database, then hand the schema over to Alembic.

    python scripts/init_db.py               # create the database, then run alembic
    python scripts/init_db.py --tables      # also create the tables directly and
                                            #   stamp Alembic, for a no-migration run
    python scripts/init_db.py --drop        # DESTRUCTIVE: drop every table first

Alembic owns the schema. This script exists because `alembic upgrade head`
cannot create the database it is meant to connect to.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from app.config import BASE_DIR, settings  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.logging_config import configure_logging, get_logger  # noqa: E402

import app.models  # noqa: E402,F401  (registers every mapper)

configure_logging(settings.log_level)
logger = get_logger("init_db")


def ensure_database() -> None:
    """CREATE DATABASE IF NOT EXISTS - MySQL only; SQLite needs no such step."""
    if settings.is_sqlite:
        return
    server = create_engine(
        settings.server_url_without_db, future=True, isolation_level="AUTOCOMMIT"
    )
    try:
        with server.connect() as conn:
            conn.execute(text(
                f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            ))
    finally:
        server.dispose()
    logger.info("Database `%s` is present.", settings.db_name)


def stamp_alembic_head() -> None:
    """Record that the schema is already at head, so `upgrade head` is a no-op."""
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(os.path.join(BASE_DIR, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(BASE_DIR, "migrations"))
        command.stamp(cfg, "head")
        logger.info("Alembic stamped at head.")
    except Exception as exc:  # pragma: no cover - alembic is optional at this point
        logger.warning(
            "Could not stamp Alembic (%s). Run `alembic stamp head` yourself so "
            "future migrations start from the right place.", exc,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialise the Sentinel IP AI database.")
    parser.add_argument(
        "--tables", action="store_true",
        help="Also create the tables directly from the models and stamp Alembic. "
             "Use this only if you are not running migrations.",
    )
    parser.add_argument(
        "--drop", action="store_true",
        help="Drop all tables before creating them. Destroys all data.",
    )
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args()

    try:
        ensure_database()
    except Exception as exc:
        logger.error(
            "Could not reach the database server at %s:%s as '%s'.\n  %s\n"
            "Check DB_HOST / DB_PORT / DB_USER / DB_PASSWORD in your .env.",
            settings.db_host, settings.db_port, settings.db_user, exc,
        )
        return 2

    if args.drop:
        if not args.yes:
            answer = input(
                f"This will DROP every table in '{settings.db_name}'. "
                "Type 'drop' to confirm: "
            )
            if answer.strip().lower() != "drop":
                logger.info("Aborted.")
                return 1
        logger.warning("Dropping all tables...")
        Base.metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    os.makedirs(settings.upload_dir, exist_ok=True)
    logger.info("Upload directory ready at %s", settings.upload_dir)

    if args.tables:
        Base.metadata.create_all(engine)
        logger.info("Schema created directly from the models: %s tables.",
                    len(Base.metadata.tables))
        stamp_alembic_head()
        print("\nNext:  python scripts/seed.py\n")
        return 0

    existing = set(inspect(engine).get_table_names())
    if "cases" in existing:
        logger.info("Schema already present (%s tables).", len(existing))
        print("\nNext:  alembic upgrade head   (no-op if already current)"
              "\n       python scripts/seed.py\n")
    else:
        print("\nNext:  alembic upgrade head"
              "\n       python scripts/seed.py\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
