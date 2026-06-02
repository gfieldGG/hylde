# Use an official lightweight Python image
FROM python:3.14-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/hylde/.venv/bin:$PATH"

# Set the working directory inside the container
WORKDIR /hylde

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /uvx /usr/local/bin/

# Copy the project files into the container
COPY pyproject.toml uv.lock config.toml README.md ./
COPY hylde ./hylde

# Install dependencies
RUN uv sync --locked --no-dev

# Expose the port the Flask app runs on
EXPOSE 5000

# Define the command to run the Flask app
CMD ["python", "hylde/server.py"]
