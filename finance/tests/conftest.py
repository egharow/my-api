import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ftracker.config import Home  # noqa: E402
from ftracker.db import connect  # noqa: E402


@pytest.fixture
def home(tmp_path):
    h = Home(tmp_path / "Finance")
    h.ensure()
    return h


@pytest.fixture
def conn(home):
    c = connect(home.db_path)
    yield c
    c.close()
