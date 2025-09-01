# Use an official lightweight Python image
FROM python:3.13-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.1.4

# Set the working directory inside the container
WORKDIR /hylde

# Install Poetry
RUN apt-get update && apt-get install -y curl && \
    curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /root/.local/bin/poetry /usr/local/bin/poetry

# Copy the project files into the container
COPY pyproject.toml poetry.lock config.toml README.md ./
COPY hylde ./hylde

# Install dependencies
RUN poetry install --no-dev --no-interaction --no-ansi

# Expose the port the Flask app runs on
EXPOSE 5000

# Define the command to run the Flask app
CMD ["poetry", "run", "python", "hylde/server.py"]
