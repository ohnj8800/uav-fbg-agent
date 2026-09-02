FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/__init__.py /app/services/__init__.py
COPY services/agent /app/services/agent

RUN mkdir -p /app/runtime && \
    useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app/runtime
USER appuser

EXPOSE 8000
CMD ["python", "-m", "services.agent.app.server"]

