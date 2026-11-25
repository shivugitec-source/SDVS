import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app, otp_digest
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


if __name__ == "__main__":
    unittest.main()
