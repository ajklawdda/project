FROM python:3.11-slim

RUN apt-get update && apt-get install -y tor && rm -rf /var/lib/apt/lists/*

# Настраиваем Tor с минимальными логами
RUN echo "SocksPort 0.0.0.0:9050" > /etc/tor/torrc && \
    echo "ControlPort 9051" >> /etc/tor/torrc && \
    echo "Log notice file /dev/null" >> /etc/tor/torrc && \
    echo "Log warn stderr" >> /etc/tor/torrc && \
    echo "SafeLogging 1" >> /etc/tor/torrc

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD tor & gunicorn gmail_reader:app --bind 0.0.0.0:$PORT --timeout 120
