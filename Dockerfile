# =============================================================================
# Dockerfile for Press Release Collection Pipeline (Cloud Run)
# =============================================================================
# This builds the container image deployed to Google Cloud Run.
# The entry point is the functions-framework HTTP server, which routes
# incoming POST requests to the press_release_collection() function in main.py.
#
# Build & deploy:
#   See scripts/deploy.sh (Linux/Mac) or scripts/deploy.ps1 (Windows)
# =============================================================================

# Use Python 3.13 slim image (matches .python-version)
FROM python:3.13-slim

# Set working directory inside the container
WORKDIR /app

# Install system dependencies needed by some Python packages:
#   gcc/g++ -- required by newspaper3k and lxml compilation
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml first (for Docker layer caching -- dependencies
# are re-installed only when pyproject.toml changes, not on every code change)
COPY pyproject.toml ./

# Install Python dependencies from pyproject.toml
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Download NLTK punkt tokenizer (needed by newspaper3k for sentence splitting)
RUN python -c "import nltk; nltk.download('punkt_tab', quiet=True)"

# Copy all application code into the container
COPY . .

# Create data directories (these are ephemeral in Cloud Run but needed
# by the pipeline for intermediate CSV files during a single run)
RUN mkdir -p inputs outputs

# Set environment variables
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# Start the functions-framework HTTP server.
# It listens on $PORT and routes POST requests to the
# press_release_collection() function in main.py.
CMD exec functions-framework --target=press_release_collection --port=$PORT
