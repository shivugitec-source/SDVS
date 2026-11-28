"""Exercise the restricted API database role against a disposable PostgreSQL transaction."""
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import engine
from app.schema import ensure_schema


def _expect_denied(connection, statement: str, parameters: dict | None = None) -> None:
    try:
        with connection.begin_nested():
            connection.execute(text(statement), parameters or {})
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
                    has_table_privilege(current_user, 'public.question_marks', 'TRUNCATE') AS marks_truncate,
                    has_table_privilege(current_user, 'public.question_marks', 'TRIGGER') AS marks_trigger,
                    has_table_privilege(current_user, 'public.users', 'UPDATE') AS users_update,
                    has_table_privilege(current_user, 'public.users', 'DELETE') AS users_delete,
                    has_table_privilege(current_user, 'public.user_profiles', 'UPDATE') AS profiles_update,
                    has_table_privilege(current_user, 'public.user_profiles', 'DELETE') AS profiles_delete,
                    has_table_privilege(current_user, 'public.alembic_version', 'SELECT') AS version_select,
                    has_table_privilege(current_user, 'public.alembic_version', 'INSERT') AS version_insert,
                    has_table_privilege(current_user, 'public.alembic_version', 'UPDATE') AS version_update,
                    has_table_privilege(current_user, 'public.alembic_version', 'DELETE') AS version_delete,
                    has_table_privilege(current_user, 'public.audit_events', 'SELECT') AS audit_select,
                    has_table_privilege(current_user, 'public.audit_events', 'INSERT') AS audit_insert,
                    has_table_privilege(current_user, 'public.audit_events', 'UPDATE') AS audit_update,
                    has_table_privilege(current_user, 'public.audit_events', 'DELETE') AS audit_delete,
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
            "users_update",
            "profiles_update",
            "version_select",
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
            "marks_truncate",
            "marks_trigger",
            "users_delete",
            "profiles_delete",
            "version_insert",
            "version_update",
            "version_delete",
            "audit_update",
            "audit_delete",
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

        # End the read-only privilege-inspection transaction before the
        # rollback-only DML/row-lock exercise below.
        connection.commit()
        institution_id = str(uuid4())
        user_id = str(uuid4())
        profile_id = str(uuid4())
        audit_id = str(uuid4())
        username = f"runtime-check-{user_id}"
        email = f"{user_id}@runtime-check.invalid"
        phone = f"+9100{user_id.replace('-', '')[:10]}"

        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "INSERT INTO public.institutions (id, name, code) "
                    "VALUES (:id, 'Runtime privilege check', :code)"
                ),
                {"id": institution_id, "code": f"rt-{user_id}"},
            )
            connection.execute(
                text(
                    'INSERT INTO public.users '
                    '(id, institution_id, username, password_hash, "role", active) '
                    "VALUES (:id, :institution_id, :username, 'runtime-test-hash', "
                    "'EVALUATOR'::public.\"role\", TRUE)"
                ),
                {"id": user_id, "institution_id": institution_id, "username": username},
            )
            connection.execute(
                text(
                    "INSERT INTO public.user_profiles "
                    "(id, user_id, full_name, email, phone, email_verified, phone_verified) "
                    "VALUES (:id, :user_id, 'Runtime Check', :email, :phone, FALSE, FALSE)"
                ),
                {"id": profile_id, "user_id": user_id, "email": email, "phone": phone},
            )
            connection.execute(
                text(
                    "INSERT INTO public.audit_events "
                    "(id, institution_id, actor_id, action, entity_type, entity_id, detail, created_at) "
                    "VALUES (:id, :institution_id, :actor_id, 'RUNTIME_CHECK', "
                    "'USER', :entity_id, '{}', CURRENT_TIMESTAMP)"
                ),
                {
                    "id": audit_id,
                    "institution_id": institution_id,
                    "actor_id": user_id,
                    "entity_id": user_id,
                },
            )

            # These row locks are used by the OTP and evaluator API paths.
            connection.execute(
                text("SELECT id FROM public.users WHERE id = :id FOR UPDATE"),
                {"id": user_id},
            ).one()
            connection.execute(
                text("SELECT id FROM public.user_profiles WHERE user_id = :id FOR UPDATE"),
                {"id": user_id},
            ).one()

            # The API may update only account activation and OTP verification flags.
            connection.execute(
                text("UPDATE public.users SET active = FALSE WHERE id = :id"),
                {"id": user_id},
            )
            connection.execute(
                text(
                    "UPDATE public.user_profiles "
                    "SET email_verified = TRUE, phone_verified = TRUE WHERE user_id = :id"
                ),
                {"id": user_id},
            )

            # Table-level UPDATE is needed for SELECT FOR UPDATE; database triggers
            # prevent the runtime role from changing identity, credentials, or role.
            _expect_denied(
                connection,
                'UPDATE public.users SET "role" = \'SUPER_ADMIN\'::public."role" WHERE id = :id',
                {"id": user_id},
            )
            _expect_denied(
                connection,
                "UPDATE public.users SET password_hash = 'changed' WHERE id = :id",
                {"id": user_id},
            )
            _expect_denied(
                connection,
                "UPDATE public.users SET username = 'changed' WHERE id = :id",
                {"id": user_id},
            )
            _expect_denied(
                connection,
                "UPDATE public.user_profiles SET email = 'changed@invalid' WHERE user_id = :id",
                {"id": user_id},
            )
            _expect_denied(
                connection,
                "UPDATE public.user_profiles SET full_name = 'Changed' WHERE user_id = :id",
                {"id": user_id},
            )
            _expect_denied(
                connection,
                "UPDATE public.audit_events SET detail = '{}' WHERE id = :id",
                {"id": audit_id},
            )
            _expect_denied(
                connection,
                "DELETE FROM public.audit_events WHERE id = :id",
                {"id": audit_id},
            )
            _expect_denied(
                connection,
                "UPDATE public.alembic_version SET version_num = version_num WHERE false",
            )
            _expect_denied(
                connection,
                "ALTER TABLE public.question_marks DISABLE TRIGGER trg_sdvs_immutable_marks",
            )
            _expect_denied(
                connection,
                "CREATE TABLE public.sdvs_runtime_privilege_probe (id integer)",
            )
        finally:
            transaction.rollback()

    print(
        "Restricted sdvs_app verified: PostgreSQL row locks and required DML work; "
        "identity/credential/role changes, audit mutation, Alembic mutation, "
        "schema DDL, and trigger administration are denied."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
