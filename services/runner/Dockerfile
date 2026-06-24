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
RUN chmod +x entrypoint.sh
EXPOSE 8082
# entrypoint runs headed under Xvfb when HEADLESS=false, else plain headless.
CMD ["./entrypoint.sh"]
