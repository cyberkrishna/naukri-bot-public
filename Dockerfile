FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

# Install Python deps first for layer caching
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Now copy the application source
COPY . .

# Make startup script executable.
RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
