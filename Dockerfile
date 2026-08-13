FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY commerce ./commerce
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY frontend ./frontend
COPY .streamlit ./.streamlit
COPY docs ./docs
COPY README.md* ./

CMD ["uvicorn", "commerce.agent_api:app", "--host", "0.0.0.0", "--port", "8000"]
