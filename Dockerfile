FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/
COPY agent_system_prompts.json .

RUN useradd -m appuser
USER appuser

ENV PYTHONPATH=/app/src
EXPOSE 8000

CMD ["uvicorn", "book_editor.main:app", "--host", "0.0.0.0", "--port", "8000"]
