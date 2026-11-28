"""Regression tests for the active-allocation uniqueness migration."""
import importlib.util
import unittest
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Boolean,
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    insert,
    select,
    update,
)
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0004_active_allocation_uniqueness.py"
)
SPEC = importlib.util.spec_from_file_location(
    "sdvs_migration_0004_active_allocation_uniqueness", MIGRATION_PATH
)
MIGRATION = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MIGRATION)


class ActiveAllocationUniquenessMigrationTests(unittest.TestCase):
    def test_revision_id_fits_alembic_version_column(self):
        self.assertLessEqual(len(MIGRATION.revision), 32)

    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        self.metadata = MetaData()
        self.allocations = Table(
            "allocations",
            self.metadata,
            Column("id", String, primary_key=True),
            Column("script_id", String, nullable=False),
            Column("evaluator_id", String, nullable=False),
            Column("active", Boolean, nullable=False),
        )
        self.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def apply_upgrade(connection):
        with Operations.context(MigrationContext.configure(connection)):
            MIGRATION.upgrade()

    def test_upgrade_fails_closed_without_rewriting_duplicate_active_rows(self):
        with self.engine.begin() as connection:
            connection.execute(
                insert(self.allocations),
                [
                    {"id": "a1", "script_id": "script-1", "evaluator_id": "eval-1", "active": True},
                    {"id": "a2", "script_id": "script-1", "evaluator_id": "eval-1", "active": True},
                ],
            )

            with self.assertRaisesRegex(RuntimeError, "multiple active allocations"):
                self.apply_upgrade(connection)

            rows = connection.execute(
                select(self.allocations.c.id, self.allocations.c.active)
                .order_by(self.allocations.c.id)
            ).all()
            self.assertEqual(rows, [("a1", True), ("a2", True)])
            self.assertNotIn(
                "uq_allocations_active_script_evaluator",
                {index["name"] for index in inspect(connection).get_indexes("allocations")},
            )

    def test_partial_unique_index_allows_history_but_rejects_two_active_rows(self):
        with self.engine.begin() as connection:
            connection.execute(
                insert(self.allocations),
                [
                    {"id": "active-1", "script_id": "script-1", "evaluator_id": "eval-1", "active": True},
                    {"id": "inactive-1", "script_id": "script-1", "evaluator_id": "eval-1", "active": False},
                    {"id": "inactive-2", "script_id": "script-1", "evaluator_id": "eval-1", "active": False},
                ],
            )
            self.apply_upgrade(connection)

            with self.assertRaises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        insert(self.allocations),
                        {"id": "active-2", "script_id": "script-1", "evaluator_id": "eval-1", "active": True},
                    )

            connection.execute(
                update(self.allocations)
                .where(self.allocations.c.id == "active-1")
                .values(active=False)
            )
            connection.execute(
                update(self.allocations)
                .where(self.allocations.c.id == "inactive-1")
                .values(active=True)
            )
            active_count = connection.execute(
                select(func.count())
                .select_from(self.allocations)
                .where(
                    self.allocations.c.script_id == "script-1",
                    self.allocations.c.evaluator_id == "eval-1",
                    self.allocations.c.active.is_(True),
                )
            ).scalar_one()
            self.assertEqual(active_count, 1)


if __name__ == "__main__":
    unittest.main()
