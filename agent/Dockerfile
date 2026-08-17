FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (ping, iproute2)
RUN apt-get update && apt-get install -y \
    iputils-ping \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source and agent scripts
COPY src/ src/
COPY agent/ agent/

CMD ["python", "agent/agent.py", "--start"]
