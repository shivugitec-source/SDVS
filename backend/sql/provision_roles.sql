\set ON_ERROR_STOP on
\getenv migrator_password SDVS_MIGRATOR_PASSWORD
\getenv app_password SDVS_APP_PASSWORD

SELECT length(:'migrator_password') >= 32 AS migrator_password_ok \gset
SELECT length(:'app_password') >= 32 AS app_password_ok \gset
SELECT :'migrator_password' <> :'app_password' AS passwords_are_distinct \gset

\if :migrator_password_ok
\else
  \echo 'SDVS_MIGRATOR_PASSWORD must contain at least 32 characters.'
  \quit 3
\endif
\if :app_password_ok
\else
  \echo 'SDVS_APP_PASSWORD must contain at least 32 characters.'
  \quit 3
\endif
\if :passwords_are_distinct
\else
  \echo 'The SDVS migrator and runtime passwords must be different.'
  \quit 3
\endif

SELECT format(
    'CREATE ROLE sdvs_migrator WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
    :'migrator_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdvs_migrator')
\gexec

SELECT format(
    'CREATE ROLE sdvs_app WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdvs_app')
\gexec

ALTER ROLE sdvs_migrator WITH LOGIN PASSWORD :'migrator_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE sdvs_app WITH LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

-- Neither service role may inherit or SET ROLE to a bootstrap or peer role.
DO $sdvs$
DECLARE membership record;
BEGIN
    FOR membership IN
        SELECT granted.rolname AS granted_role, member.rolname AS member_role
        FROM pg_auth_members AS m
        JOIN pg_roles AS granted ON granted.oid = m.roleid
        JOIN pg_roles AS member ON member.oid = m.member
        WHERE member.rolname IN ('sdvs_app', 'sdvs_migrator')
    LOOP
        EXECUTE format('REVOKE %I FROM %I', membership.granted_role, membership.member_role);
    END LOOP;
END
$sdvs$;

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM sdvs_app, sdvs_migrator', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO sdvs_migrator, sdvs_app', current_database()) \gexec

-- The migrator owns the application schema; the runtime can only use it.
ALTER SCHEMA public OWNER TO sdvs_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM sdvs_app;
GRANT USAGE, CREATE ON SCHEMA public TO sdvs_migrator;
GRANT USAGE ON SCHEMA public TO sdvs_app;

-- Adopt existing SDVS objects in this dedicated database without changing data.
-- The script is scoped to public; review custom objects before using it on a
-- shared database.
DO $sdvs$
DECLARE object_record record;
BEGIN
    FOR object_record IN
        SELECT schemaname, tablename FROM pg_tables WHERE schemaname = 'public'
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I OWNER TO sdvs_migrator',
            object_record.schemaname, object_record.tablename
        );
    END LOOP;

    FOR object_record IN
        SELECT sequence_schema, sequence_name
        FROM information_schema.sequences
        WHERE sequence_schema = 'public'
    LOOP
        EXECUTE format(
            'ALTER SEQUENCE %I.%I OWNER TO sdvs_migrator',
            object_record.sequence_schema, object_record.sequence_name
        );
    END LOOP;

    FOR object_record IN
        SELECT n.nspname, p.proname,
               pg_get_function_identity_arguments(p.oid) AS identity_arguments,
               p.prokind
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
    LOOP
        IF object_record.prokind = 'p' THEN
            EXECUTE format(
                'ALTER PROCEDURE %I.%I(%s) OWNER TO sdvs_migrator',
                object_record.nspname, object_record.proname,
                object_record.identity_arguments
            );
        ELSE
            EXECUTE format(
                'ALTER FUNCTION %I.%I(%s) OWNER TO sdvs_migrator',
                object_record.nspname, object_record.proname,
                object_record.identity_arguments
            );
        END IF;
    END LOOP;

    FOR object_record IN
        SELECT n.nspname, t.typname, t.typtype
        FROM pg_type AS t
        JOIN pg_namespace AS n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd')
    LOOP
        IF object_record.typtype = 'd' THEN
            EXECUTE format(
                'ALTER DOMAIN %I.%I OWNER TO sdvs_migrator',
                object_record.nspname, object_record.typname
            );
        ELSE
            EXECUTE format(
                'ALTER TYPE %I.%I OWNER TO sdvs_migrator',
                object_record.nspname, object_record.typname
            );
        END IF;
    END LOOP;
END
$sdvs$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sdvs_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM sdvs_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sdvs_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO sdvs_app;

-- Keep the sensitive-table restrictions in force even when provisioning is
-- re-run independently of the migration service.
DO $sdvs$
BEGIN
    IF to_regclass('public.users') IS NOT NULL THEN
        EXECUTE 'REVOKE UPDATE, DELETE ON TABLE public.users FROM sdvs_app';
        EXECUTE 'GRANT UPDATE (active) ON TABLE public.users TO sdvs_app';
    END IF;
    IF to_regclass('public.user_profiles') IS NOT NULL THEN
        EXECUTE 'REVOKE UPDATE, DELETE ON TABLE public.user_profiles FROM sdvs_app';
        EXECUTE 'GRANT UPDATE (email_verified, phone_verified) ON TABLE public.user_profiles TO sdvs_app';
    END IF;
    IF to_regclass('public.audit_events') IS NOT NULL THEN
        EXECUTE 'REVOKE UPDATE, DELETE ON TABLE public.audit_events FROM sdvs_app';
    END IF;
    IF to_regclass('public.alembic_version') IS NOT NULL THEN
        EXECUTE 'REVOKE INSERT, UPDATE, DELETE ON TABLE public.alembic_version FROM sdvs_app';
        EXECUTE 'GRANT SELECT ON TABLE public.alembic_version TO sdvs_app';
    END IF;
END
$sdvs$;

ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM sdvs_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM sdvs_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sdvs_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sdvs_migrator IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO sdvs_app;

SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolbypassrls
FROM pg_roles
WHERE rolname IN ('sdvs_migrator', 'sdvs_app')
ORDER BY rolname;
