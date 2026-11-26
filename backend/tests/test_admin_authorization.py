import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app, claim_otp_attempt, get_otp_request_user_for_update, otp_digest
from app.models import Institution, OTPChallenge, Role, User, UserProfile
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
        self.assertIn("FROM users", sql.lower())

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


if __name__ == "__main__":
    unittest.main()
