#!/bin/bash
# Запускаем Tor в фоне
tor -f /etc/tor/torrc &
# Даём Tor время на запуск
sleep 10
# Запускаем Python скрипт
python /app/gmail_reader.py
