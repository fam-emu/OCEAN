import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "script"))
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ocean-mpl")


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
