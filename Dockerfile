FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/web/requirements.txt services/web/requirements.txt
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -r services/web/requirements.txt \
    && pip install --no-cache-dir --no-deps .

COPY . .

CMD ["python", "app.py"]
