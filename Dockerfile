FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (ping, iproute2 for network diagnostics)
RUN apt-get update && apt-get install -y \
    iputils-ping \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "app.py", "--web"]
