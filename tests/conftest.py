"""Shared fixtures. Uses fakes for DB/Redis so the API layer is testable
without infrastructure (unit-level); integration tests come with docker."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())
