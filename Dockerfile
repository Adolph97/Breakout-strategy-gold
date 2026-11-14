FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user for security. It must own the app directory (logs/) and the
# database directory, which docker-compose mounts as a volume SEPARATE from the code.
RUN useradd --create-home --shell /bin/bash app \
    && mkdir -p /app/logs /app/db_data \
    && chown -R app:app /app
ENV GOLD_DB_PATH=/app/db_data/gold_trader.db
USER app

# Set working directory
WORKDIR /app

# Default command
CMD ["python", "main.py"]