from datetime import datetime, timedelta, timezone

import pytest

from app import create_app


def test_create_app_returns_flask_instance():
    app = create_app()
    assert app is not None
    assert app.name == "app"


# --- /health ---------------------------------------------------------------


def test_health_status_code_and_body(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_health_rejects_post(client):
    response = client.post("/health")
    assert response.status_code == 405
    assert "error" in response.get_json()


def test_unknown_route_returns_json_404(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.content_type == "application/json"


# --- Booking ---------------------------------------------------------------


def test_book_appointment(client, booking):
    response = client.post("/appointments", json=booking)
    assert response.status_code == 201
    body = response.get_json()
    assert body["patient_name"] == "Alex Doe"
    assert body["provider"] == "Dr. Smith"
    assert body["reason"] == "Annual check-up"
    assert body["status"] == "booked"
    assert body["cancelled_at"] is None
    assert response.headers["Location"] == f"/appointments/{body['id']}"


def test_book_normalises_time_to_utc(client, booking):
    local = datetime.now(timezone(timedelta(hours=5))) + timedelta(days=2)
    booking["scheduled_at"] = local.replace(microsecond=0).isoformat()
    body = client.post("/appointments", json=booking).get_json()
    assert body["scheduled_at"].endswith("+00:00")
    assert datetime.fromisoformat(body["scheduled_at"]) == local.replace(microsecond=0)


def test_book_accepts_z_suffix(client, booking):
    future = datetime.now(timezone.utc) + timedelta(days=3)
    booking["scheduled_at"] = future.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert client.post("/appointments", json=booking).status_code == 201


def test_book_without_reason(client, booking):
    del booking["reason"]
    response = client.post("/appointments", json=booking)
    assert response.status_code == 201
    assert response.get_json()["reason"] is None


def test_book_trims_whitespace(client, booking):
    booking["patient_name"] = "  Alex Doe  "
    booking["reason"] = "   "
    body = client.post("/appointments", json=booking).get_json()
    assert body["patient_name"] == "Alex Doe"
    assert body["reason"] is None


@pytest.mark.parametrize("data", ["not json", "[1, 2]", ""])
def test_book_rejects_non_object_body(client, data):
    response = client.post("/appointments", data=data, content_type="application/json")
    assert response.status_code == 400
    assert response.get_json()["error"] == "request body must be a JSON object"


@pytest.mark.parametrize("field", ["patient_name", "provider", "scheduled_at"])
def test_book_rejects_missing_required_field(client, booking, field):
    del booking[field]
    response = client.post("/appointments", json=booking)
    assert response.status_code == 400
    assert field in response.get_json()["details"]


@pytest.mark.parametrize("value", ["", "   ", 42, None])
def test_book_rejects_invalid_patient_name(client, booking, value):
    booking["patient_name"] = value
    response = client.post("/appointments", json=booking)
    assert response.status_code == 400
    assert "patient_name" in response.get_json()["details"]


def test_book_rejects_overlong_field(client, booking):
    booking["provider"] = "x" * 201
    response = client.post("/appointments", json=booking)
    assert response.status_code == 400
    assert "provider" in response.get_json()["details"]


@pytest.mark.parametrize("reason", [123, "x" * 201])
def test_book_rejects_invalid_reason(client, booking, reason):
    booking["reason"] = reason
    response = client.post("/appointments", json=booking)
    assert response.status_code == 400
    assert "reason" in response.get_json()["details"]


@pytest.mark.parametrize("value", ["tomorrow", "2030-13-01T10:00:00+00:00", "2030-01-01T10:00:00"])
def test_book_rejects_bad_datetime(client, booking, value):
    booking["scheduled_at"] = value
    response = client.post("/appointments", json=booking)
    assert response.status_code == 400
    assert "timezone" in response.get_json()["details"]["scheduled_at"]


def test_book_rejects_past_datetime(client, booking):
    booking["scheduled_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    response = client.post("/appointments", json=booking)
    assert response.status_code == 400
    assert response.get_json()["details"]["scheduled_at"] == "must be in the future"


def test_book_rejects_double_booking_provider(client, booking, booked):
    booking["patient_name"] = "Someone Else"
    response = client.post("/appointments", json=booking)
    assert response.status_code == 409


def test_book_allows_slot_after_cancellation(client, booking, booked):
    client.delete(f"/appointments/{booked['id']}")
    assert client.post("/appointments", json=booking).status_code == 201


def test_book_allows_same_time_different_provider(client, booking, booked):
    booking["provider"] = "Dr. Jones"
    assert client.post("/appointments", json=booking).status_code == 201


# --- Viewing ---------------------------------------------------------------


def test_get_appointment(client, booked):
    response = client.get(f"/appointments/{booked['id']}")
    assert response.status_code == 200
    assert response.get_json() == booked


def test_get_unknown_appointment_returns_404(client):
    response = client.get("/appointments/does-not-exist")
    assert response.status_code == 404
    assert response.get_json() == {"error": "appointment not found"}


def test_list_appointments_empty(client):
    response = client.get("/appointments")
    assert response.status_code == 200
    assert response.get_json() == {"appointments": []}


def test_list_appointments_sorted_by_time(client, booking):
    later = dict(booking, scheduled_at=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat())
    client.post("/appointments", json=later)
    client.post("/appointments", json=booking)
    appointments = client.get("/appointments").get_json()["appointments"]
    times = [a["scheduled_at"] for a in appointments]
    assert len(times) == 2
    assert times == sorted(times)


def test_list_appointments_filter_by_status(client, booking, booked):
    other = dict(booking, provider="Dr. Jones")
    client.post("/appointments", json=other)
    client.delete(f"/appointments/{booked['id']}")

    cancelled = client.get("/appointments?status=cancelled").get_json()["appointments"]
    active = client.get("/appointments?status=booked").get_json()["appointments"]
    assert [a["id"] for a in cancelled] == [booked["id"]]
    assert [a["provider"] for a in active] == ["Dr. Jones"]


def test_list_appointments_rejects_unknown_status(client):
    response = client.get("/appointments?status=pending")
    assert response.status_code == 400


# --- Cancelling ------------------------------------------------------------


def test_cancel_appointment(client, booked):
    response = client.delete(f"/appointments/{booked['id']}")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] is not None
    assert client.get(f"/appointments/{booked['id']}").get_json()["status"] == "cancelled"


def test_cancel_twice_returns_409(client, booked):
    client.delete(f"/appointments/{booked['id']}")
    response = client.delete(f"/appointments/{booked['id']}")
    assert response.status_code == 409
    assert response.get_json() == {"error": "appointment is already cancelled"}


def test_cancel_unknown_appointment_returns_404(client):
    response = client.delete("/appointments/does-not-exist")
    assert response.status_code == 404


# --- /metrics --------------------------------------------------------------


def test_metrics_endpoint_exposes_prometheus_text(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    assert b"healthqueue_http_requests_total" in response.data


def test_metrics_track_appointments(client, booking, booked):
    client.post("/appointments", json=dict(booking, provider="Dr. Jones"))
    client.delete(f"/appointments/{booked['id']}")
    text = client.get("/metrics").get_data(as_text=True)
    assert "healthqueue_appointments_booked_total 2.0" in text
    assert "healthqueue_appointments_cancelled_total 1.0" in text
    assert "healthqueue_appointments_active 1.0" in text


def test_metrics_use_route_template_labels(client, booked):
    client.get(f"/appointments/{booked['id']}")
    client.get("/nope")
    text = client.get("/metrics").get_data(as_text=True)
    assert 'endpoint="/appointments/<appointment_id>",method="GET",status="200"' in text
    assert 'endpoint="unmatched"' in text
    assert booked["id"] not in text
    assert 'endpoint="/metrics"' not in text


def test_metrics_isolated_between_apps():
    first = create_app().test_client()
    first.get("/health")
    second = create_app().test_client()
    assert 'endpoint="/health"' not in second.get("/metrics").get_data(as_text=True)
