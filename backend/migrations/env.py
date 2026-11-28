from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, inspect, pool, text

from app import models  # noqa: F401 - register all mapped tables
from app.db import Base, database_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def _apply_runtime_privileges(connection) -> None:
    """Grant only application DML to sdvs_app after each PostgreSQL migration."""
    if connection.dialect.name != "postgresql":
        return

    role = connection.execute(
        text(
            """
            SELECT current_user AS role_name, rolsuper, rolcreatedb,
                   rolcreaterole, rolinherit, rolbypassrls
            FROM pg_roles
            WHERE rolname = current_user
            """
        )
    ).mappings().one()

    if (
        role["role_name"] != "sdvs_migrator"
        or role["rolsuper"]
        or role["rolcreatedb"]
        or role["rolcreaterole"]
        or role["rolinherit"]
        or role["rolbypassrls"]
    ):
        raise RuntimeError(
            "Alembic must run as the restricted sdvs_migrator role, "
            "not as the API runtime or a PostgreSQL administrator."
        )

    schema_owner = connection.execute(
        text("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'")
    ).scalar_one_or_none()
    if schema_owner != "sdvs_migrator":
        raise RuntimeError(
            "The public schema must be owned by sdvs_migrator before migrations run."
        )

    statements = (
        "REVOKE ALL ON SCHEMA public FROM sdvs_app",
        "GRANT USAGE ON SCHEMA public TO sdvs_app",
        "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC",
        "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC",
        "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sdvs_app",
        "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM sdvs_app",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sdvs_app",
        "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO sdvs_app",
        "ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC",
        "ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC",
        "ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public REVOKE ALL ON TABLES FROM sdvs_app",
        "ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public REVOKE ALL ON SEQUENCES FROM sdvs_app",
        "ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sdvs_app",
        "ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO sdvs_app",
    )
    for statement in statements:
        connection.execute(text(statement))

    inspector = inspect(connection)
    restrictions = {
        "users": (
            "REVOKE UPDATE, DELETE ON TABLE public.users FROM sdvs_app",
            "GRANT UPDATE (active) ON TABLE public.users TO sdvs_app",
        ),
        "user_profiles": (
            "REVOKE UPDATE, DELETE ON TABLE public.user_profiles FROM sdvs_app",
            "GRANT UPDATE (email_verified, phone_verified) ON TABLE public.user_profiles TO sdvs_app",
        ),
        "audit_events": (
            "REVOKE UPDATE, DELETE ON TABLE public.audit_events FROM sdvs_app",
        ),
        "alembic_version": (
            "REVOKE INSERT, UPDATE, DELETE ON TABLE public.alembic_version FROM sdvs_app",
            "GRANT SELECT ON TABLE public.alembic_version TO sdvs_app",
        ),
    }
    for table_name, table_statements in restrictions.items():
        if inspector.has_table(table_name, schema="public"):
            for statement in table_statements:
                connection.execute(text(statement))


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
            _apply_runtime_privileges(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
