import os
import pickle
import logging
import time
import base64
import threading
from flask import Flask, jsonify, request
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from stem import Signal
from stem.control import Controller
import requests

# Настройка логирования
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
last_code = ""
reconnections = 0

def setup_credentials_from_env():
    """Создаёт credentials.json и token.pickle из переменных окружения"""
    
    # 1. Обрабатываем GOOGLE_CREDENTIALS (JSON строка)
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if google_creds_json:
        try:
            # Проверяем, что это валидный JSON
            import json
            creds_dict = json.loads(google_creds_json)
            print(f"✅ GOOGLE_CREDENTIALS: валидный JSON, client_id: {creds_dict.get('installed', {}).get('client_id', 'не найден')[:20]}...")
            
            # Сохраняем в файл credentials.json
            with open('credentials.json', 'w', encoding='utf-8') as f:
                f.write(google_creds_json)
            print("✅ credentials.json создан из GOOGLE_CREDENTIALS")
            
        except json.JSONDecodeError as e:
            print(f"❌ GOOGLE_CREDENTIALS: невалидный JSON: {e}")
            return False
        except Exception as e:
            print(f"❌ Ошибка при создании credentials.json: {e}")
            return False
    else:
        # Проверяем, может файл уже существует
        if os.path.exists('credentials.json'):
            print("📁 credentials.json найден в файловой системе")
        else:
            print("⚠️ credentials.json не найден ни в переменных, ни в файлах")
            return False
    
    # 2. Обрабатываем TOKEN_PICKLE_B64 (бинарный файл в base64)
    token_b64 = os.environ.get('TOKEN_PICKLE_B64')
    if token_b64:
        try:
            token_bytes = base64.b64decode(token_b64)
            
            # Проверяем валидность pickle
            try:
                creds = pickle.loads(token_bytes)
                print(f"✅ Токен валиден, истекает: {getattr(creds, 'expiry', 'неизвестно')}")
            except Exception as e:
                print(f"⚠️ Предупреждение: токен может быть повреждён: {e}")
            
            # Сохраняем в файл
            with open('token.pickle', 'wb') as f:
                f.write(token_bytes)
            print(f"✅ token.pickle создан из переменной окружения (размер: {len(token_bytes)} байт)")
            
        except Exception as e:
            print(f"❌ Ошибка при создании token.pickle: {e}")
            return False
    else:
        if os.path.exists('token.pickle'):
            print("📁 token.pickle найден в файловой системе")
        else:
            print("⚠️ token.pickle не найден")
            return False
    
    print("🎉 Все необходимые файлы успешно созданы!")
    return True


def restart_tor():
    """Перезапуск Tor внутри контейнера"""
    try:
        # Вариант 1: через systemctl (если Tor как служба)
        subprocess.run(['systemctl', 'restart', 'tor'], 
                      capture_output=True, timeout=10)
        
        # Вариант 2: через kill и запуск (если Tor запущен вручную)
        # subprocess.run(['pkill', 'tor'], capture_output=True)
        # time.sleep(2)
        # subprocess.Popen(['tor', '-f', '/etc/tor/torrc'], 
        #                 stdout=subprocess.DEVNULL, 
        #                 stderr=subprocess.DEVNULL)
        
        time.sleep(10)  # Ждём полного запуска
        logging.info("✅ Tor перезапущен")
        return True
        
    except Exception as e:
        logging.error(f"❌ Ошибка перезапуска Tor: {e}")
        return False



def renew_tor_ip(delay=5):
    """Смена IP через Tor"""
    global reconnections
    if reconnections > 5:
        logging.info("Перезапуская Tor")
        reconnections = 0
        return restart_tor()
    try:
        with Controller.from_port(port=9051) as controller:

            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            time.sleep(delay)
            logging.info("✅ IP изменён через Tor")
            reconnections += 1
            return True
    except Exception as e:
        logging.error(f"❌ Ошибка смены IP: {e}")
        return False


def get_current_ip(proxies):
    """Получить текущий IP через Tor"""
    try:
        response = requests.get('https://httpbin.org/ip', proxies=proxies, timeout=10)
        return response.json()['origin']
    except:
        return None


def read_emails(max_results=10, query=None):
    """
    Читать письма из Gmail

    Args:
        max_results: максимальное количество писем
        query: поисковый запрос (например, 'from:someone@gmail.com')
    """
    global service
    if service is None:
        service = get_gmail_service()

    # Получаем список писем
    results = service.users().messages().list(
        userId='me',
        maxResults=max_results,
        q=query
    ).execute()

    messages = results.get('messages', [])

    emails = []
    for msg in messages:
        # Получаем детали письма
        message = service.users().messages().get(
            userId='me',
            id=msg['id'],
            format='full'
        ).execute()

        # Извлекаем заголовки
        headers = message['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Без темы')
        from_email = next((h['value'] for h in headers if h['name'] == 'From'), 'Неизвестно')
        date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Неизвестно')

        emails.append({
            'id': msg['id'],
            'threadId': message['threadId'],
            'subject': subject,
            'from': from_email,
            'date': date,
            'snippet': message['snippet']
        })

    return emails

