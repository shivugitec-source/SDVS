"""Guard user identity and profile fields from runtime mutation."""
from alembic import op

revision = "0003_runtime_identity_guards"
down_revision = "0002_immutability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        CREATE OR REPLACE FUNCTION sdvs_reject_user_identity_mutation()
        RETURNS trigger AS $sdvs$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.institution_id IS DISTINCT FROM OLD.institution_id
               OR NEW.username IS DISTINCT FROM OLD.username
               OR NEW.password_hash IS DISTINCT FROM OLD.password_hash
               OR NEW.role IS DISTINCT FROM OLD.role THEN
                RAISE EXCEPTION 'Runtime API cannot change user identity, credentials, or role'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END;
        $sdvs$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_sdvs_protect_user_identity ON users")
    op.execute(
        """
        CREATE TRIGGER trg_sdvs_protect_user_identity
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION sdvs_reject_user_identity_mutation();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION sdvs_reject_profile_identity_mutation()
        RETURNS trigger AS $sdvs$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.user_id IS DISTINCT FROM OLD.user_id
               OR NEW.full_name IS DISTINCT FROM OLD.full_name
               OR NEW.email IS DISTINCT FROM OLD.email
               OR NEW.phone IS DISTINCT FROM OLD.phone THEN
                RAISE EXCEPTION 'Runtime API cannot change user profile identity or contact fields'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END;
        $sdvs$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_sdvs_protect_profile_identity ON user_profiles")
    op.execute(
        """
        CREATE TRIGGER trg_sdvs_protect_profile_identity
        BEFORE UPDATE ON user_profiles
        FOR EACH ROW EXECUTE FUNCTION sdvs_reject_profile_identity_mutation();
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS trg_sdvs_protect_user_identity ON users")
    op.execute("DROP TRIGGER IF EXISTS trg_sdvs_protect_profile_identity ON user_profiles")
    op.execute("DROP FUNCTION IF EXISTS sdvs_reject_user_identity_mutation()")
    op.execute("DROP FUNCTION IF EXISTS sdvs_reject_profile_identity_mutation()")
