# Use Python 3.13 slim image
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY pyproject.toml ./

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Download spaCy model
RUN python -m spacy download en_core_web_lg

# Download NLTK data
RUN python -c "import nltk; nltk.download('punkt_tab', quiet=True)"

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p inputs outputs

# Set environment variables
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# Run the application
CMD exec functions-framework --target=press_release_collection --port=$PORT
