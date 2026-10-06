"""Shared fixtures for the HTTP API."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def build_client(tmp_path, **overrides) -> TestClient:
    database_path = (tmp_path / "test.db").resolve().as_posix()
    values = {
        "database_url": f"sqlite:///{database_path}",
        "upload_dir": tmp_path / "uploads",
        "max_upload_bytes": 2_000_000,
        "max_unzipped_bytes": 10_000_000,
        "max_features": 1000,
    }
    values.update(overrides)
    application = create_app(Settings(**values))
    return TestClient(application)


@pytest.fixture
def client(tmp_path):
    with build_client(tmp_path) as test_client:
        yield test_client
