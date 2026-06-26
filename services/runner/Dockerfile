# Build context = repo root. Use the official Playwright image, which ships
# Chromium + all required OS deps preinstalled — avoids the fragile
# `playwright install --with-deps` apt step (which breaks on Debian trixie).
# The tag MUST match the pinned playwright version in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY services/runner/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Optional OpenHands harness (AGENT_HARNESS=openhands). Off by default to keep
# the image lean; build with `--build-arg INSTALL_OPENHANDS=1` to include it.
# openhands-ai pulls playwright>=1.55, so re-pin to the base image's 1.49.1
# afterwards — our harness drives SessionBridge's own browser, not OpenHands'.
ARG INSTALL_OPENHANDS=0
COPY services/runner/requirements-openhands.txt .
RUN if [ "$INSTALL_OPENHANDS" = "1" ]; then \
      pip install --no-cache-dir -r requirements-openhands.txt && \
      pip install --no-cache-dir --force-reinstall playwright==1.49.1; \
    fi
COPY shared ./shared
COPY services/runner/ ./
RUN chmod +x entrypoint.sh
EXPOSE 8082
# entrypoint runs headed under Xvfb when HEADLESS=false, else plain headless.
CMD ["./entrypoint.sh"]
