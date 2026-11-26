import os
import unittest
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import assigned_pdf, assigned_questions, assigned_script, get_evaluation, get_evaluation_for_update, get_otp_request_user_for_update
from app.models import Allocation, Evaluation, EvaluationScript, Examination, Institution, Question, QuestionMark, Role, Status, Subject, User


class PostgresOtpLockingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL must point to the disposable PostgreSQL CI service."
            )
        cls.engine = create_engine(database_url, pool_pre_ping=True)
        if cls.engine.dialect.name != "postgresql":
            raise RuntimeError("OTP row-lock integration tests require PostgreSQL.")
        Base.metadata.create_all(bind=cls.engine)
        cls.SessionLocal = sessionmaker(
            bind=cls.engine, autoflush=False, autocommit=False
        )

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=cls.engine)
        cls.engine.dispose()

    def test_account_row_lock_blocks_competing_transaction_until_commit(self):
        suffix = uuid.uuid4().hex
        with self.SessionLocal() as db:
            institution = Institution(
                name="SDVS PostgreSQL lock test",
                code=f"PGLOCK-{suffix}",
            )
            db.add(institution)
            db.flush()
            user = User(
                institution_id=institution.id,
                username=f"lock-test-{suffix}",
                password_hash="not-a-real-password",
                role=Role.SUPER_ADMIN,
            )
            db.add(user)
            db.commit()
            user_id = user.id
            institution_id = institution.id

        try:
            with self.SessionLocal() as first, self.SessionLocal() as second:
                first.begin()
                locked_user = get_otp_request_user_for_update(first, user_id)
                self.assertIsNotNone(locked_user)
                self.assertEqual(locked_user.id, user_id)

                second.begin()
                second.execute(text("SET LOCAL lock_timeout = '250ms'"))
                with self.assertRaises(OperationalError):
                    get_otp_request_user_for_update(second, user_id)

                second.rollback()
                first.commit()

                second.begin()
                released_user = get_otp_request_user_for_update(second, user_id)
                self.assertIsNotNone(released_user)
                self.assertEqual(released_user.id, user_id)
                second.rollback()
        finally:
            with self.SessionLocal() as db:
                db.query(User).filter_by(id=user_id).delete(synchronize_session=False)
                db.query(Institution).filter_by(id=institution_id).delete(
                    synchronize_session=False
                )
                db.commit()


    def test_concurrent_otp_requests_serialize_account_rate_limit(self):
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime, timedelta, timezone
        from threading import Event, Lock
        import time
        from unittest.mock import patch

        from fastapi import HTTPException
        from app.main import get_otp_request_user_for_update as lock_user, request_otp
        from app.models import OTPChallenge, UserProfile
        from app.schemas import OTPRequestIn

        suffix = uuid.uuid4().hex
        email = f"otp-rate-{suffix}@example.test"
        phone = "+91" + str(int(suffix[:10], 16)).zfill(10)
        now = datetime.now(timezone.utc)

        with self.SessionLocal() as db:
            institution = Institution(
                name="SDVS concurrent OTP rate-limit test",
                code=f"OTPRATE-{suffix}",
            )
            db.add(institution)
            db.flush()
            user = User(
                institution_id=institution.id,
                username=f"otp-rate-{suffix}",
                password_hash="not-a-real-password",
                role=Role.SUPER_ADMIN,
            )
            db.add(user)
            db.flush()
            db.add(
                UserProfile(
                    user_id=user.id,
                    full_name="Fictional OTP Rate Test",
                    email=email,
                    phone=phone,
                )
            )
            db.add(
                OTPChallenge(
                    user_id=user.id,
                    batch_id=f"seed-{suffix[:20]}",
                    channel="email",
                    code_hash="0" * 64,
                    expires_at=now + timedelta(minutes=5),
                    created_at=now,
                )
            )
            db.commit()
            user_id = user.id
            institution_id = institution.id

        first_lock_acquired = Event()
        release_first_lock = Event()
        second_lock_attempted = Event()
        call_counter_lock = Lock()
        call_count = 0

        def coordinated_lock(db, requested_user_id):
            nonlocal call_count
            with call_counter_lock:
                call_count += 1
                call_number = call_count

            if call_number == 1:
                locked_user = lock_user(db, requested_user_id)
                first_lock_acquired.set()
                if not release_first_lock.wait(timeout=5):
                    raise TimeoutError("Timed out waiting to release the first OTP account lock.")
                return locked_user

            if call_number == 2:
                second_lock_attempted.set()
            return lock_user(db, requested_user_id)

        def issue_request(application_name):
            with self.SessionLocal() as db:
                db.execute(text(f"SET application_name = '{application_name}'"))
                try:
                    request_otp(OTPRequestIn(email=email), db)
                    return 200
                except HTTPException as exc:
                    db.rollback()
                    return exc.status_code
                finally:
                    db.rollback()
                    db.execute(text("RESET application_name"))
                    db.commit()

        try:
            with (
                patch("app.main.get_otp_request_user_for_update", side_effect=coordinated_lock),
                patch("app.main.send_email_otp") as email_delivery,
                patch("app.main.send_sms_otp") as sms_delivery,
            ):
                with ThreadPoolExecutor(max_workers=2, thread_name_prefix="sdvs-otp-rate") as pool:
                    first_future = pool.submit(issue_request, "sdvs_otp_first")
                    second_future = None
                    try:
                        self.assertTrue(
                            first_lock_acquired.wait(timeout=5),
                            "First OTP request did not acquire the account lock.",
                        )
                        second_future = pool.submit(issue_request, "sdvs_otp_second")
                        self.assertTrue(
                            second_lock_attempted.wait(timeout=5),
                            "Second OTP request did not reach the account-lock helper.",
                        )

                        deadline = time.monotonic() + 5
                        second_waiting_on_postgres_lock = False
                        while time.monotonic() < deadline and not second_future.done():
                            with self.SessionLocal() as observer:
                                waiters = observer.scalar(
                                    text(
                                        "SELECT count(*) FROM pg_stat_activity "
                                        "WHERE application_name = 'sdvs_otp_second' "
                                        "AND wait_event_type = 'Lock'"
                                    )
                                )
                            if waiters:
                                second_waiting_on_postgres_lock = True
                                break
                            time.sleep(0.02)

                        self.assertTrue(
                            second_waiting_on_postgres_lock,
                            "Second OTP request did not block on the first request's PostgreSQL account lock.",
                        )
                        self.assertFalse(
                            second_future.done(),
                            "Second OTP request completed while the first request still held the account lock.",
                        )
                    finally:
                        release_first_lock.set()

                    self.assertEqual(first_future.result(timeout=5), 200)
                    self.assertIsNotNone(second_future)
                    self.assertEqual(second_future.result(timeout=5), 429)

                email_delivery.assert_called_once()
                sms_delivery.assert_called_once()

            with self.SessionLocal() as db:
                challenges = db.query(OTPChallenge).filter_by(user_id=user_id).all()
                self.assertEqual(len(challenges), 3)
                self.assertEqual(sum(challenge.consumed for challenge in challenges), 1)
                active = [challenge for challenge in challenges if not challenge.consumed]
                self.assertEqual({challenge.channel for challenge in active}, {"email", "phone"})
                self.assertEqual(len({challenge.batch_id for challenge in active}), 1)
        finally:
            with self.SessionLocal() as db:
                db.query(OTPChallenge).filter_by(user_id=user_id).delete(
                    synchronize_session=False
                )
                db.query(UserProfile).filter_by(user_id=user_id).delete(
                    synchronize_session=False
                )
                db.query(User).filter_by(id=user_id).delete(synchronize_session=False)
                db.query(Institution).filter_by(id=institution_id).delete(
                    synchronize_session=False
                )
                db.commit()


    def test_submission_authorization_locks_active_allocation_until_commit(self):
        suffix = uuid.uuid4().hex
        with self.SessionLocal() as db:
            institution = Institution(
                name="SDVS allocation lock test",
                code=f"ALLOCK-{suffix[:20]}",
            )
            db.add(institution)
            db.flush()
            evaluator = User(
                institution_id=institution.id,
                username=f"allocation-lock-{suffix}",
                password_hash="not-a-real-password",
                role=Role.EVALUATOR,
            )
            examination = Examination(
                institution_id=institution.id,
                name="Allocation lock test",
                academic_year="2026-27",
            )
            db.add_all([evaluator, examination])
            db.flush()
            subject = Subject(
                examination_id=examination.id,
                code=f"AL-{suffix[:8]}",
                name="Allocation lock subject",
                max_marks=10,
            )
            db.add(subject)
            db.flush()
            script = EvaluationScript(
                institution_id=institution.id,
                subject_id=subject.id,
                anonymous_code=f"SDVS-AL-{suffix[:12]}",
                object_key="unused-allocation-lock-test.pdf",
                pdf_sha256="0" * 64,
                page_count=1,
            )
            db.add(script)
            db.flush()
            allocation = Allocation(
                script_id=script.id,
                evaluator_id=evaluator.id,
                active=True,
            )
            db.add(allocation)
            db.commit()
            institution_id = institution.id
            evaluator_id = evaluator.id
            examination_id = examination.id
            subject_id = subject.id
            script_id = script.id
            allocation_id = allocation.id

        try:
            with self.SessionLocal() as first, self.SessionLocal() as second:
                first.begin()
                evaluator = first.get(User, evaluator_id)
                authorized_script = assigned_script(
                    script_id, evaluator, first, lock_allocation=True
                )
                self.assertEqual(authorized_script.id, script_id)

                second.begin()
                second.execute(text("SET LOCAL lock_timeout = '250ms'"))
                with self.assertRaises(OperationalError):
                    second.execute(
                        text("UPDATE allocations SET active = false WHERE id = :id"),
                        {"id": allocation_id},
                    )
                second.rollback()
                first.commit()

                second.begin()
                second.execute(
                    text("UPDATE allocations SET active = false WHERE id = :id"),
                    {"id": allocation_id},
                )
                second.commit()

            with self.SessionLocal() as db:
                allocation = db.get(Allocation, allocation_id)
                self.assertFalse(allocation.active)
        finally:
            with self.SessionLocal() as db:
                db.query(Allocation).filter_by(id=allocation_id).delete(
                    synchronize_session=False
                )
                db.query(EvaluationScript).filter_by(id=script_id).delete(
                    synchronize_session=False
                )
                db.query(Subject).filter_by(id=subject_id).delete(
                    synchronize_session=False
                )
                db.query(Examination).filter_by(id=examination_id).delete(
                    synchronize_session=False
                )
                db.query(User).filter_by(id=evaluator_id).delete(
                    synchronize_session=False
                )
                db.query(Institution).filter_by(id=institution_id).delete(
                    synchronize_session=False
                )
                db.commit()


    def test_evaluation_row_lock_blocks_competing_draft_writer_until_commit(self):
        suffix = uuid.uuid4().hex
        with self.SessionLocal() as db:
            institution = Institution(
                name="SDVS evaluation lock test",
                code=f"EVLOCK-{suffix[:20]}",
            )
            db.add(institution)
            db.flush()
            evaluator = User(
                institution_id=institution.id,
                username=f"evaluation-lock-{suffix}",
                password_hash="not-a-real-password",
                role=Role.EVALUATOR,
            )
            examination = Examination(
                institution_id=institution.id,
                name="Evaluation row lock test",
                academic_year="2026-27",
            )
            db.add_all([evaluator, examination])
            db.flush()
            subject = Subject(
                examination_id=examination.id,
                code=f"EV-{suffix[:8]}",
                name="Evaluation row lock subject",
                max_marks=10,
            )
            db.add(subject)
            db.flush()
            script = EvaluationScript(
                institution_id=institution.id,
                subject_id=subject.id,
                anonymous_code=f"SDVS-EV-{suffix[:12]}",
                object_key="unused-evaluation-lock-test.pdf",
                pdf_sha256="0" * 64,
                page_count=1,
            )
            db.add(script)
            db.flush()
            evaluation = Evaluation(
                script_id=script.id,
                evaluator_id=evaluator.id,
                status=Status.DRAFT,
            )
            db.add(evaluation)
            db.commit()
            institution_id = institution.id
            evaluator_id = evaluator.id
            examination_id = examination.id
            subject_id = subject.id
            script_id = script.id
            evaluation_id = evaluation.id

        try:
            with self.SessionLocal() as first, self.SessionLocal() as second:
                first.begin()
                locked = get_evaluation_for_update(
                    first, evaluator_id, evaluation_id=evaluation_id
                )
                self.assertIsNotNone(locked)
                self.assertEqual(locked.status, Status.DRAFT)

                second.begin()
                second.execute(text("SET LOCAL lock_timeout = '250ms'"))
                with self.assertRaises(OperationalError):
                    get_evaluation_for_update(
                        second, evaluator_id, evaluation_id=evaluation_id
                    )
                second.rollback()
                first.commit()

                second.begin()
                released = get_evaluation_for_update(
                    second, evaluator_id, evaluation_id=evaluation_id
                )
                self.assertIsNotNone(released)
                self.assertEqual(released.status, Status.DRAFT)
                second.rollback()
        finally:
            with self.SessionLocal() as db:
                db.query(Evaluation).filter_by(id=evaluation_id).delete(
                    synchronize_session=False
                )
                db.query(EvaluationScript).filter_by(id=script_id).delete(
                    synchronize_session=False
                )
                db.query(Subject).filter_by(id=subject_id).delete(
                    synchronize_session=False
                )
                db.query(Examination).filter_by(id=examination_id).delete(
                    synchronize_session=False
                )
                db.query(User).filter_by(id=evaluator_id).delete(
                    synchronize_session=False
                )
                db.query(Institution).filter_by(id=institution_id).delete(
                    synchronize_session=False
                )
                db.commit()


    def test_pdf_download_holds_active_allocation_lock_until_response_prepared(self):
        import hashlib
        from unittest.mock import patch

        suffix = uuid.uuid4().hex
        pdf_bytes = b"%PDF-1.4\\n% allocation lock fixture\\n%%EOF\\n"
        with self.SessionLocal() as db:
            institution = Institution(
                name="SDVS PDF allocation lock test",
                code=f"PDFLOCK-{suffix[:20]}",
            )
            db.add(institution)
            db.flush()
            evaluator = User(
                institution_id=institution.id,
                username=f"pdf-allocation-lock-{suffix}",
                password_hash="not-a-real-password",
                role=Role.EVALUATOR,
            )
            examination = Examination(
                institution_id=institution.id,
                name="PDF allocation lock test",
                academic_year="2026-27",
            )
            db.add_all([evaluator, examination])
            db.flush()
            subject = Subject(
                examination_id=examination.id,
                code=f"PDFL-{suffix[:8]}",
                name="PDF allocation lock subject",
                max_marks=10,
            )
            db.add(subject)
            db.flush()
            script = EvaluationScript(
                institution_id=institution.id,
                subject_id=subject.id,
                anonymous_code=f"SDVS-PDFL-{suffix[:12]}",
                object_key="unused-pdf-allocation-lock-test.pdf",
                pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
                page_count=1,
            )
            db.add(script)
            db.flush()
            allocation = Allocation(
                script_id=script.id,
                evaluator_id=evaluator.id,
                active=True,
            )
            db.add(allocation)
            db.commit()
            institution_id = institution.id
            evaluator_id = evaluator.id
            examination_id = examination.id
            subject_id = subject.id
            script_id = script.id
            allocation_id = allocation.id

        try:
            with self.SessionLocal() as first, self.SessionLocal() as second:
                first.begin()
                evaluator = first.get(User, evaluator_id)
                with patch("app.main.get_object", return_value=pdf_bytes) as get_object:
                    response = assigned_pdf(script_id, first, evaluator)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "application/pdf")
                get_object.assert_called_once_with("unused-pdf-allocation-lock-test.pdf")

                second.begin()
                second.execute(text("SET LOCAL lock_timeout = '250ms'"))
                with self.assertRaises(OperationalError):
                    second.execute(
                        text("UPDATE allocations SET active = false WHERE id = :id"),
                        {"id": allocation_id},
                    )
                second.rollback()
                first.commit()

                second.begin()
                second.execute(
                    text("UPDATE allocations SET active = false WHERE id = :id"),
                    {"id": allocation_id},
                )
                second.commit()

            with self.SessionLocal() as db:
                allocation = db.get(Allocation, allocation_id)
                self.assertFalse(allocation.active)
        finally:
            with self.SessionLocal() as db:
                db.query(Allocation).filter_by(id=allocation_id).delete(
                    synchronize_session=False
                )
                db.query(EvaluationScript).filter_by(id=script_id).delete(
                    synchronize_session=False
                )
                db.query(Subject).filter_by(id=subject_id).delete(
                    synchronize_session=False
                )
                db.query(Examination).filter_by(id=examination_id).delete(
                    synchronize_session=False
                )
                db.query(User).filter_by(id=evaluator_id).delete(
                    synchronize_session=False
                )
                db.query(Institution).filter_by(id=institution_id).delete(
                    synchronize_session=False
                )
                db.commit()


    def test_evaluator_read_endpoints_hold_active_allocation_lock_until_response_prepared(self):
        suffix = uuid.uuid4().hex
        with self.SessionLocal() as db:
            institution = Institution(
                name="SDVS evaluator read allocation lock test",
                code=f"READLOCK-{suffix[:20]}",
            )
            db.add(institution)
            db.flush()
            evaluator = User(
                institution_id=institution.id,
                username=f"evaluator-read-lock-{suffix}",
                password_hash="not-a-real-password",
                role=Role.EVALUATOR,
            )
            examination = Examination(
                institution_id=institution.id,
                name="Evaluator read allocation lock test",
                academic_year="2026-27",
            )
            db.add_all([evaluator, examination])
            db.flush()
            subject = Subject(
                examination_id=examination.id,
                code=f"READ-{suffix[:8]}",
                name="Evaluator read allocation lock subject",
                max_marks=10,
            )
            db.add(subject)
            db.flush()
            question = Question(
                subject_id=subject.id,
                question_number="1",
                max_marks=10,
                rubric="Fictional rubric fixture",
            )
            script = EvaluationScript(
                institution_id=institution.id,
                subject_id=subject.id,
                anonymous_code=f"SDVS-READ-{suffix[:12]}",
                object_key="unused-evaluator-read-lock-test.pdf",
                pdf_sha256="0" * 64,
                page_count=1,
            )
            db.add_all([question, script])
            db.flush()
            allocation = Allocation(
                script_id=script.id,
                evaluator_id=evaluator.id,
                active=True,
            )
            evaluation = Evaluation(
                script_id=script.id,
                evaluator_id=evaluator.id,
                status=Status.DRAFT,
            )
            db.add_all([allocation, evaluation])
            db.flush()
            mark = QuestionMark(
                evaluation_id=evaluation.id,
                question_id=question.id,
                marks=5,
                remark="Fictional remark",
                private_note="Fictional private note",
            )
            db.add(mark)
            db.commit()
            institution_id = institution.id
            evaluator_id = evaluator.id
            examination_id = examination.id
            subject_id = subject.id
            question_id = question.id
            script_id = script.id
            allocation_id = allocation.id
            evaluation_id = evaluation.id

        try:
            for endpoint in (assigned_questions, get_evaluation):
                with self.SessionLocal() as first, self.SessionLocal() as second:
                    first.begin()
                    evaluator = first.get(User, evaluator_id)
                    response = endpoint(script_id, first, evaluator)
                    if endpoint is assigned_questions:
                        self.assertEqual([item["id"] for item in response], [question_id])
                    else:
                        self.assertEqual(response["status"], Status.DRAFT.value)
                        self.assertEqual(response["marks"][0]["question_id"], question_id)

                    second.begin()
                    second.execute(text("SET LOCAL lock_timeout = '250ms'"))
                    with self.assertRaises(OperationalError):
                        second.execute(
                            text("UPDATE allocations SET active = false WHERE id = :id"),
                            {"id": allocation_id},
                        )
                    second.rollback()
                    first.commit()

                    second.begin()
                    second.execute(
                        text("UPDATE allocations SET active = false WHERE id = :id"),
                        {"id": allocation_id},
                    )
                    second.commit()

                with self.SessionLocal() as db:
                    self.assertFalse(db.get(Allocation, allocation_id).active)
                    db.query(Allocation).filter_by(id=allocation_id).update(
                        {"active": True}, synchronize_session=False
                    )
                    db.commit()
        finally:
            with self.SessionLocal() as db:
                db.query(QuestionMark).filter_by(evaluation_id=evaluation_id).delete(
                    synchronize_session=False
                )
                db.query(Evaluation).filter_by(id=evaluation_id).delete(
                    synchronize_session=False
                )
                db.query(Allocation).filter_by(id=allocation_id).delete(
                    synchronize_session=False
                )
                db.query(EvaluationScript).filter_by(id=script_id).delete(
                    synchronize_session=False
                )
                db.query(Question).filter_by(id=question_id).delete(
                    synchronize_session=False
                )
                db.query(Subject).filter_by(id=subject_id).delete(
                    synchronize_session=False
                )
                db.query(Examination).filter_by(id=examination_id).delete(
                    synchronize_session=False
                )
                db.query(User).filter_by(id=evaluator_id).delete(
                    synchronize_session=False
                )
                db.query(Institution).filter_by(id=institution_id).delete(
                    synchronize_session=False
                )
                db.commit()
