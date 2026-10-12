import os
from urllib.parse import urlparse

import psycopg
import pytest

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(scope="session")
def db_url():
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    name = urlparse(TEST_DB_URL).path.lstrip("/")
    assert "test" in name, "refusing to wipe a database whose name doesn't contain 'test'"
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    from secqa.db import migrate

    migrate(TEST_DB_URL)
    return TEST_DB_URL


@pytest.fixture
def conn(db_url):
    from secqa.db import connect

    with connect(db_url) as c:
        yield c
