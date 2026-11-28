"""Install PostgreSQL immutability triggers for submitted valuations."""
from alembic import op

revision = "0002_immutability"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
    CREATE OR REPLACE FUNCTION sdvs_reject_submitted_mark_mutation()
    RETURNS trigger AS $$
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
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_sdvs_immutable_marks ON question_marks")
    op.execute("""
    CREATE TRIGGER trg_sdvs_immutable_marks
    BEFORE UPDATE OR DELETE ON question_marks
    FOR EACH ROW EXECUTE FUNCTION sdvs_reject_submitted_mark_mutation();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION sdvs_reject_submitted_eval_mutation()
    RETURNS trigger AS $$
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
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_sdvs_immutable_evaluation ON evaluations")
    op.execute("""
    CREATE TRIGGER trg_sdvs_immutable_evaluation
    BEFORE UPDATE OR DELETE ON evaluations
    FOR EACH ROW EXECUTE FUNCTION sdvs_reject_submitted_eval_mutation();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS trg_sdvs_immutable_marks ON question_marks")
    op.execute("DROP TRIGGER IF EXISTS trg_sdvs_immutable_evaluation ON evaluations")
    op.execute("DROP FUNCTION IF EXISTS sdvs_reject_submitted_mark_mutation()")
    op.execute("DROP FUNCTION IF EXISTS sdvs_reject_submitted_eval_mutation()")
