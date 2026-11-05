# Attendance web application: gunicorn, notification worker and digest scheduler all
# run from this image with different commands (see deploy/entrypoint.sh).
# Nothing secret, no database, no photos and no accounts are baked into the image.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=Project101.settings.prod \
    PRIVATE_MEDIA_ROOT=/data/private_media \
    FACE_MODEL_DIR=/data/models \
    DJANGO_STATIC_ROOT=/app/staticfiles

# opencv-python-headless needs no GUI libraries; the slim image already has CA certificates.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY requirements.txt requirements-postgres.txt ./
# Behind a TLS-inspecting proxy, pass its CA as a build secret (never stored in a layer):
#   docker build --secret id=pip_ca,src=/path/to/proxy-ca.pem .
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    pip install -r requirements-postgres.txt

COPY --chown=app:app . .
# Static files are collected at build time with throwaway development settings; the
# production secret is only ever provided at runtime.
RUN DJANGO_SETTINGS_MODULE=Project101.settings.dev DJANGO_SECRET_KEY=build-only-not-used-at-runtime \
        python manage.py collectstatic --noinput -v 0 \
    && mkdir -p /data/private_media /data/models /srv/static \
    && chown app:app /app \
    && chown -R app:app /data /app/staticfiles /srv/static \
    && chmod 0700 /data/private_media

USER app
VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["web"]
