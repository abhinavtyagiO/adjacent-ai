FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# Both services run the source in /srv/app. Keep only one copy of the application.
RUN useradd --create-home app && mkdir -p /srv/chatgpt /srv/media \
    && chown app:app /srv/chatgpt /srv/media && chmod 700 /srv/media
ARG APP_BUILD_REVISION
ENV APP_BUILD_REVISION=${APP_BUILD_REVISION}
LABEL org.opencontainers.image.revision=${APP_BUILD_REVISION}
USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
