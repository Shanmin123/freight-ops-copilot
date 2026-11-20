from pathlib import Path

import pytest

from forwarder.config import Settings
from forwarder.db import Database
from forwarder.world.data import load_world


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    database.init_schema()
    return database


@pytest.fixture
def world():
    return load_world(Path(__file__).parents[1] / "data" / "world")


@pytest.fixture
def settings(tmp_path):
    return Settings("mock", None, None, db_path=tmp_path / "t.db")
