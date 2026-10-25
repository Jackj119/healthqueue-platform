import threading
import uuid
from datetime import datetime, timezone

BOOKED = "booked"
CANCELLED = "cancelled"


class AppointmentNotFound(Exception):
    pass


class AppointmentConflict(Exception):
    pass


class AppointmentStore:
    """Thread-safe in-memory appointment store.

    Data lives in process memory, so it is lost on restart and is not shared
    between processes or pods.
    """

    def __init__(self):
        self._appointments = {}
        self._lock = threading.Lock()

    def book(self, patient_name, provider, scheduled_at, reason=None):
        with self._lock:
            for existing in self._appointments.values():
                if (
                    existing["status"] == BOOKED
                    and existing["provider"] == provider
                    and existing["scheduled_at"] == scheduled_at
                ):
                    raise AppointmentConflict(
                        "provider already has an appointment at that time"
                    )
            appointment = {
                "id": str(uuid.uuid4()),
                "patient_name": patient_name,
                "provider": provider,
                "scheduled_at": scheduled_at,
                "reason": reason,
                "status": BOOKED,
                "created_at": _now(),
                "cancelled_at": None,
            }
            self._appointments[appointment["id"]] = appointment
            return dict(appointment)

    def get(self, appointment_id):
        with self._lock:
            appointment = self._appointments.get(appointment_id)
            if appointment is None:
                raise AppointmentNotFound(appointment_id)
            return dict(appointment)

    def list(self, status=None):
        with self._lock:
            appointments = [
                dict(a)
                for a in self._appointments.values()
                if status is None or a["status"] == status
            ]
        return sorted(appointments, key=lambda a: a["scheduled_at"])

    def count(self, status):
        with self._lock:
            return sum(1 for a in self._appointments.values() if a["status"] == status)

    def cancel(self, appointment_id):
        with self._lock:
            appointment = self._appointments.get(appointment_id)
            if appointment is None:
                raise AppointmentNotFound(appointment_id)
            if appointment["status"] == CANCELLED:
                raise AppointmentConflict("appointment is already cancelled")
            appointment["status"] = CANCELLED
            appointment["cancelled_at"] = _now()
            return dict(appointment)


def _now():
    return datetime.now(timezone.utc).isoformat()
