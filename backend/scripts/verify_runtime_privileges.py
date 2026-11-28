"""Verify that the API database account cannot perform schema or audit administration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import engine
from app.schema import ensure_schema


def _expect_permission_denied(connection, statement: str) -> None:
    try:
        with connection.begin_nested():
            connection.execute(text(statement))
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if sqlstate != "42501":
            raise
    else:
        raise AssertionError(f"Runtime database role unexpectedly executed: {statement}")


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Runtime privilege verification requires PostgreSQL.")

    ensure_schema()

    with engine.connect() as connection:
        checks = connection.execute(
            text(
                """
                SELECT
                    current_user AS role_name,
                    r.rolsuper,
                    r.rolcreatedb,
                    r.rolcreaterole,
                    r.rolinherit,
                    r.rolbypassrls,
                    has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
                    has_database_privilege(current_user, current_database(), 'CREATE') AS database_create,
                    pg_has_role(current_user, 'sdvs_migrator', 'MEMBER') AS migrator_member,
                    has_table_privilege(current_user, 'public.question_marks', 'SELECT') AS marks_select,
                    has_table_privilege(current_user, 'public.question_marks', 'INSERT') AS marks_insert,
                    has_table_privilege(current_user, 'public.question_marks', 'UPDATE') AS marks_update,
                    has_table_privilege(current_user, 'public.question_marks', 'DELETE') AS marks_delete,
                    has_table_privilege(current_user, 'public.alembic_version', 'SELECT') AS version_select,
                    has_table_privilege(current_user, 'public.alembic_version', 'UPDATE') AS version_update,
                    has_column_privilege(current_user, 'public.users', 'active', 'UPDATE') AS user_active_update,
                    has_column_privilege(current_user, 'public.users', 'role', 'UPDATE') AS user_role_update,
                    has_column_privilege(current_user, 'public.users', 'password_hash', 'UPDATE') AS password_hash_update,
                    has_column_privilege(current_user, 'public.user_profiles', 'email_verified', 'UPDATE') AS email_verified_update,
                    has_column_privilege(current_user, 'public.user_profiles', 'phone_verified', 'UPDATE') AS phone_verified_update,
                    has_column_privilege(current_user, 'public.user_profiles', 'email', 'UPDATE') AS profile_email_update,
                    has_table_privilege(current_user, 'public.audit_events', 'SELECT') AS audit_select,
                    has_table_privilege(current_user, 'public.audit_events', 'INSERT') AS audit_insert,
                    has_table_privilege(current_user, 'public.audit_events', 'UPDATE') AS audit_update,
                    has_table_privilege(current_user, 'public.audit_events', 'DELETE') AS audit_delete,
                    has_table_privilege(current_user, 'public.question_marks', 'TRUNCATE') AS marks_truncate,
                    has_table_privilege(current_user, 'public.question_marks', 'TRIGGER') AS marks_trigger,
                    (
                        SELECT count(*) FROM pg_tables
                        WHERE schemaname = 'public' AND tableowner = current_user
                    ) AS owned_table_count
                FROM pg_roles AS r
                WHERE r.rolname = current_user
                """
            )
        ).mappings().one()

        expected_true = (
            "marks_select",
            "marks_insert",
            "marks_update",
            "marks_delete",
            "version_select",
            "user_active_update",
            "email_verified_update",
            "phone_verified_update",
            "audit_select",
            "audit_insert",
        )
        expected_false = (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolinherit",
            "rolbypassrls",
            "schema_create",
            "database_create",
            "migrator_member",
            "version_update",
            "user_role_update",
            "password_hash_update",
            "profile_email_update",
            "audit_update",
            "audit_delete",
            "marks_truncate",
            "marks_trigger",
        )

        if checks["role_name"] != "sdvs_app":
            raise AssertionError(f"Expected sdvs_app, connected as {checks['role_name']!r}.")
        for field in expected_true:
            if not checks[field]:
                raise AssertionError(f"Runtime role is missing required privilege: {field}.")
        for field in expected_false:
            if checks[field]:
                raise AssertionError(f"Runtime role has forbidden privilege: {field}.")
        if checks["owned_table_count"] != 0:
            raise AssertionError("Runtime role owns application tables.")

        denied_statements = (
            "ALTER TABLE public.question_marks DISABLE TRIGGER trg_sdvs_immutable_marks",
            "UPDATE public.alembic_version SET version_num = version_num WHERE false",
            "UPDATE public.users SET role = role WHERE false",
            "UPDATE public.users SET password_hash = password_hash WHERE false",
            "UPDATE public.user_profiles SET email = email WHERE false",
            "UPDATE public.audit_events SET detail = detail WHERE false",
            "DELETE FROM public.audit_events WHERE false",
            "CREATE TABLE public.sdvs_runtime_privilege_probe (id integer)",
        )
        for statement in denied_statements:
            _expect_permission_denied(connection, statement)

    print(
        "Restricted sdvs_app verified: required valuation DML works; "
        "DDL, trigger administration, Alembic mutation, account privilege "
        "changes, and audit mutation are denied."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
