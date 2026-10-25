from datetime import datetime, timedelta, timezone

import pytest

from app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def future_time():
    return (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).isoformat()


@pytest.fixture
def booking(future_time):
    return {
        "patient_name": "Alex Doe",
        "provider": "Dr. Smith",
        "scheduled_at": future_time,
        "reason": "Annual check-up",
    }


@pytest.fixture
def booked(client, booking):
    response = client.post("/appointments", json=booking)
    assert response.status_code == 201
    return response.get_json()
