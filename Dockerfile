FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Keep boot deterministic on Railway: schema/static/category setup must finish
# before traffic reaches ASGI. Connection/statement timeouts in Django settings
# make a broken dependency fail fast so Railway can restart the container.
CMD sh -c "python manage.py migrate --noinput && python manage.py collectstatic --noinput && python manage.py seed_categories && exec uvicorn config.asgi:application --host 0.0.0.0 --port ${PORT:-8000} --timeout-keep-alive 5 --ws-ping-interval 20 --ws-ping-timeout 20 --limit-concurrency ${UVICORN_LIMIT_CONCURRENCY:-200}"
