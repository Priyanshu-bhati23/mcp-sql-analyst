import pytest

from sql_analyst.database import Database
from sql_analyst.seed import build


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    return build(tmp_path_factory.mktemp("data") / "demo.db")


@pytest.fixture
def db(db_path) -> Database:
    return Database(db_path)
