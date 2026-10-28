# Pinned by digest so every build uses the exact same base image (a tag can be
# re-pointed upstream). Dependabot bumps the digest when 3.12-slim is updated.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

# .pyc files and pip's cache would only add size to an image that is never
# modified at runtime; unbuffered output makes logs appear in `kubectl logs`
# as they are written instead of when a buffer fills.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first to take advantage of layer caching
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ app/
COPY wsgi.py .

# Run as a non-root user. The fixed UID matches runAsUser in
# manifests/deployment.yaml, which the restricted Pod Security level requires.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 5000

# Appointments are held in process memory, so run a single worker (with threads)
# so every request sees the same data.
ENTRYPOINT ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--access-logfile", "-", "wsgi:app"]