# Глобальный флаг
is_busy = False
busy_lock = threading.Lock()

def background_code_finder():
    """Вся логика поиска кода в фоне"""
    global is_busy
    
    try:
        
        # ВАШ КОД ИЗ find_new_code
        url = "https://hide-my-name.app/demo/success/"
        data = {"demo_mail": "fhsjarij@gmail.com"}
        all_codes = []
        
        proxies = {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050'
        }
        
        ip = get_current_ip(proxies)
        if ip is None:
            logging.info("!!! ip is None")
            return
        
        logging.info(f"ip: {ip}")
        need = 1
        delay = 5
        
        for i in range(need):
            ok = False
            while not ok:
                response1 = send_post_through_tor(data, url)
                text = response1.text
                ok = "выслан" in text
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
            
            logging.info(f"Код {i + 1} найден и отправлен на почту {data['demo_mail']}")
            logging.info("Попытка взять код:")
            
            for _ in range(3):
                code = get_code_from_last_email()
                if code in all_codes:
                    logging.info("Trying again")
                    time.sleep(5)
                    continue
                else:
                    logging.info(f"Найден код: {code}")
                    all_codes.append(code)
                    break
            else:
                logging.info("Не получилось извлечь код, пропустим шаг")
        
        logging.info("Закончено, все коды:")
        logging.info("\n".join(all_codes))
        
        global last_code
        last_code = all_codes[0] if all_codes else ""
        
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

def get_code_from_last_email():
    emails = read_emails(max_results=1, query='from:hidemy.name')
    last_email = emails[0]
    theme = last_email["subject"]
    code = theme[theme.find(":") + 1:].strip()
    return code


def send_post_through_tor(data, url):
    """Отправить POST-запрос через Tor"""
    proxies = {
        'http': 'socks5h://127.0.0.1:9050',
        'https': 'socks5h://127.0.0.1:9050'
    }

    # Отправляем запрос
    response = requests.post(url, data=data, proxies=proxies, timeout=10)
    return response


def get_gmail_service():
    """Получение сервиса Gmail API"""
    global service
    global creds
    creds = None
    
    # Убеждаемся, что credentials.json существует
    if not os.path.exists('credentials.json'):
        print("⚠️ credentials.json не найден, пробуем создать из переменных...")
        if not setup_credentials_from_env():
            raise Exception("Не удалось создать credentials.json")
    
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0, open_browser=False)
        
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    service = build('gmail', 'v1', credentials=creds)
    return service


@app.route("/get-last-code", methods=["GET"])
def get_last_code():
    global last_code
    return last_code


@app.route("/start-finding-new-code-old", methods=["GET"])
def find_new_code_old():
    url = "https://hide-my-name.app/demo/success/"
    data = {"demo_mail": "fhsjarij@gmail.com"}
    all_codes = []
    proxies = {
        'http': 'socks5h://127.0.0.1:9050',
        'https': 'socks5h://127.0.0.1:9050'
    }
    ip = get_current_ip(proxies)
    if ip is None:
        print("!!!", "ip is None")
        return ""
    print("ip:", ip)
    need = 1
    delay = 5
    for i in range(need):
        ok = False

        while not ok:

            response1 = send_post_through_tor(data, url)
            text = response1.text
            ok = "выслан" in text
            print("ok:", ok)

            while True:
                did = renew_tor_ip(delay=delay)
                if did:
                    new_ip = get_current_ip(proxies)
                    if new_ip == ip:
                        continue
                    else:
                        ip = new_ip
                        print(f"Новый IP: {ip}")
                        break
                print("Trying again to find new ip")

        print(f"Код {i + 1} найден и отправлен на почту {data['demo_mail']}")
        print("Попытка взять код:")

        for _ in range(3):
            code = get_code_from_last_email()
            if code in all_codes:
                print("Trying again")
                time.sleep(5)
                continue
            else:
                print("Найден код:", code)
                all_codes.append(code)
                break
        else:
            print("Не получилось извлечь код, пропустим шаг")

    print("Закончено, все коды:")
    print("\n".join(all_codes))
    global last_code
    last_code = all_codes[0]
    return ""


if __name__ == "__main__":
    # Инициализация
    creds = setup_credentials_from_env()

    # Запускаем Tor (он уже должен быть запущен отдельно)
    time.sleep(5)

    # Получаем сервис Gmail
    service = get_gmail_service()

    # Запускаем веб-сервер (Render ожидает, что сервис слушает порт)
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
