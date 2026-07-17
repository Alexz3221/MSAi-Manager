FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV GOOGLE_GENAI_USE_VERTEXAI=TRUE

WORKDIR /app

COPY services/john/requirements.txt /tmp/john-requirements.txt
RUN pip install --no-cache-dir -r /tmp/john-requirements.txt

COPY services/john/ services/john/
COPY scripts/__init__.py scripts/__init__.py
COPY scripts/seed_john_demo.py scripts/seed_john_demo.py
COPY sql/john_demo.sql sql/john_demo.sql
RUN python -m scripts.seed_john_demo

EXPOSE 8080

CMD ["adk", "api_server", "--host=0.0.0.0", "--port=8080", "--no-reload", "--session_service_uri=memory://", "--artifact_service_uri=memory://", "services/john"]
