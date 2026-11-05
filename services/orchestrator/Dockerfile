FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY services/orchestrator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/orchestrator/app ./app
COPY services/orchestrator/working_capital_agent ./working_capital_agent
COPY packages/shared/fixtures ./fixtures

ENV WC_FIXTURE_PATH=/app/fixtures/retailco-demo.json \
    ORCHESTRATOR_DATA_DIR=/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
