FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY sweep_bot.py watchdog.py backtest.py config.example.json ./

# Copy config if exists, otherwise fallback to example
RUN cp config.example.json config.json

ENV PYTHONUNBUFFERED=1

# Run with watchdog supervisor
CMD ["python", "watchdog.py"]
