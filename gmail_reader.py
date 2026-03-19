import os
import pickle
import logging
import time
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

# Глобальные переменные для хранения состояния
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
last_emails = []
tor_ip = None
service = None

def setup_credentials_from_env():
    """Создаёт все необходимые файлы из переменных окружения"""
    # ... (тот же код, что и раньше) ...

def renew_tor_ip():
    """Смена IP через Tor"""
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            time.sleep(5)
            logging.info("✅ IP изменён через Tor")
            return True
    except Exception as e:
        logging.error(f"❌ Ошибка смены IP: {e}")
        return False

def get_tor_session():
    """Создание сессии через Tor"""
    session = requests.Session()
    session.proxies = {
        'http': 'socks5h://127.0.0.1:9050',
        'https': 'socks5h://127.0.0.1:9050'
    }
    return session

def check_tor_ip():
    """Проверка текущего IP через Tor"""
    global tor_ip
    try:
        session = get_tor_session()
        response = session.get('https://httpbin.org/ip', timeout=10)
        tor_ip = response.json()['origin']
        logging.info(f"🌐 Текущий IP через Tor: {tor_ip}")
        return tor_ip
    except Exception as e:
        logging.error(f"Не удалось проверить IP: {e}")
        return None

def get_gmail_service():
    """Получение сервиса Gmail API"""
    global service
    creds = None
    
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

def read_emails(max_results=5):
    """Чтение писем"""
    global service, last_emails
    try:
        if not service:
            service = get_gmail_service()
        
        results = service.users().messages().list(
            userId='me', 
            maxResults=max_results,
            q='is:unread'
        ).execute()
        
        messages = results.get('messages', [])
        
        if not messages:
            logging.info("📭 Нет новых писем")
            return []
        
        emails = []
        for msg in messages:
            message = service.users().messages().get(
                userId='me', 
                id=msg['id'],
                format='metadata',
                metadataHeaders=['From', 'Subject', 'Date']
            ).execute()
            
            headers = message['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Без темы')
            from_email = next((h['value'] for h in headers if h['name'] == 'From'), 'Неизвестно')
            date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Неизвестно')
            
            emails.append({
                'id': msg['id'],
                'subject': subject,
                'from': from_email,
                'date': date,
                'snippet': message['snippet']
            })
        
        last_emails = emails
        logging.info(f"📧 Прочитано {len(emails)} писем")
        return emails
    except Exception as e:
        logging.error(f"Ошибка чтения писем: {e}")
        return []

def background_task():
    """Фоновая задача для периодической проверки почты"""
    while True:
        try:
            read_emails(5)
            renew_tor_ip()  # Меняем IP после каждой проверки
            time.sleep(300)  # 5 минут
        except Exception as e:
            logging.error(f"Ошибка в фоновой задаче: {e}")
            time.sleep(60)

# API endpoints
@app.route('/')
def home():
    return jsonify({
        'status': 'running',
        'service': 'Gmail Tor Reader',
        'tor_ip': tor_ip,
        'endpoints': {
            '/emails': 'GET - получить последние письма',
            '/emails/unread': 'GET - получить непрочитанные письма',
            '/tor/status': 'GET - статус Tor',
            '/tor/renew': 'POST - сменить IP Tor',
            '/read': 'POST - принудительно прочитать почту'
        }
    })

@app.route('/emails', methods=['GET'])
def get_emails():
    """Получить последние прочитанные письма"""
    return jsonify({
        'count': len(last_emails),
        'emails': last_emails,
        'tor_ip': tor_ip
    })

@app.route('/emails/unread', methods=['GET'])
def get_unread():
    """Принудительно проверить непрочитанные письма"""
    emails = read_emails(5)
    return jsonify({
        'count': len(emails),
        'emails': emails,
        'tor_ip': tor_ip
    })

@app.route('/tor/status', methods=['GET'])
def tor_status():
    """Статус Tor"""
    return jsonify({
        'tor_ip': tor_ip,
        'tor_running': tor_ip is not None
    })

@app.route('/tor/renew', methods=['POST'])
def tor_renew():
    """Принудительно сменить IP Tor"""
    success = renew_tor_ip()
    check_tor_ip()
    return jsonify({
        'success': success,
        'new_ip': tor_ip
    })

@app.route('/read', methods=['POST'])
def read_now():
    """Принудительно прочитать почту"""
    emails = read_emails(5)
    return jsonify({
        'success': True,
        'count': len(emails),
        'emails': emails
    })

if __name__ == "__main__":
    # Инициализация
    setup_credentials_from_env()
    
    # Запускаем Tor (он уже должен быть запущен отдельно)
    time.sleep(5)
    check_tor_ip()
    
    # Получаем сервис Gmail
    service = get_gmail_service()
    
    # Запускаем фоновую задачу в отдельном потоке
    bg_thread = threading.Thread(target=background_task, daemon=True)
    bg_thread.start()
    
    # Запускаем веб-сервер (Render ожидает, что сервис слушает порт)
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
