import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _private_cache(tmp_path_factory, monkeypatch):
    """Tests never touch the real settings cache in your home folder."""
    monkeypatch.setenv("HARBIUM_CACHE", str(tmp_path_factory.mktemp("cache")))
