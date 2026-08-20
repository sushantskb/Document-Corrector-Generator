# Playwright's official image ships Chromium plus every system library it
# needs. The tag version must match playwright's version in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.62.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# main.py reads HOST/PORT from the environment (Render sets PORT).
CMD ["python", "main.py"]
