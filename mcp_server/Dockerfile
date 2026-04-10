FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency management
RUN pip install uv

# Copy project files
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv pip install --system --no-cache --prerelease=allow -r pyproject.toml

# Copy application code
COPY *.py ./

# Expose MCP server port
EXPOSE 3001

ENV PYTHONUNBUFFERED=1

# Run the MCP server
CMD ["python", "azure_ops_mcp_server.py", "--port", "3001", "--host", "0.0.0.0"]
