"""Enforce one active allocation per script/evaluator pair.

Existing duplicate active pairs are not rewritten or deleted automatically.
The migration fails closed so an operator can resolve them through the
authorized allocation-revocation workflow before retrying.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_active_alloc_unique"
down_revision = "0003_runtime_identity_guards"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_allocations_active_script_evaluator"


def upgrade() -> None:
    bind = op.get_bind()
    duplicate_pairs = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM (
                SELECT script_id, evaluator_id
                FROM allocations
                WHERE active IS TRUE
                GROUP BY script_id, evaluator_id
                HAVING COUNT(*) > 1
            ) AS duplicate_pairs
            """
        )
    ).scalar_one()

    if duplicate_pairs:
        raise RuntimeError(
            "Cannot create the active-allocation unique index: found "
            f"{duplicate_pairs} script/evaluator pairs with multiple active "
            "allocations. No rows were changed. Revoke each affected assignment "
            "through the authorized allocation-revocation workflow, then retry."
        )

    op.create_index(
        _INDEX_NAME,
        "allocations",
        ["script_id", "evaluator_id"],
        unique=True,
        postgresql_where=sa.text("active IS TRUE"),
        sqlite_where=sa.text("active = 1"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="allocations")
