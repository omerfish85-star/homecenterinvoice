FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ backend/
COPY ceo_pilot.html .
COPY wsgi.py .

# DATA_DIR can be overridden to a mounted volume for persistence
ENV DATA_DIR=/app/backend/data
ENV FLASK_ENV=production
ENV PORT=8000

EXPOSE 8000

# 2 workers; increase to (2 × CPU + 1) for a larger instance
CMD ["gunicorn", "wsgi:app", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "120"]
