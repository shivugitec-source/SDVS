"""Verify PostgreSQL migration head and submitted-valuation immutability triggers."""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal, engine
from app.models import Evaluation, EvaluationScript, Examination, Institution
from app.models import Question, QuestionMark, Role, Status, Subject, User
from app.schema import _verify_postgres_triggers


def _expect_immutable_rejection(session, statement: str, params: dict[str, str]) -> None:
    try:
        with session.begin_nested():
            session.execute(text(statement), params)
    except DBAPIError as exc:
        if "Submitted valuation is immutable" not in str(exc.orig):
            raise
    else:
        raise AssertionError(f"Immutability trigger did not reject: {statement}")


def _verify_disabled_trigger_fails_closed() -> None:
    """Prove the startup guard rejects a present-but-disabled trigger."""
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE question_marks DISABLE TRIGGER trg_sdvs_immutable_marks")
        )
        try:
            try:
                _verify_postgres_triggers(connection)
            except RuntimeError as exc:
                if "trg_sdvs_immutable_marks is disabled" not in str(exc):
                    raise
            else:
                raise AssertionError("Startup trigger guard accepted a disabled immutability trigger.")
        finally:
            connection.execute(
                text("ALTER TABLE question_marks ENABLE TRIGGER trg_sdvs_immutable_marks")
            )


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Migration trigger verification requires PostgreSQL.")
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        _verify_postgres_triggers(connection)
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()
    if revision != expected_revision:
        raise AssertionError(
            f"Unexpected migration revision: {revision!r}; expected {expected_revision!r}"
        )

    _verify_disabled_trigger_fails_closed()

    session = SessionLocal()
    try:
        session.begin()
        suffix = uuid.uuid4().hex
        institution = Institution(name="Migration verification", code=f"MIG-{suffix[:20]}")
        session.add(institution)
        session.flush()
        evaluator = User(
            institution_id=institution.id,
            username=f"migration-{suffix}@example.test",
            password_hash="not-a-real-password",
            role=Role.EVALUATOR,
        )
        examination = Examination(
            institution_id=institution.id,
            name="Migration verification",
            academic_year="2026-27",
        )
        session.add_all([evaluator, examination])
        session.flush()
        subject = Subject(
            examination_id=examination.id,
            code=f"MIG-{suffix[:8]}",
            name="Migration verification",
            max_marks=10,
        )
        session.add(subject)
        session.flush()
        question = Question(subject_id=subject.id, question_number="1", max_marks=10)
        script = EvaluationScript(
            institution_id=institution.id,
            subject_id=subject.id,
            anonymous_code=f"SDVS-MIG-{suffix[:12]}",
            object_key="migration-verification.pdf",
            pdf_sha256="0" * 64,
            page_count=1,
        )
        session.add_all([question, script])
        session.flush()
        evaluation = Evaluation(
            script_id=script.id,
            evaluator_id=evaluator.id,
            status=Status.SUBMITTED,
            total_marks=5,
        )
        session.add(evaluation)
        session.flush()
        mark = QuestionMark(evaluation_id=evaluation.id, question_id=question.id, marks=5)
        session.add(mark)
        session.flush()

        _expect_immutable_rejection(session, "UPDATE question_marks SET marks = 6 WHERE id = :id", {"id": mark.id})
        _expect_immutable_rejection(session, "DELETE FROM question_marks WHERE id = :id", {"id": mark.id})
        _expect_immutable_rejection(session, "UPDATE evaluations SET total_marks = 6 WHERE id = :id", {"id": evaluation.id})
        _expect_immutable_rejection(session, "DELETE FROM evaluations WHERE id = :id", {"id": evaluation.id})

        draft_evaluation = Evaluation(
            script_id=script.id,
            evaluator_id=evaluator.id,
            status=Status.DRAFT,
            total_marks=5,
        )
        session.add(draft_evaluation)
        session.flush()
        draft_mark = QuestionMark(evaluation_id=draft_evaluation.id, question_id=question.id, marks=5)
        session.add(draft_mark)
        session.flush()
        session.execute(text("DELETE FROM question_marks WHERE id = :id"), {"id": draft_mark.id})
        session.execute(text("DELETE FROM evaluations WHERE id = :id"), {"id": draft_evaluation.id})
        session.rollback()
    finally:
        session.close()

    print(
        "Alembic head, enabled PostgreSQL trigger definitions, disabled-trigger startup "
        "rejection, and submitted/draft mutation behavior verified."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
