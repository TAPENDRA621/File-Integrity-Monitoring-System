# Use official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY server.py fims_agent.py viewer.py generate_cert.py wsgi.py ./
COPY templates/ ./templates/

# Create monitored directories and persistent db directory
RUN mkdir -p test_monitor important_files db

# Expose server port
EXPOSE 5000

# Default command: run via Gunicorn with eventlet worker (production-safe)
CMD ["gunicorn", "-k", "eventlet", "-w", "1", "--bind", "0.0.0.0:5000", "wsgi:app"]
