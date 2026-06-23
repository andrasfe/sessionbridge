# Build context = repo root.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY services/artifacts/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY services/artifacts/ ./
RUN mkdir -p /data/artifacts /data/metadata
EXPOSE 8084
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8084"]
