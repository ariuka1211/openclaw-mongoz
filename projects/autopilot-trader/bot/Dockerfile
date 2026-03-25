FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (NOT config — mounted as volume)
COPY bot.py .
COPY healthcheck.py .
COPY dsl.py .

# Don't copy config.yml — it's mounted as a volume with secrets
VOLUME /app/config

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python healthcheck.py

# Run bot with config from volume mount
ENV BOT_CONFIG=/app/config/config.yml
CMD ["python", "bot.py"]
