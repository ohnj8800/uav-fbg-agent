FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/__init__.py /app/services/__init__.py
COPY services/analysis /app/services/analysis

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8001
CMD ["python", "-m", "services.analysis.app.server"]

