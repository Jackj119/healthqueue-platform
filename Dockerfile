FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first to take advantage of layer caching
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ app/
COPY wsgi.py .

# Run as a non-root user
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 5000

# Appointments are held in process memory, so run a single worker (with threads)
# so every request sees the same data.
ENTRYPOINT ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--access-logfile", "-", "wsgi:app"]
