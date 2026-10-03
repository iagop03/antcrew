"""Create a fresh local SQLite DB from current models and stamp alembic to head."""
import asyncio
import subprocess
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.models.run  # noqa: F401
import app.models.admin  # noqa: F401
import app.models.feedback  # noqa: F401


async def main() -> None:
    db_path = Path("platform.db")
    if db_path.exists():
        db_path.unlink()
        print(f"Deleted existing {db_path}")

    engine = create_async_engine("sqlite+aiosqlite:///platform.db")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await engine.dispose()
    print("Schema created from models.")

    result = subprocess.run(
        ["alembic", "stamp", "head"],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        sys.exit(1)
    print("Alembic stamped at head. DB is ready.")


asyncio.run(main())
