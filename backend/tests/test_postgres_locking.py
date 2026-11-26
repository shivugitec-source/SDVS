import os
import unittest
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import get_otp_request_user_for_update
from app.models import Institution, Role, User


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


if __name__ == "__main__":
    unittest.main()
