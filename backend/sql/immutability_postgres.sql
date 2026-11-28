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
    RETURN COALESCE(NEW, OLD);
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
