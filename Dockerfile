FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

# Install Python deps first for layer caching
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Now copy the application source
COPY . .

# Default command is set per service in render.yaml.
# Local sanity-check default: print Python + Playwright versions and exit.
CMD ["python", "-c", "import sys, playwright; print(f'Python {sys.version}'); print(f'Playwright {playwright.__version__}')"]
