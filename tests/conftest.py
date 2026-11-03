import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _private_cache(tmp_path_factory, monkeypatch):
    """Tests never touch the real settings cache in your home folder."""
    monkeypatch.setenv("HARBIUM_CACHE", str(tmp_path_factory.mktemp("cache")))


@pytest.fixture(autouse=True)
def _private_config(tmp_path_factory, monkeypatch):
    """Tests never touch the real app preferences or recent-projects list."""
    monkeypatch.setenv("HARBIUM_CONFIG", str(tmp_path_factory.mktemp("config")))
