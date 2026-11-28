"""Database schema readiness checks for local and PostgreSQL deployments."""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from . import models as _models  # noqa: F401 - register mapped tables
from .db import Base, engine

_REQUIRED_POSTGRES_TRIGGERS = {
    "trg_sdvs_immutable_marks": (
        "question_marks",
        "sdvs_reject_submitted_mark_mutation",
        27,  # BEFORE ROW UPDATE OR DELETE
    ),
    "trg_sdvs_immutable_evaluation": (
        "evaluations",
        "sdvs_reject_submitted_eval_mutation",
        27,  # BEFORE ROW UPDATE OR DELETE
    ),
}


def _verify_postgres_triggers(connection) -> None:
    """Fail closed unless both expected immutability triggers are correctly enabled."""
    rows = connection.execute(
        text(
            """
            SELECT
                t.tgname,
                c.relname AS table_name,
                n.nspname AS table_schema,
                p.proname AS function_name,
                pn.nspname AS function_schema,
                t.tgenabled,
                t.tgtype
            FROM pg_trigger AS t
            JOIN pg_class AS c ON c.oid = t.tgrelid
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_proc AS p ON p.oid = t.tgfoid
            JOIN pg_namespace AS pn ON pn.oid = p.pronamespace
            WHERE NOT t.tgisinternal
              AND n.nspname = current_schema()
              AND t.tgname IN (
                  'trg_sdvs_immutable_marks',
                  'trg_sdvs_immutable_evaluation'
              )
            """
        )
    ).mappings()
    installed = {row["tgname"]: row for row in rows}
    problems = []

    for name, (expected_table, expected_function, expected_type) in _REQUIRED_POSTGRES_TRIGGERS.items():
        trigger = installed.get(name)
        if trigger is None:
            problems.append(f"{name} is missing")
            continue
        if (
            trigger["table_name"] != expected_table
            or trigger["function_name"] != expected_function
            or trigger["function_schema"] != trigger["table_schema"]
            or trigger["tgtype"] != expected_type
        ):
            problems.append(f"{name} definition does not match the required table/function/events")
        if trigger["tgenabled"] not in {"O", "A"}:
            problems.append(f"{name} is disabled (tgenabled={trigger['tgenabled']!r})")

    if problems:
        raise RuntimeError(
            "PostgreSQL immutability trigger verification failed: "
            + "; ".join(problems)
            + ". Run 'alembic upgrade head' and verify database trigger configuration."
        )


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

        if current_revision != expected_revision:
            raise RuntimeError(
                f"Database migration revision is {current_revision!r}; expected "
                f"{expected_revision!r}. Run 'alembic upgrade head' before starting SDVS."
            )
        _verify_postgres_triggers(connection)
