import hashlib
import io
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pypdf import PdfWriter

from fastapi.testclient import TestClient
from fastapi import HTTPException
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app, assigned_script, claim_otp_attempt, get_evaluation_for_update, get_otp_request_user_for_update, otp_digest
from app.models import (Allocation, AuditEvent, Candidate, Evaluation, EvaluationScript, Examination, IdentityMapping, Institution, OTPChallenge, Question, QuestionMark, Role, Status, Subject, User, UserProfile)
from app.security import create_token


class AdminAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=cls.engine)
        cls.SessionLocal = sessionmaker(
            bind=cls.engine, autoflush=False, autocommit=False
        )

        with cls.SessionLocal() as db:
            institution = Institution(name="SDVS Test Institution A", code="TEST-A")
            other_institution = Institution(name="SDVS Test Institution B", code="TEST-B")
            db.add_all([institution, other_institution])
            db.flush()

            master = User(
                institution_id=institution.id,
                username="master-a@example.test",
                password_hash="not-used-by-this-test",
                role=Role.SUPER_ADMIN,
            )
            evaluator = User(
                institution_id=institution.id,
                username="evaluator-a@example.test",
                password_hash="not-used-by-this-test",
                role=Role.EVALUATOR,
            )
            inactive_master = User(
                institution_id=institution.id,
                username="inactive-master@example.test",
                password_hash="not-used-by-this-test",
                role=Role.SUPER_ADMIN,
                active=False,
            )
            other_master = User(
                institution_id=other_institution.id,
                username="master-b@example.test",
                password_hash="not-used-by-this-test",
                role=Role.SUPER_ADMIN,
            )
            db.add_all([master, evaluator, inactive_master, other_master])
            db.flush()

            db.add_all([
                UserProfile(
                    user_id=master.id,
                    full_name="Fictional Master A",
                    email="master-a@example.test",
                    phone="+919000000001",
                ),
                UserProfile(
                    user_id=evaluator.id,
                    full_name="Fictional Evaluator A",
                    email="evaluator-a@example.test",
                    phone="+919000000002",
                ),
                UserProfile(
                    user_id=inactive_master.id,
                    full_name="Inactive Master A",
                    email="inactive-master@example.test",
                    phone="+919000000003",
                ),
                UserProfile(
                    user_id=other_master.id,
                    full_name="Fictional Master B",
                    email="master-b@example.test",
                    phone="+919000000004",
                ),
            ])
            db.commit()

            cls.master_id = master.id
            cls.evaluator_id = evaluator.id
            cls.inactive_master_id = inactive_master.id
            cls.other_master_id = other_master.id
            cls.institution_id = institution.id
            cls.master_token = create_token(master)
            cls.evaluator_token = create_token(evaluator)
            cls.inactive_token = create_token(inactive_master)

        def override_get_db():
            with cls.SessionLocal() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        # Deliberately do not enter TestClient as a context manager: the app's
        # production startup hook must not create or touch a real local database.
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=cls.engine)
        cls.engine.dispose()

    def setUp(self):
        # OTP tests share one in-memory database; clear prior challenges so
        # each test exercises only the challenge it creates.
        with self.SessionLocal() as db:
            db.query(OTPChallenge).delete(synchronize_session=False)
            db.commit()

    def test_admin_user_list_requires_authentication(self):
        response = self.client.get("/admin/users")
        self.assertEqual(response.status_code, 401)

    def test_evaluator_cannot_access_master_admin_user_list(self):
        response = self.client.get(
            "/admin/users",
            headers={"Authorization": f"Bearer {self.evaluator_token}"},
        )
        self.assertEqual(response.status_code, 403)

    def test_evaluator_cannot_read_unassigned_subject_rubric(self):
        fixture = self._create_evaluation_assignment()
        suffix = uuid.uuid4().hex
        with self.SessionLocal() as db:
            assigned_script = db.get(EvaluationScript, fixture["script_id"])
            assigned_subject = db.get(Subject, assigned_script.subject_id)
            assigned_question = db.get(Question, fixture["question_id"])
            assigned_question.rubric = "Fictional rubric for the assigned script"

            unassigned_subject = Subject(
                examination_id=assigned_subject.examination_id,
                code=f"UNASSIGNED-{suffix[:8]}",
                name="Unassigned subject",
                max_marks=10,
            )
            db.add(unassigned_subject)
            db.flush()
            unassigned_question = Question(
                subject_id=unassigned_subject.id,
                question_number="1",
                max_marks=10,
                rubric="Fictional confidential rubric for an unassigned subject",
            )
            db.add(unassigned_question)
            db.commit()
            unassigned_subject_id = unassigned_subject.id

        evaluator_headers = {"Authorization": f"Bearer {self.evaluator_token}"}
        denied = self.client.get(
            f"/subjects/{unassigned_subject_id}/questions",
            headers=evaluator_headers,
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"], "Insufficient permissions")

        assigned = self.client.get(
            f"/evaluator/scripts/{fixture['script_id']}/questions",
            headers=evaluator_headers,
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(
            assigned.json()[0]["rubric"],
            "Fictional rubric for the assigned script",
        )

        administrator = self.client.get(
            f"/subjects/{unassigned_subject_id}/questions",
            headers={"Authorization": f"Bearer {self.master_token}"},
        )
        self.assertEqual(administrator.status_code, 200, administrator.text)
        self.assertEqual(
            administrator.json()[0]["rubric"],
            "Fictional confidential rubric for an unassigned subject",
        )


    def test_super_admin_list_is_scoped_to_its_institution(self):
        response = self.client.get(
            "/admin/users",
            headers={"Authorization": f"Bearer {self.master_token}"},
        )
        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.json()}
        self.assertEqual(
            returned_ids,
            {self.master_id, self.evaluator_id, self.inactive_master_id},
        )
        self.assertNotIn(self.other_master_id, returned_ids)

    def test_database_role_overrides_an_elevated_token_role_claim(self):
        forged_claims = {
            "sub": self.evaluator_id,
            "inst": self.institution_id,
            "role": Role.SUPER_ADMIN.value,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
        token = jwt.encode(
            forged_claims,
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        response = self.client.get(
            "/admin/users",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 403)

    def test_inactive_user_token_is_rejected(self):
        response = self.client.get(
            "/admin/users",
            headers={"Authorization": f"Bearer {self.inactive_token}"},
        )
        self.assertEqual(response.status_code, 401)


    def test_deactivated_account_cannot_complete_an_existing_otp_challenge(self):
        email = "inactive-master@example.test"
        batch_id = "inactive-otp-test"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        with self.SessionLocal() as db:
            db.add_all([
                OTPChallenge(
                    user_id=self.inactive_master_id,
                    batch_id=batch_id,
                    channel="email",
                    code_hash=otp_digest("123456"),
                    expires_at=expires_at,
                ),
                OTPChallenge(
                    user_id=self.inactive_master_id,
                    batch_id=batch_id,
                    channel="phone",
                    code_hash=otp_digest("654321"),
                    expires_at=expires_at,
                    consumed=True,
                ),
            ])
            db.commit()

        response = self.client.post(
            "/auth/verify-otp",
            json={"email": email, "channel": "email", "code": "123456"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "This account is inactive.")

    def test_two_channel_otp_is_required_and_each_code_is_single_use(self):
        email = "master-a@example.test"
        batch_id = "otp-single-use-test-0001"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        with self.SessionLocal() as db:
            db.add_all([
                OTPChallenge(
                    user_id=self.master_id,
                    batch_id=batch_id,
                    channel="email",
                    code_hash=otp_digest("112233"),
                    expires_at=expires_at,
                ),
                OTPChallenge(
                    user_id=self.master_id,
                    batch_id=batch_id,
                    channel="phone",
                    code_hash=otp_digest("445566"),
                    expires_at=expires_at,
                ),
            ])
            db.commit()

        email_response = self.client.post(
            "/auth/verify-otp",
            json={"email": email, "channel": "email", "code": "112233"},
        )
        self.assertEqual(email_response.status_code, 200)
        email_result = email_response.json()
        self.assertTrue(email_result["verified"])
        self.assertFalse(email_result["both_channels_verified"])
        self.assertEqual(email_result["next_channel"], "phone")
        self.assertNotIn("access_token", email_result)

        phone_response = self.client.post(
            "/auth/verify-otp",
            json={"email": email, "channel": "phone", "code": "445566"},
        )
        self.assertEqual(phone_response.status_code, 200)
        phone_result = phone_response.json()
        self.assertTrue(phone_result["both_channels_verified"])
        self.assertEqual(phone_result["token_type"], "bearer")
        token = phone_result["access_token"]
        self.assertTrue(token)
        authorized = self.client.get(
            "/admin/users",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(authorized.status_code, 200)

        for channel, code in (("email", "112233"), ("phone", "445566")):
            replay = self.client.post(
                "/auth/verify-otp",
                json={"email": email, "channel": channel, "code": code},
            )
            self.assertEqual(replay.status_code, 400)
            self.assertEqual(replay.json()["detail"], "Request a new OTP first.")


    def test_new_otp_batch_invalidates_older_unconsumed_challenges(self):
        email = "master-a@example.test"
        old_batch_id = "otp-replaced-batch-test-0001"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        with self.SessionLocal() as db:
            db.add_all([
                OTPChallenge(
                    user_id=self.master_id,
                    batch_id=old_batch_id,
                    channel="email",
                    code_hash=otp_digest("101010"),
                    expires_at=expires_at,
                    consumed=True,
                ),
                OTPChallenge(
                    user_id=self.master_id,
                    batch_id=old_batch_id,
                    channel="phone",
                    code_hash=otp_digest("202020"),
                    expires_at=expires_at,
                ),
            ])
            db.commit()

        delivered = {}
        with (
            patch(
                "app.main.send_email_otp",
                side_effect=lambda destination, code: delivered.__setitem__("email", code),
            ),
            patch(
                "app.main.send_sms_otp",
                side_effect=lambda destination, code: delivered.__setitem__("phone", code),
            ),
            patch("app.main.secrets.randbelow", side_effect=[303030, 404040]),
        ):
            requested = self.client.post("/auth/request-otp", json={"email": email})

        self.assertEqual(requested.status_code, 200)
        self.assertEqual(delivered, {"email": "303030", "phone": "404040"})
        with self.SessionLocal() as db:
            old_phone = db.query(OTPChallenge).filter_by(
                batch_id=old_batch_id, channel="phone"
            ).one()
            self.assertTrue(old_phone.consumed)

        email_verified = self.client.post(
            "/auth/verify-otp",
            json={"email": email, "channel": "email", "code": delivered["email"]},
        )
        self.assertEqual(email_verified.status_code, 200)
        self.assertFalse(email_verified.json()["both_channels_verified"])
        self.assertNotIn("access_token", email_verified.json())

        phone_verified = self.client.post(
            "/auth/verify-otp",
            json={"email": email, "channel": "phone", "code": delivered["phone"]},
        )
        self.assertEqual(phone_verified.status_code, 200)
        self.assertTrue(phone_verified.json()["both_channels_verified"])
        self.assertIn("access_token", phone_verified.json())

        stale_phone = self.client.post(
            "/auth/verify-otp",
            json={"email": email, "channel": "phone", "code": "202020"},
        )
        self.assertEqual(stale_phone.status_code, 400)
        self.assertEqual(stale_phone.json()["detail"], "Request a new OTP first.")

    def test_stale_otp_request_cannot_consume_a_challenge_twice(self):
        batch_id = "otp-concurrent-claim-test-0001"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        with self.SessionLocal() as db:
            challenge = OTPChallenge(
                user_id=self.master_id,
                batch_id=batch_id,
                channel="email",
                code_hash=otp_digest("123123"),
                expires_at=expires_at,
            )
            db.add(challenge)
            db.commit()
            challenge_id = challenge.id

        # Model two requests that both read the same unconsumed challenge.
        # The second keeps a detached, stale snapshot while the first commits.
        with self.SessionLocal() as db:
            stale_challenge = db.get(OTPChallenge, challenge_id)
            db.expunge(stale_challenge)

        with self.SessionLocal() as db:
            current_challenge = db.get(OTPChallenge, challenge_id)
            self.assertTrue(claim_otp_attempt(db, current_challenge, consume=True))
            db.commit()

        with self.SessionLocal() as db:
            self.assertFalse(claim_otp_attempt(db, stale_challenge, consume=True))
            db.rollback()
            persisted = db.get(OTPChallenge, challenge_id)
            self.assertTrue(persisted.consumed)
            self.assertEqual(persisted.attempts, 1)

    def test_stale_otp_request_cannot_exceed_attempt_limit(self):
        batch_id = "otp-concurrent-limit-test-0001"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        with self.SessionLocal() as db:
            challenge = OTPChallenge(
                user_id=self.master_id,
                batch_id=batch_id,
                channel="email",
                code_hash=otp_digest("123123"),
                expires_at=expires_at,
                attempts=4,
            )
            db.add(challenge)
            db.commit()
            challenge_id = challenge.id

        # Model two requests that both read the fourth attempt before either
        # writes. The conditional UPDATE must allow only one to claim attempt 5.
        with self.SessionLocal() as db:
            stale_challenge = db.get(OTPChallenge, challenge_id)
            db.expunge(stale_challenge)

        with self.SessionLocal() as db:
            current_challenge = db.get(OTPChallenge, challenge_id)
            self.assertTrue(claim_otp_attempt(db, current_challenge, consume=False))
            db.commit()

        with self.SessionLocal() as db:
            self.assertFalse(claim_otp_attempt(db, stale_challenge, consume=False))
            db.rollback()
            persisted = db.get(OTPChallenge, challenge_id)
            self.assertEqual(persisted.attempts, 5)
            self.assertFalse(persisted.consumed)

    def test_expired_otp_is_rejected_even_when_the_code_matches(self):
        email = "master-a@example.test"
        batch_id = "otp-expired-test-0001"
        expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        with self.SessionLocal() as db:
            db.add(OTPChallenge(
                user_id=self.master_id,
                batch_id=batch_id,
                channel="email",
                code_hash=otp_digest("778899"),
                expires_at=expires_at,
            ))
            db.commit()

        response = self.client.post(
            "/auth/verify-otp",
            json={"email": email, "channel": "email", "code": "778899"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "OTP expired. Request a new one.")

        with self.SessionLocal() as db:
            challenge = db.query(OTPChallenge).filter_by(batch_id=batch_id).one()
            self.assertFalse(challenge.consumed)
            self.assertEqual(challenge.attempts, 0)

    def test_stale_otp_request_cannot_claim_a_challenge_after_expiry(self):
        batch_id = "otp-expiry-claim-test-0001"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        with self.SessionLocal() as db:
            challenge = OTPChallenge(
                user_id=self.master_id,
                batch_id=batch_id,
                channel="email",
                code_hash=otp_digest("456456"),
                expires_at=expires_at,
            )
            db.add(challenge)
            db.commit()
            challenge_id = challenge.id

        # The request read a live challenge, but its atomic claim happens
        # after the expiry boundary. The database predicate must reject it.
        with self.SessionLocal() as db:
            stale_challenge = db.get(OTPChallenge, challenge_id)
            db.expunge(stale_challenge)

        with self.SessionLocal() as db:
            claim_time = expires_at + timedelta(seconds=1)
            self.assertFalse(
                claim_otp_attempt(
                    db, stale_challenge, consume=True, now=claim_time
                )
            )
            db.rollback()
            persisted = db.get(OTPChallenge, challenge_id)
            self.assertFalse(persisted.consumed)
            self.assertEqual(persisted.attempts, 0)

    def test_otp_is_locked_after_five_incorrect_attempts(self):
        email = "master-a@example.test"
        batch_id = "otp-attempt-limit-test-0001"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        with self.SessionLocal() as db:
            db.add(OTPChallenge(
                user_id=self.master_id,
                batch_id=batch_id,
                channel="email",
                code_hash=otp_digest("999999"),
                expires_at=expires_at,
            ))
            db.commit()

        for attempt in range(5):
            response = self.client.post(
                "/auth/verify-otp",
                json={"email": email, "channel": "email", "code": "000000"},
            )
            self.assertEqual(response.status_code, 400, f"Incorrect attempt {attempt + 1} should be rejected.")

        locked_response = self.client.post(
            "/auth/verify-otp",
            json={"email": email, "channel": "email", "code": "999999"},
        )
        self.assertEqual(locked_response.status_code, 429)
        self.assertEqual(
            locked_response.json()["detail"],
            "Too many incorrect attempts. Request a new OTP.",
        )

        with self.SessionLocal() as db:
            challenge = db.query(OTPChallenge).filter_by(batch_id=batch_id).one()
            self.assertEqual(challenge.attempts, 5)
            self.assertFalse(challenge.consumed)


    def test_otp_request_rate_limit_uses_postgresql_account_row_lock(self):
        class RecordingSession:
            statement = None

            def scalar(self, statement):
                self.statement = statement
                return None

        recorder = RecordingSession()
        get_otp_request_user_for_update(recorder, self.master_id)
        sql = str(recorder.statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FOR UPDATE", sql.upper())
        self.assertIn("from users", sql.lower())

        with (
            patch(
                "app.main.get_otp_request_user_for_update",
                wraps=get_otp_request_user_for_update,
            ) as account_lock,
            patch("app.main.send_email_otp"),
            patch("app.main.send_sms_otp"),
        ):
            response = self.client.post(
                "/auth/request-otp",
                json={"email": "master-a@example.test"},
            )

        self.assertEqual(response.status_code, 200)
        account_lock.assert_called_once()

    def test_otp_requests_are_rate_limited_after_two_batches_per_minute(self):
        email = "master-a@example.test"
        with (
            patch("app.main.send_email_otp") as email_delivery,
            patch("app.main.send_sms_otp") as sms_delivery,
        ):
            first = self.client.post("/auth/request-otp", json={"email": email})
            second = self.client.post("/auth/request-otp", json={"email": email})
            third = self.client.post("/auth/request-otp", json={"email": email})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(third.status_code, 429)
        self.assertEqual(
            third.json()["detail"],
            "Please wait 60 seconds before requesting another OTP.",
        )
        self.assertEqual(email_delivery.call_count, 2)
        self.assertEqual(sms_delivery.call_count, 2)

        with self.SessionLocal() as db:
            challenges = db.query(OTPChallenge).filter_by(
                user_id=self.master_id
            ).all()
            self.assertEqual(len(challenges), 4)
            self.assertEqual(sum(challenge.consumed for challenge in challenges), 2)
            active = [challenge for challenge in challenges if not challenge.consumed]
            self.assertEqual({challenge.channel for challenge in active}, {"email", "phone"})
            self.assertEqual(len({challenge.batch_id for challenge in active}), 1)


    def test_email_delivery_failure_invalidates_new_otp_batch(self):
        with (
            patch(
                "app.main.send_email_otp",
                side_effect=HTTPException(502, "Email delivery failed."),
            ) as email_delivery,
            patch("app.main.send_sms_otp") as sms_delivery,
        ):
            response = self.client.post(
                "/auth/request-otp",
                json={"email": "master-a@example.test"},
            )

        self.assertEqual(response.status_code, 502)
        email_delivery.assert_called_once()
        sms_delivery.assert_not_called()
        with self.SessionLocal() as db:
            challenges = db.query(OTPChallenge).filter_by(
                user_id=self.master_id
            ).all()
            self.assertEqual(len(challenges), 2)
            self.assertTrue(all(challenge.consumed for challenge in challenges))

    def test_sms_delivery_failure_invalidates_partially_delivered_otp_batch(self):
        delivered_email_codes = []
        with (
            patch(
                "app.main.send_email_otp",
                side_effect=lambda destination, code: delivered_email_codes.append(code),
            ) as email_delivery,
            patch(
                "app.main.send_sms_otp",
                side_effect=HTTPException(502, "SMS delivery failed."),
            ) as sms_delivery,
        ):
            response = self.client.post(
                "/auth/request-otp",
                json={"email": "master-a@example.test"},
            )

        self.assertEqual(response.status_code, 502)
        email_delivery.assert_called_once()
        sms_delivery.assert_called_once()
        self.assertEqual(len(delivered_email_codes), 1)
        with self.SessionLocal() as db:
            challenges = db.query(OTPChallenge).filter_by(
                user_id=self.master_id
            ).all()
            self.assertEqual(len(challenges), 2)
            self.assertTrue(all(challenge.consumed for challenge in challenges))

        replay = self.client.post(
            "/auth/verify-otp",
            json={
                "email": "master-a@example.test",
                "channel": "email",
                "code": delivered_email_codes[0],
            },
        )
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["detail"], "Request a new OTP first.")


    def _create_evaluation_assignment(self):
        suffix = uuid.uuid4().hex
        with self.SessionLocal() as db:
            examination = Examination(
                institution_id=self.institution_id,
                name=f"Authorization test {suffix}",
                academic_year="2026-27",
            )
            db.add(examination)
            db.flush()
            subject = Subject(
                examination_id=examination.id,
                code=f"AUTH-{suffix[:8]}",
                name="Authorization test subject",
                max_marks=10,
            )
            db.add(subject)
            db.flush()
            question = Question(
                subject_id=subject.id,
                question_number="1",
                max_marks=10,
            )
            db.add(question)
            db.flush()
            script = EvaluationScript(
                institution_id=self.institution_id,
                subject_id=subject.id,
                anonymous_code=f"SDVS-{suffix[:12]}",
                object_key="unused-test-object.pdf",
                pdf_sha256="0" * 64,
                page_count=1,
            )
            db.add(script)
            db.flush()
            allocation = Allocation(
                script_id=script.id,
                evaluator_id=self.evaluator_id,
                active=True,
            )
            db.add(allocation)
            db.flush()
            evaluation = Evaluation(
                script_id=script.id,
                evaluator_id=self.evaluator_id,
                status=Status.DRAFT,
            )
            db.add(evaluation)
            db.flush()
            question_mark = QuestionMark(
                evaluation_id=evaluation.id,
                question_id=question.id,
                marks=5,
            )
            db.add(question_mark)
            db.commit()
            return {
                "allocation_id": allocation.id,
                "evaluation_id": evaluation.id,
                "script_id": script.id,
                "question_id": question.id,
                "question_mark_id": question_mark.id,
            }

    def test_revoked_allocation_cannot_submit_a_saved_draft(self):
        fixture = self._create_evaluation_assignment()
        with self.SessionLocal() as db:
            allocation = db.get(Allocation, fixture["allocation_id"])
            allocation.active = False
            db.commit()

        response = self.client.post(
            f"/evaluations/{fixture['evaluation_id']}/submit",
            headers={"Authorization": f"Bearer {self.evaluator_token}"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Script not found")

        with self.SessionLocal() as db:
            evaluation = db.get(Evaluation, fixture["evaluation_id"])
            script = db.get(EvaluationScript, fixture["script_id"])
            self.assertEqual(evaluation.status, Status.DRAFT)
            self.assertIsNone(evaluation.content_hash)
            self.assertEqual(script.status, "UPLOADED")

    def test_active_allocation_can_submit_a_complete_draft(self):
        fixture = self._create_evaluation_assignment()
        response = self.client.post(
            f"/evaluations/{fixture['evaluation_id']}/submit",
            headers={"Authorization": f"Bearer {self.evaluator_token}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "SUBMITTED")
        self.assertEqual(payload["total_marks"], "5.00")
        self.assertRegex(payload["content_sha256"], r"^[0-9a-f]{64}$")

        with self.SessionLocal() as db:
            evaluation = db.get(Evaluation, fixture["evaluation_id"])
            script = db.get(EvaluationScript, fixture["script_id"])
            self.assertEqual(evaluation.status, Status.SUBMITTED)
            self.assertEqual(evaluation.content_hash, payload["content_sha256"])
            self.assertEqual(script.status, "SUBMITTED")


    def test_revoked_allocation_cannot_download_assigned_pdf(self):
        fixture = self._create_evaluation_assignment()
        with self.SessionLocal() as db:
            allocation = db.get(Allocation, fixture["allocation_id"])
            allocation.active = False
            db.commit()

        with patch("app.main.get_object") as get_object:
            response = self.client.get(
                f"/evaluator/scripts/{fixture['script_id']}/pdf",
                headers={"Authorization": f"Bearer {self.evaluator_token}"},
            )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Script not found")
        get_object.assert_not_called()

    def test_assigned_pdf_is_served_when_stored_sha256_matches(self):
        import hashlib

        fixture = self._create_evaluation_assignment()
        pdf_bytes = b"%PDF-1.4\\n% SDVS integrity fixture\\n%%EOF\\n"
        expected_digest = hashlib.sha256(pdf_bytes).hexdigest()
        with self.SessionLocal() as db:
            script = db.get(EvaluationScript, fixture["script_id"])
            script.pdf_sha256 = expected_digest
            db.commit()

        with patch("app.main.get_object", return_value=pdf_bytes) as get_object:
            response = self.client.get(
                f"/evaluator/scripts/{fixture['script_id']}/pdf",
                headers={"Authorization": f"Bearer {self.evaluator_token}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, pdf_bytes)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        get_object.assert_called_once_with("unused-test-object.pdf")

    def test_assigned_pdf_is_rejected_when_stored_sha256_does_not_match(self):
        import hashlib

        fixture = self._create_evaluation_assignment()
        original_pdf = b"%PDF-1.4\\n% original stored object\\n%%EOF\\n"
        tampered_pdf = b"%PDF-1.4\\n% substituted object\\n%%EOF\\n"
        expected_digest = hashlib.sha256(original_pdf).hexdigest()
        with self.SessionLocal() as db:
            script = db.get(EvaluationScript, fixture["script_id"])
            script.pdf_sha256 = expected_digest
            db.commit()

        with patch("app.main.get_object", return_value=tampered_pdf) as get_object:
            response = self.client.get(
                f"/evaluator/scripts/{fixture['script_id']}/pdf",
                headers={"Authorization": f"Bearer {self.evaluator_token}"},
            )

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "Stored PDF integrity check failed.")
        get_object.assert_called_once_with("unused-test-object.pdf")

    def test_submitted_evaluation_rejects_draft_changes_and_preserves_hash(self):
        fixture = self._create_evaluation_assignment()
        headers = {"Authorization": f"Bearer {self.evaluator_token}"}

        submitted = self.client.post(
            f"/evaluations/{fixture['evaluation_id']}/submit",
            headers=headers,
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        submitted_hash = submitted.json()["content_sha256"]

        with self.SessionLocal() as db:
            evaluation = db.get(Evaluation, fixture["evaluation_id"])
            original_submitted_at = evaluation.submitted_at
            original_total = evaluation.total_marks
            original_mark = db.query(QuestionMark).filter_by(
                evaluation_id=fixture["evaluation_id"]
            ).one()
            original_mark_id = original_mark.id
            original_mark_value = original_mark.marks

        attempted_change = self.client.post(
            "/evaluations",
            json={
                "script_id": fixture["script_id"],
                "marks": [
                    {"question_id": fixture["question_id"], "marks": 1}
                ],
            },
            headers=headers,
        )
        self.assertEqual(attempted_change.status_code, 409, attempted_change.text)
        self.assertEqual(
            attempted_change.json()["detail"],
            "Submitted evaluation is locked",
        )

        with self.SessionLocal() as db:
            evaluation = db.get(Evaluation, fixture["evaluation_id"])
            persisted_mark = db.query(QuestionMark).filter_by(
                evaluation_id=fixture["evaluation_id"]
            ).one()
            self.assertEqual(evaluation.status, Status.SUBMITTED)
            self.assertEqual(evaluation.content_hash, submitted_hash)
            self.assertEqual(evaluation.submitted_at, original_submitted_at)
            self.assertEqual(evaluation.total_marks, original_total)
            self.assertEqual(persisted_mark.id, original_mark_id)
            self.assertEqual(persisted_mark.marks, original_mark_value)

    def test_draft_save_and_submission_lock_allocation_before_evaluation(self):
        fixture = self._create_evaluation_assignment()
        events = []

        def track_assignment(script_id, user, db, **kwargs):
            events.append(("allocation", kwargs.get("lock_allocation")))
            return assigned_script(script_id, user, db, **kwargs)

        def track_evaluation(db, evaluator_id, **kwargs):
            events.append(("evaluation", kwargs))
            return get_evaluation_for_update(db, evaluator_id, **kwargs)

        headers = {"Authorization": f"Bearer {self.evaluator_token}"}
        with (
            patch("app.main.assigned_script", side_effect=track_assignment),
            patch("app.main.get_evaluation_for_update", side_effect=track_evaluation),
        ):
            saved = self.client.post(
                "/evaluations",
                json={
                    "script_id": fixture["script_id"],
                    "marks": [{"question_id": fixture["question_id"], "marks": 6}],
                },
                headers=headers,
            )
        self.assertEqual(saved.status_code, 200, saved.text)
        with self.SessionLocal() as db:
            updated_marks = db.query(QuestionMark).filter_by(
                evaluation_id=fixture["evaluation_id"]
            ).all()
            self.assertEqual(len(updated_marks), 1)
            self.assertEqual(updated_marks[0].id, fixture["question_mark_id"])
            self.assertEqual(float(updated_marks[0].marks), 6.0)
        self.assertEqual(
            events,
            [
                ("allocation", True),
                ("evaluation", {"script_id": fixture["script_id"]}),
            ],
        )

        events.clear()
        with (
            patch("app.main.assigned_script", side_effect=track_assignment),
            patch("app.main.get_evaluation_for_update", side_effect=track_evaluation),
        ):
            submitted = self.client.post(
                f"/evaluations/{fixture['evaluation_id']}/submit",
                headers=headers,
            )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(
            events,
            [
                ("allocation", True),
                ("evaluation", {"evaluation_id": fixture["evaluation_id"]}),
            ],
        )

    def _create_upload_subject(self):
        suffix = uuid.uuid4().hex
        with self.SessionLocal() as db:
            examination = Examination(
                institution_id=self.institution_id,
                name=f"Upload test {suffix}",
                academic_year="2026-27",
            )
            db.add(examination)
            db.flush()
            subject = Subject(
                examination_id=examination.id,
                code=f"UPLOAD-{suffix[:8]}",
                name="Upload test subject",
                max_marks=10,
            )
            db.add(subject)
            db.commit()
            return subject.id

    def test_pdf_upload_rejects_payload_over_configured_limit_before_storage(self):
        subject_id = self._create_upload_subject()
        oversized_pdf = b"%PDF-" + b"x" * (1024 * 1024 - 4)
        self.assertEqual(len(oversized_pdf), 1024 * 1024 + 1)

        with patch("app.main.settings.max_upload_mb", 1), patch("app.main.put_object") as put_object:
            response = self.client.post(
                "/scripts/upload",
                data={
                    "subject_id": subject_id,
                    "candidate_name": "Fictional Oversized Candidate",
                    "register_number": "REG-OVERSIZED-001",
                },
                files={"file": ("oversized.pdf", oversized_pdf, "application/pdf")},
                headers={"Authorization": f"Bearer {self.master_token}"},
            )

        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(response.json()["detail"], "PDF exceeds upload limit")
        put_object.assert_not_called()

    def test_pdf_upload_keeps_candidate_identity_out_of_anonymous_response_and_audit(self):
        subject_id = self._create_upload_subject()
        candidate_name = "Fictional Candidate Upload Test"
        register_number = "REG-UPLOAD-001"
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        source = io.BytesIO()
        writer.write(source)
        source_pdf = source.getvalue()

        with patch("app.main.put_object") as put_object:
            response = self.client.post(
                "/scripts/upload",
                data={
                    "subject_id": subject_id,
                    "candidate_name": candidate_name,
                    "register_number": register_number,
                },
                files={"file": ("sample.pdf", source_pdf, "application/pdf")},
                headers={"Authorization": f"Bearer {self.master_token}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(set(payload), {"script_id", "anonymous_code", "page_count", "sha256"})
        self.assertNotIn(candidate_name, json.dumps(payload))
        self.assertNotIn(register_number, json.dumps(payload))
        self.assertEqual(payload["page_count"], 1)

        put_object.assert_called_once()
        object_key, stored_pdf = put_object.call_args.args
        self.assertEqual(object_key, f"{payload['anonymous_code']}.pdf")
        self.assertTrue(stored_pdf.startswith(b"%PDF-"))
        expected_digest = hashlib.sha256(stored_pdf).hexdigest()
        self.assertEqual(payload["sha256"], expected_digest)

        with self.SessionLocal() as db:
            script = db.get(EvaluationScript, payload["script_id"])
            self.assertEqual(script.anonymous_code, payload["anonymous_code"])
            self.assertEqual(script.pdf_sha256, expected_digest)
            mapping = db.query(IdentityMapping).filter_by(script_id=script.id).one()
            candidate = db.get(Candidate, mapping.candidate_id)
            self.assertEqual(candidate.full_name, candidate_name)
            self.assertEqual(candidate.register_number, register_number)
            audit_event = db.query(AuditEvent).filter_by(
                action="SCRIPT_UPLOADED",
                entity_id=script.id,
            ).one()
            audit_detail = json.loads(audit_event.detail)
            self.assertEqual(
                audit_detail,
                {"anonymous_code": payload["anonymous_code"], "sha256": expected_digest},
            )
            self.assertNotIn(candidate_name, audit_event.detail)
            self.assertNotIn(register_number, audit_event.detail)

    def test_pdf_upload_rejects_malformed_pdf_before_storage(self):
        subject_id = self._create_upload_subject()
        candidate_name = "Fictional Malformed Candidate"
        register_number = "REG-MALFORMED-001"
        malformed_pdf = b"%PDF-1.7\\nthis is not a valid PDF\\n%%EOF"

        with patch("app.main.put_object") as put_object:
            response = self.client.post(
                "/scripts/upload",
                data={
                    "subject_id": subject_id,
                    "candidate_name": candidate_name,
                    "register_number": register_number,
                },
                files={"file": ("malformed.pdf", malformed_pdf, "application/pdf")},
                headers={"Authorization": f"Bearer {self.master_token}"},
            )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"], "PDF is invalid or unreadable")
        put_object.assert_not_called()

        with self.SessionLocal() as db:
            self.assertIsNone(
                db.query(Candidate).filter_by(register_number=register_number).first()
            )

    def test_pdf_upload_rejects_encrypted_pdf_before_storage(self):
        subject_id = self._create_upload_subject()
        candidate_name = "Fictional Encrypted Candidate"
        register_number = "REG-ENCRYPTED-001"
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.encrypt(user_password="fixture-password")
        source = io.BytesIO()
        writer.write(source)
        encrypted_pdf = source.getvalue()

        with patch("app.main.put_object") as put_object:
            response = self.client.post(
                "/scripts/upload",
                data={
                    "subject_id": subject_id,
                    "candidate_name": candidate_name,
                    "register_number": register_number,
                },
                files={"file": ("encrypted.pdf", encrypted_pdf, "application/pdf")},
                headers={"Authorization": f"Bearer {self.master_token}"},
            )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"], "Encrypted PDFs are not accepted")
        put_object.assert_not_called()

        with self.SessionLocal() as db:
            self.assertIsNone(
                db.query(Candidate).filter_by(register_number=register_number).first()
            )
