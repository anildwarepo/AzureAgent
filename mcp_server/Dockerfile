FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency management
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY *.py .

# Install dependencies
RUN uv pip install --system -e .

# Expose MCP server port
EXPOSE 3001

# Run the MCP server
CMD ["python", "azure_ops_mcp_server.py", "--port", "3001", "--host", "0.0.0.0"]
