import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import store


@pytest.fixture(autouse=True)
def reset_db_pool_between_tests():
    yield
    try:
        asyncio.run(store.close())
    except Exception:
        # Test-only safety: IsolatedAsyncioTestCase may close the loop that
        # owns the asyncpg pool before pytest fixture teardown runs.
        # Ensure the next test does not reuse a stale event-loop-bound pool.
        store._pool = None
