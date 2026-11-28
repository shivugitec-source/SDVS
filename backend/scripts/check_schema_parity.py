"""Compare an existing database to current ORM metadata without changing it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import models  # noqa: F401 - register mapped tables
from app.db import Base, engine


def main() -> int:
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True},
        )
        differences = compare_metadata(context, Base.metadata)

    if differences:
        print("Database schema differs from the SDVS initial Alembic baseline; do not stamp it.")
        for difference in differences:
            print(repr(difference))
        return 1
    print("Database schema matches the SDVS initial Alembic baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
