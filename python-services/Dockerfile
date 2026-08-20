# Playwright's official image ships Chromium plus every system library it
# needs. The tag version must match playwright's version in requirements.txt.
# The noble (Ubuntu 24.04) variant is required: jammy has Python 3.10, and
# scikit-image 0.26.0 needs Python >= 3.11.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# main.py reads HOST/PORT from the environment (Render sets PORT).
CMD ["python", "main.py"]
