# healthqueue-platform

Flask appointment API for HealthQueue.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/appointments` | Book an appointment (`patient_name`, `provider`, `scheduled_at` required; `reason` optional) |
| `GET` | `/appointments` | List appointments, optionally `?status=booked\|cancelled` |
| `GET` | `/appointments/<id>` | View one appointment |
| `DELETE` | `/appointments/<id>` | Cancel an appointment (kept with `status: cancelled`) |
| `GET` | `/health` | Liveness/readiness check |
| `GET` | `/metrics` | Prometheus metrics |

`scheduled_at` must be a future ISO 8601 datetime with a timezone offset, e.g. `2030-01-01T09:00:00Z`.
A provider cannot have two active appointments at the same time (`409`).

Storage is in memory, so data is lost on restart and the service runs as one gunicorn worker and one replica.

## Development

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # .venv/bin/pip on Linux/macOS
.venv/Scripts/pytest --cov=app --cov-report=term-missing
```

## Docker

```sh
docker build -t healthqueue-platform .
docker run -p 5000:5000 healthqueue-platform
```

## Kubernetes

```sh
kubectl apply -f manifests/
```
