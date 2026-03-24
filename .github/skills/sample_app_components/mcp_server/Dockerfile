# Use Python 3.12 slim image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies for psycopg (PostgreSQL)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency management
RUN pip install --no-cache-dir uv

# Copy pyproject.toml and uv.lock first for better caching
COPY pyproject.toml uv.lock* ./

# Install Python dependencies using uv
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy application code
COPY . .

# Expose the port
EXPOSE 3002

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the AGE MCP server
CMD ["python", "age_mcp_server.py"]
