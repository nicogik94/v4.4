FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# Install the validated, exact Python dependency graph.
COPY requirements.txt requirements.lock.txt ./
COPY scripts/validate_requirements_lock.py scripts/validate_requirements_lock.py
RUN python -m pip install --no-cache-dir --upgrade pip==25.3 && \
    python scripts/validate_requirements_lock.py && \
    python -m pip install --no-cache-dir -r requirements.lock.txt

# Copy application
COPY . .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
