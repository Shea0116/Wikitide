FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --system --uid 10001 --create-home kb

COPY *.py ./
COPY release-notes.json ./
COPY templates ./templates
COPY static ./static
COPY scripts ./scripts
RUN mkdir -p /app/content /data && chown kb:kb /data

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
