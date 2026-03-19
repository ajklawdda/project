FROM python:3.11-slim

# Устанавливаем системные зависимости и Tor
RUN apt-get update && apt-get install -y \
    tor \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Настраиваем Tor
RUN echo "SocksPort 0.0.0.0:9050" > /etc/tor/torrc && \
    echo "ControlPort 9051" >> /etc/tor/torrc && \
    echo "CookieAuthentication 1" >> /etc/tor/torrc

# Создаём рабочую директорию
WORKDIR /app

# Копируем зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Делаем скрипт запуска исполняемым
RUN chmod +x /app/start.sh

# Запускаем Tor и Python скрипт
CMD ["/app/start.sh"]
