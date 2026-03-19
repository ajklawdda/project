import os
import pickle
import time
import logging
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from stem import Signal
from stem.control import Controller
import requests

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

import os
import base64
import pickle

# Создаём token.pickle из переменной окружения при запуске
def setup_token_from_env():
    """Создаёт token.pickle из переменной окружения TOKEN_PICKLE_B64"""
    token_b64 = os.environ.get('TOKEN_PICKLE_B64')
    if token_b64:
        try:
            # Декодируем из base64 в бинарные данные
            token_bytes = base64.b64decode(token_b64)
            
            # Проверяем, что это действительно pickle-файл
            try:
                creds = pickle.loads(token_bytes)
                print(f"✅ Токен успешно загружен из переменной окружения")
                print(f"  Срок действия: {creds.expiry}")
            except Exception as e:
                print(f"⚠️ Предупреждение: полученные данные не являются валидным pickle: {e}")
            
            # Сохраняем в файл для совместимости со старым кодом
            print("✅ token.pickle создан из переменной окружения")
            return creds
        except Exception as e:
            print(f"❌ Ошибка при создании token.pickle из переменной окружения: {e}")
            return False
    else:
        print("⚠️ Переменная TOKEN_PICKLE_B64 не найдена")
        return False

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
    try:
        session = get_tor_session()
        response = session.get('https://httpbin.org/ip', timeout=10)
        ip = response.json()['origin']
        logging.info(f"🌐 Текущий IP через Tor: {ip}")
        return ip
    except Exception as e:
        logging.error(f"Не удалось проверить IP: {e}")
        return None

def get_gmail_service():
    """Получение сервиса Gmail API"""
    creds = setup_token_from_env()
    
    # В продакшене нужно хранить токен в безопасном месте
    # На Render можно использовать volume или переменные окружения
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # На Render нужно использовать headless авторизацию
            # Это сложнее, поэтому пока используем готовый токен
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0, open_browser=False)
        
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    return build('gmail', 'v1', credentials=creds)

def read_emails(service, max_results=5):
    """Чтение писем"""
    try:
        results = service.users().messages().list(
            userId='me', 
            maxResults=max_results,
            q='is:unread'  # Только непрочитанные
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
            
            logging.info(f"📧 Письмо от {from_email}: {subject}")
        
        return emails
    except Exception as e:
        logging.error(f"Ошибка чтения писем: {e}")
        return []

def main():
    logging.info("🚀 Запуск Gmail Tor Reader")
    
    # Проверяем Tor
    check_tor_ip()
    
    # Получаем сервис Gmail
    service = get_gmail_service()
    
    # Читаем письма
    emails = read_emails(service, max_results=5)
    
    # Можно сменить IP перед следующим чтением
    if emails:
        logging.info(f"✅ Прочитано {len(emails)} писем")
        renew_tor_ip()
    else:
        logging.info("📭 Писем нет")
    
    # Бесконечный цикл для постоянной работы
    while True:
        logging.info("⏳ Ожидание следующей проверки...")
        time.sleep(300)  # Проверка каждые 5 минут
        
        # Смена IP перед новым циклом
        renew_tor_ip()
        
        # Новая проверка
        emails = read_emails(service, max_results=5)
        if emails:
            logging.info(f"✅ Прочитано {len(emails)} новых писем")

if __name__ == "__main__":
    main()
