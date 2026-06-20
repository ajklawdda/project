import os
import logging
import time
import imaplib
import email
import threading
import subprocess
from flask import Flask, jsonify, request
from stem import Signal
from stem.control import Controller
import requests


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

logging.getLogger('stem').setLevel(logging.CRITICAL)
logging.getLogger('stem.control').setLevel(logging.CRITICAL)
logging.getLogger('stem.socket').setLevel(logging.CRITICAL)
logging.getLogger('stem.connection').setLevel(logging.CRITICAL)

# Глобальные переменные для хранения состояния
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
last_emails = []
tor_ip = None
service = None
data = {}
last_code = ""
reconnections = 0


def get_last_email():
    IMAP_SERVER = 'imap.gmail.com'
    IMAP_PORT = 993

    email_address = "fhsjarij@gmail.com"
    app_password = os.environ.get("APP_PASSWORD")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(email_address, app_password)
    mail.select("[Gmail]/Spam", readonly=True)

    status, messages = mail.search(None, f'(FROM "hidemy.name")')

    if status == 'OK':
        email_id = messages[0].split()[-1]
        status, msg_data = mail.fetch(email_id, '(RFC822)')
        if status == 'OK':
            msg = email.message_from_bytes(msg_data[0][1])
            subject = email.header.decode_header(msg['Subject'])[0][0]
            code = subject.decode("utf-8")
            code = code[code.find(":") + 1:].strip()

    mail.close()
    mail.logout()

    return code


def get_current_ip(proxies):
    """Получить текущий IP через Tor"""
    try:
        session = requests.Session()
        session.proxies = proxies
        session.keep_alive = False
        response = session.get('https://check.torproject.org/api/ip', timeout=10)
        session.close()
        return response.json()['IP']
    except Exception as e:
        logging.info(f"get_current_ip error: {e}")
        return None


def restart_tor():
    """Перезапуск Tor внутри контейнера"""
    try:

        subprocess.run(['pkill', 'tor'], capture_output=True)
        time.sleep(2)
        subprocess.Popen(['tor', '-f', '/etc/tor/torrc'],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

        time.sleep(10)  # Ждём полного запуска
        logging.info("✅ Tor перезапущен")
        return True

    except Exception as e:
        logging.error(f"❌ Ошибка перезапуска Tor: {e}")
        return False


def renew_tor_ip(delay=5):
    """Смена IP через Tor"""
    global reconnections, controller
    if reconnections >= 30:
        logging.info("Перезапуск Tor")
        reconnections = 0
        result = restart_tor()
        return result
    try:
        if 'controller' not in globals() or controller is None:
            controller = Controller.from_port(port=9051)
            controller.authenticate()
        controller.signal(Signal.NEWNYM)
        time.sleep(delay)
        reconnections += 1
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка смены IP: {e}")
        try:
            controller = Controller.from_port(port=9051)
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            time.sleep(delay)
            reconnections += 1
            return True
        except:
            controller = None
            return False


def send_post_through_tor(data, url):
    """Отправить POST-запрос через Tor"""
    proxies = {
        'http': 'socks5h://127.0.0.1:9050',
        'https': 'socks5h://127.0.0.1:9050'
    }

    session = requests.Session()
    session.proxies = proxies
    session.keep_alive = False
    response = session.post(url, data=data, timeout=10)
    session.close()
    return response


def get_new_duck_email():
    return requests.post("https://quack.duckduckgo.com/api/email/addresses", headers={
        "Authorization": os.environ.get("DUCK_PASSWORD"),
    }).json()["address"] + "@duck.com"


# Глобальный флаг
is_busy = False
busy_lock = threading.Lock()


def background_code_finder():
    """Вся логика поиска кода в фоне"""
    global is_busy, last_code

    try:

        url = "https://hide-my-name.cc/demo/success/"
        data["demo_mail"] = get_new_duck_email()
        logging.info(f"Почта: {data['demo_mail']}")

        proxies = {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050'
        }

        ip = get_current_ip(proxies)
        if ip is None:
            logging.info("!!! ip is None")
            return

        logging.info(f"ip: {ip}")
        delay = 5
        number_of_try = 0

        ok = False
        while not ok:
            response1 = send_post_through_tor(data, url)
            text = response1.text
            ok = "Тестовый доступ уже был запрошен ранее" not in text
            logging.info(f"ok: {ok}")

            while True:
                did = renew_tor_ip(delay=delay)
                if did:
                    new_ip = get_current_ip(proxies)
                    if new_ip == ip:
                        continue
                    else:
                        ip = new_ip
                        logging.info(f"Новый IP: {ip}")
                        break
                logging.info("Trying again to find new ip")

            number_of_try += 1
            if number_of_try > 10:
                number_of_try = 0
                data["demo_mail"] = get_new_duck_email()
                logging.info(f"Новая почта: {data['demo_mail']}")

        logging.info(f"Код найден и отправлен на почту {data['demo_mail']}")
        logging.info("Попытка взять код:")

        for _ in range(3):
            try:
                logging.info(f"Попытка номер: {_ + 1}")
                code = get_last_email()
                if code == last_code:
                    logging.info("Trying again")
                    time.sleep(5)
                    continue
                else:
                    logging.info(f"Найден код: {code}")
                    break
            except Exception as e:
                logging.error(e)
        else:
            logging.info("Не получилось извлечь код, пропустим шаг")

        logging.info(f"Закончено, все коды: {code}")

        last_code = code

        print("✅ Поиск кода завершён")

    except Exception as e:
        print(f"❌ Ошибка в фоновой задаче: {e}")
    finally:
        # ВАЖНО: освобождаем флаг
        with busy_lock:
            is_busy = False
            print("🔓 Сервер снова свободен")


@app.route("/start-finding-new-code", methods=["GET"])
def find_new_code():
    global is_busy

    # Проверяем, не занят ли сервер
    with busy_lock:
        if is_busy:
            return "busy"

        # Занимаем сервер
        is_busy = True

    # Запускаем фоновую задачу
    thread = threading.Thread(target=background_code_finder)
    thread.daemon = True
    thread.start()

    # Мгновенный ответ
    return "starting"


@app.route("/status", methods=["GET"])
def status():
    """Проверить статус сервера"""
    with busy_lock:
        return jsonify({
            'busy': is_busy,
            'message': 'Finding code in progress' if is_busy else 'Ready for new requests'
        })


@app.route("/get-last-code", methods=["GET"])
def get_last_code():
    global last_code
    temp = last_code
    last_code = ""
    return temp


if __name__ == "__main__":
    # Инициализация

    # Запускаем Tor (он уже должен быть запущен отдельно)
    # time.sleep(5)

    # # Запускаем веб-сервер (Render ожидает, что сервис слушает порт)
    # port = int(os.environ.get('PORT', 10000))
    # app.run(host='0.0.0.0', port=port)
    print(get_new_duck_email())
    print(get_new_duck_email())
    print(get_new_duck_email())
    print(get_new_duck_email())
