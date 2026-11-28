"""Database schema readiness checks for local and PostgreSQL deployments."""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from . import models as _models  # noqa: F401 - register mapped tables
from .db import Base, engine

_REQUIRED_POSTGRES_TRIGGERS = {
    "trg_sdvs_immutable_marks",
    "trg_sdvs_immutable_evaluation",
}


def ensure_schema() -> None:
    """Create only local SQLite schema; require current Alembic on PostgreSQL."""
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)
        return
    if engine.dialect.name != "postgresql":
        raise RuntimeError(f"Unsupported database dialect: {engine.dialect.name}")

    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()
    with engine.connect() as connection:
        if not inspect(connection).has_table("alembic_version"):
            raise RuntimeError(
                "PostgreSQL schema is not versioned. Back up the database, run "
                "'python scripts/check_schema_parity.py', and only if it reports "
                "an exact match run 'alembic stamp 0001_initial' followed by "
                "'alembic upgrade head'."
            )
        current_revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
        installed_triggers = set(
            connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE NOT tgisinternal AND tgname IN "
                    "('trg_sdvs_immutable_marks', 'trg_sdvs_immutable_evaluation')"
                )
            ).scalars()
        )

    if current_revision != expected_revision:
        raise RuntimeError(
            f"Database migration revision is {current_revision!r}; expected "
            f"{expected_revision!r}. Run 'alembic upgrade head' before starting SDVS."
        )
    missing_triggers = _REQUIRED_POSTGRES_TRIGGERS - installed_triggers
    if missing_triggers:
        raise RuntimeError(
            "PostgreSQL immutability triggers are missing: "
            f"{', '.join(sorted(missing_triggers))}. Run 'alembic upgrade head'."
        )
