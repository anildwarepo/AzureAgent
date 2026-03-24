# Use Python 3.12 slim image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies for psycopg (PostgreSQL) and other native libs
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency management
RUN pip install --no-cache-dir uv

# Copy pyproject.toml and uv.lock first for better caching
COPY pyproject.toml uv.lock* ./

# Install Python dependencies using uv (--prerelease=allow for beta packages like agent-framework)
RUN uv pip install --system --no-cache --prerelease=allow -r pyproject.toml

# Copy application code
COPY . .

# Expose the port
EXPOSE 8080

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the FastAPI application (without --reload for production)
CMD ["uvicorn", "af_fastapi:app", "--host", "0.0.0.0", "--port", "8080"]
