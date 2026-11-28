"""Database schema readiness checks for local and PostgreSQL deployments."""
from pathlib import Path
import re

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
    "trg_sdvs_protect_user_identity": (
        "users",
        "sdvs_reject_user_identity_mutation",
        19,  # BEFORE ROW UPDATE
    ),
    "trg_sdvs_protect_profile_identity": (
        "user_profiles",
        "sdvs_reject_profile_identity_mutation",
        19,  # BEFORE ROW UPDATE
    ),
}


def _verify_postgres_runtime_role(connection) -> None:
    """Fail closed unless the API uses the restricted, non-owner runtime role."""
    role = connection.execute(
        text(
            """
            SELECT
                current_user AS role_name,
                r.rolsuper,
                r.rolcreatedb,
                r.rolcreaterole,
                r.rolinherit,
                r.rolbypassrls,
                has_schema_privilege(current_user, 'public', 'USAGE') AS schema_usage,
                has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
                has_database_privilege(current_user, current_database(), 'CREATE') AS database_create,
                pg_has_role(current_user, 'sdvs_migrator', 'MEMBER') AS migrator_member,
                has_table_privilege(current_user, 'public.question_marks', 'UPDATE') AS can_update_marks,
                has_table_privilege(current_user, 'public.users', 'UPDATE') AS can_update_users,
                has_table_privilege(current_user, 'public.users', 'DELETE') AS can_delete_users,
                has_table_privilege(current_user, 'public.user_profiles', 'UPDATE') AS can_update_profiles,
                has_table_privilege(current_user, 'public.user_profiles', 'DELETE') AS can_delete_profiles,
                has_table_privilege(current_user, 'public.alembic_version', 'SELECT') AS can_read_version,
                has_table_privilege(current_user, 'public.alembic_version', 'INSERT') AS can_insert_version,
                has_table_privilege(current_user, 'public.alembic_version', 'UPDATE') AS can_update_version,
                has_table_privilege(current_user, 'public.alembic_version', 'DELETE') AS can_delete_version,
                has_table_privilege(current_user, 'public.audit_events', 'SELECT') AS can_read_audit,
                has_table_privilege(current_user, 'public.audit_events', 'INSERT') AS can_insert_audit,
                has_table_privilege(current_user, 'public.audit_events', 'UPDATE') AS can_update_audit,
                has_table_privilege(current_user, 'public.audit_events', 'DELETE') AS can_delete_audit,
                has_table_privilege(current_user, 'public.question_marks', 'TRUNCATE') AS can_truncate_marks,
                has_table_privilege(current_user, 'public.question_marks', 'TRIGGER') AS can_manage_mark_triggers,
                (
                    SELECT count(*) FROM pg_tables
                    WHERE schemaname = 'public' AND tableowner = current_user
                ) AS owned_table_count
            FROM pg_roles AS r
            WHERE r.rolname = current_user
            """
        )
    ).mappings().one()

    problems = []
    if role["role_name"] != "sdvs_app":
        problems.append(f"API database role is {role['role_name']!r}, not 'sdvs_app'")
    for flag in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolinherit", "rolbypassrls"):
        if role[flag]:
            problems.append(f"runtime role has forbidden PostgreSQL attribute {flag}")
    if not role["schema_usage"]:
        problems.append("runtime role lacks USAGE on schema public")
    if role["schema_create"]:
        problems.append("runtime role can CREATE objects in schema public")
    if role["database_create"]:
        problems.append("runtime role can CREATE schemas in the database")
    if role["migrator_member"]:
        problems.append("runtime role is a member of sdvs_migrator")
    if not role["can_update_marks"] or not role["can_update_users"] or not role["can_update_profiles"]:
        problems.append("runtime role lacks required row-lock/DML UPDATE privileges")
    if role["can_delete_users"] or role["can_delete_profiles"]:
        problems.append("runtime role must not delete users or user profiles")
    if (
        not role["can_read_version"]
        or role["can_insert_version"]
        or role["can_update_version"]
        or role["can_delete_version"]
    ):
        problems.append("runtime role must read but must not modify alembic_version")
    if not role["can_read_audit"] or not role["can_insert_audit"]:
        problems.append("runtime role lacks required audit SELECT/INSERT privileges")
    if role["can_update_audit"] or role["can_delete_audit"]:
        problems.append("runtime role must not modify or delete audit events")
    if role["can_truncate_marks"]:
        problems.append("runtime role must not TRUNCATE valuation marks")
    if role["can_manage_mark_triggers"]:
        problems.append("runtime role must not administer valuation triggers")
    if role["owned_table_count"] != 0:
        problems.append("runtime role owns application tables")

    if problems:
        raise RuntimeError(
            "PostgreSQL runtime role verification failed: "
            + "; ".join(problems)
            + ". Provision sdvs_app/sdvs_migrator and run migrations as sdvs_migrator."
        )


def _verify_postgres_triggers(connection) -> None:
    """Fail closed unless expected immutability and identity-guard triggers are enabled."""
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
                  'trg_sdvs_immutable_evaluation',
                  'trg_sdvs_protect_user_identity',
                  'trg_sdvs_protect_profile_identity'
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
            "PostgreSQL security trigger verification failed: "
            + "; ".join(problems)
            + ". Run 'alembic upgrade head' and verify database trigger configuration."
        )


def _verify_postgres_active_allocation_index(connection) -> None:
    """Fail closed unless only one active row exists per script/evaluator pair."""
    row = connection.execute(
        text(
            """
            SELECT i.indisunique,
                   pg_get_indexdef(i.indexrelid) AS definition,
                   pg_get_expr(i.indpred, i.indrelid) AS predicate
            FROM pg_index AS i
            JOIN pg_class AS index_class ON index_class.oid = i.indexrelid
            JOIN pg_namespace AS namespace ON namespace.oid = index_class.relnamespace
            WHERE namespace.nspname = current_schema()
              AND index_class.relname = 'uq_allocations_active_script_evaluator'
            """
        )
    ).mappings().one_or_none()

    problems = []
    if row is None:
        problems.append("uq_allocations_active_script_evaluator is missing")
    else:
        definition = str(row["definition"]).lower()
        predicate = re.sub(r"[\\s()\\\"]+", "", str(row["predicate"]).lower())
        if not row["indisunique"]:
            problems.append("active-allocation index is not unique")
        if "script_id, evaluator_id" not in definition:
            problems.append("active-allocation index does not cover script_id and evaluator_id")
        if predicate != "activeistrue":
            problems.append("active-allocation index predicate is not active IS TRUE")

    if problems:
        raise RuntimeError(
            "PostgreSQL active-allocation uniqueness verification failed: "
            + "; ".join(problems)
            + ". Run 'alembic upgrade head' before starting SDVS."
        )


def ensure_schema() -> None:
    """Create only local SQLite schema; require current Alembic and restricted role on PostgreSQL."""
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
        _verify_postgres_runtime_role(connection)
        _verify_postgres_triggers(connection)
        _verify_postgres_active_allocation_index(connection)
