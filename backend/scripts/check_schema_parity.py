"""Compare an existing database to the frozen 0001 baseline without changing it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import models  # noqa: F401 - register mapped tables
from app.db import Base, engine


# This index is introduced by 0004, after the 0001 schema baseline. It must not
# make a legitimate pre-0004 database fail the read-only baseline adoption check.
_INITIAL_BASELINE_EXCLUDED_INDEXES = {
    "uq_allocations_active_script_evaluator",
}


def _include_initial_baseline_object(obj, name, type_, reflected, compare_to):
    if type_ == "index" and name in _INITIAL_BASELINE_EXCLUDED_INDEXES:
        return False
    return True


def main() -> int:
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "include_object": _include_initial_baseline_object},
        )
        differences = compare_metadata(context, Base.metadata)

    if differences:
        print("Database schema differs from the SDVS initial Alembic baseline; do not stamp it.")
        for difference in differences:
            print(repr(difference))
        return 1
    print("Database schema matches the SDVS initial Alembic baseline (post-baseline indexes excluded).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
