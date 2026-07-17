FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/web/requirements.txt services/web/requirements.txt
COPY services/john/requirements.txt services/john/requirements.txt
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -r services/web/requirements.txt \
    -r services/john/requirements.txt \
    && pip install --no-cache-dir --no-deps .

COPY . .
RUN python -m scripts.seed_john_demo

CMD ["python", "app.py"]
