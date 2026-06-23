# Build context = repo root. Use the official Playwright image, which ships
# Chromium + all required OS deps preinstalled — avoids the fragile
# `playwright install --with-deps` apt step (which breaks on Debian trixie).
# The tag MUST match the pinned playwright version in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY services/runner/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY services/runner/ ./
EXPOSE 8082
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8082"]
