import time
from datetime import datetime, timezone

from flask import Flask, g, jsonify, request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from werkzeug.exceptions import HTTPException

from app.store import (
    BOOKED,
    CANCELLED,
    AppointmentConflict,
    AppointmentNotFound,
    AppointmentStore,
)

REQUIRED_FIELDS = ("patient_name", "provider", "scheduled_at")
MAX_FIELD_LENGTH = 200


def create_app(store=None):
    app = Flask(__name__)
    store = store or AppointmentStore()
    app.config["STORE"] = store

    # A per-app registry keeps metrics isolated when several apps are created
    # in one process (e.g. one per test).
    registry = CollectorRegistry()
    request_count = Counter(
        "healthqueue_http_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status"],
        registry=registry,
    )
    request_latency = Histogram(
        "healthqueue_http_request_duration_seconds",
        "HTTP request latency in seconds",
        ["method", "endpoint"],
        registry=registry,
    )
    appointments_booked = Counter(
        "healthqueue_appointments_booked_total",
        "Appointments booked",
        registry=registry,
    )
    appointments_cancelled = Counter(
        "healthqueue_appointments_cancelled_total",
        "Appointments cancelled",
        registry=registry,
    )
    appointments_active = Gauge(
        "healthqueue_appointments_active",
        "Appointments currently booked and not cancelled",
        registry=registry,
    )
    appointments_active.set_function(lambda: store.count(BOOKED))

    @app.before_request
    def start_timer():
        g.start_time = time.perf_counter()

    @app.after_request
    def record_metrics(response):
        # Use the route template, not the raw path, to keep label cardinality bounded.
        endpoint = request.url_rule.rule if request.url_rule else "unmatched"
        if endpoint != "/metrics":
            elapsed = time.perf_counter() - g.start_time
            request_latency.labels(request.method, endpoint).observe(elapsed)
            request_count.labels(
                request.method, endpoint, str(response.status_code)
            ).inc()
        return response

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return jsonify(error=error.description), error.code

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.get("/metrics")
    def metrics():
        return generate_latest(registry), 200, {"Content-Type": CONTENT_TYPE_LATEST}

    @app.post("/appointments")
    def book_appointment():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="request body must be a JSON object"), 400

        errors = _validate_booking(payload)
        if errors:
            return jsonify(error="validation failed", details=errors), 400

        try:
            appointment = store.book(
                patient_name=payload["patient_name"].strip(),
                provider=payload["provider"].strip(),
                scheduled_at=_parse_datetime(payload["scheduled_at"]).isoformat(),
                reason=(payload.get("reason") or "").strip() or None,
            )
        except AppointmentConflict as exc:
            return jsonify(error=str(exc)), 409

        appointments_booked.inc()
        location = f"/appointments/{appointment['id']}"
        return jsonify(appointment), 201, {"Location": location}

    @app.get("/appointments")
    def list_appointments():
        status = request.args.get("status")
        if status is not None and status not in (BOOKED, CANCELLED):
            return jsonify(error=f"status must be one of: {BOOKED}, {CANCELLED}"), 400
        return jsonify(appointments=store.list(status=status))

    @app.get("/appointments/<appointment_id>")
    def get_appointment(appointment_id):
        try:
            return jsonify(store.get(appointment_id))
        except AppointmentNotFound:
            return jsonify(error="appointment not found"), 404

    @app.delete("/appointments/<appointment_id>")
    def cancel_appointment(appointment_id):
        try:
            appointment = store.cancel(appointment_id)
        except AppointmentNotFound:
            return jsonify(error="appointment not found"), 404
        except AppointmentConflict as exc:
            return jsonify(error=str(exc)), 409

        appointments_cancelled.inc()
        return jsonify(appointment)

    return app


def _validate_booking(payload):
    errors = {}
    for field in REQUIRED_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            errors[field] = "is required and must be a non-empty string"
        elif len(value) > MAX_FIELD_LENGTH:
            errors[field] = f"must be at most {MAX_FIELD_LENGTH} characters"

    reason = payload.get("reason")
    if reason is not None:
        if not isinstance(reason, str):
            errors["reason"] = "must be a string"
        elif len(reason) > MAX_FIELD_LENGTH:
            errors["reason"] = f"must be at most {MAX_FIELD_LENGTH} characters"

    if "scheduled_at" not in errors:
        scheduled_at = _parse_datetime(payload["scheduled_at"])
        if scheduled_at is None:
            errors["scheduled_at"] = "must be an ISO 8601 datetime with a timezone offset"
        elif scheduled_at <= datetime.now(timezone.utc):
            errors["scheduled_at"] = "must be in the future"

    return errors


def _parse_datetime(value):
    """Parse an ISO 8601 string into an aware UTC datetime, or return None."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)
