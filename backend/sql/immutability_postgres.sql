-- Apply after migrations create evaluations and question_marks.
CREATE OR REPLACE FUNCTION sdvs_reject_submitted_mark_mutation() RETURNS trigger AS $$
DECLARE target_eval text;
BEGIN
    target_eval := COALESCE(NEW.evaluation_id, OLD.evaluation_id);
    IF EXISTS (
        SELECT 1 FROM evaluations
        WHERE id = target_eval AND status IN ('SUBMITTED', 'APPROVED')
    ) THEN
        RAISE EXCEPTION 'Submitted valuation is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sdvs_immutable_marks ON question_marks;
CREATE TRIGGER trg_sdvs_immutable_marks
BEFORE UPDATE OR DELETE ON question_marks
FOR EACH ROW EXECUTE FUNCTION sdvs_reject_submitted_mark_mutation();

CREATE OR REPLACE FUNCTION sdvs_reject_submitted_eval_mutation() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('SUBMITTED', 'APPROVED') THEN
        RAISE EXCEPTION 'Submitted valuation is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sdvs_immutable_evaluation ON evaluations;
CREATE TRIGGER trg_sdvs_immutable_evaluation
BEFORE UPDATE OR DELETE ON evaluations
FOR EACH ROW EXECUTE FUNCTION sdvs_reject_submitted_eval_mutation();


-- Runtime identity guards (Alembic revision 0003).
-- Run as sdvs_migrator after provisioning the restricted database roles.
CREATE OR REPLACE FUNCTION sdvs_reject_user_identity_mutation() RETURNS trigger AS $sdvs$
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

DROP TRIGGER IF EXISTS trg_sdvs_protect_user_identity ON users;
CREATE TRIGGER trg_sdvs_protect_user_identity
BEFORE UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION sdvs_reject_user_identity_mutation();

CREATE OR REPLACE FUNCTION sdvs_reject_profile_identity_mutation() RETURNS trigger AS $sdvs$
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

DROP TRIGGER IF EXISTS trg_sdvs_protect_profile_identity ON user_profiles;
CREATE TRIGGER trg_sdvs_protect_profile_identity
BEFORE UPDATE ON user_profiles
FOR EACH ROW EXECUTE FUNCTION sdvs_reject_profile_identity_mutation();
