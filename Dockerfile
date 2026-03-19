FROM python:3.11-slim

# Устанавливаем Tor
RUN apt-get update && apt-get install -y tor && rm -rf /var/lib/apt/lists/*

# Настраиваем Tor
RUN echo "SocksPort 0.0.0.0:9050" > /etc/tor/torrc
RUN echo "ControlPort 9051" >> /etc/tor/torrc

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Запускаем Tor и веб-сервер
CMD tor & gunicorn gmail_reader:app --bind 0.0.0.0:$PORT --timeout 0
