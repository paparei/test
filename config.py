import os
from dataclasses import dataclass

# Попытка загрузить .env файл только из папки ggsel_bot
try:
    from dotenv import load_dotenv
    
    # Use a mounted env file when ENV_FILE is set; otherwise use local .env.
    current_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.abspath(os.getenv('ENV_FILE', os.path.join(current_dir, '.env')))
    
    load_dotenv(env_path)
        
except ImportError:
    # Если python-dotenv не установлен, продолжаем без него
    pass

@dataclass
class Config:
    # GGSel API (обязательные поля)
    ggsel_seller_id: int
    ggsel_api_key: str
    telegram_bot_token: str
    telegram_group_id: int
    
    # Опциональные поля с значениями по умолчанию
    ggsel_base_url: str = "https://seller.ggsel.com/api_sellers/api"
    database_path: str = "ggsel_bot.db"
    poll_interval: int = 15  # секунды для проверки сообщений
    chat_check_interval: int = 40  # секунды для проверки новых чатов
    review_check_interval: int = 120
    topic_sync_interval: int = 3600
    
    # Настройки таймаутов и повторных попыток
    telegram_timeout: int = 30  # таймаут для Telegram API
    max_retries: int = 3  # максимальное количество повторных попыток
    retry_delay: int = 5  # задержка между попытками в секундах
    
    # Автообновление
    auto_update: bool = False  # opt in after validating an update in production
    
    @classmethod
    def from_env(cls) -> 'Config':
        return cls(
            ggsel_seller_id=int(os.getenv('GGSEL_SELLER_ID', '0')),
            ggsel_api_key=os.getenv('GGSEL_API_KEY', ''),
            telegram_bot_token=os.getenv('TELEGRAM_BOT_TOKEN', ''),
            telegram_group_id=int(os.getenv('TELEGRAM_GROUP_ID', '0')),
            database_path=os.getenv('DATABASE_PATH', 'ggsel_bot.db'),
            poll_interval=int(os.getenv('POLL_INTERVAL', '15')),
            chat_check_interval=int(os.getenv('CHAT_CHECK_INTERVAL', '40')),
            review_check_interval=int(os.getenv('REVIEW_CHECK_INTERVAL', '120')),
            topic_sync_interval=int(os.getenv('TOPIC_SYNC_INTERVAL', '3600')),
            telegram_timeout=int(os.getenv('TELEGRAM_TIMEOUT', '30')),
            max_retries=int(os.getenv('MAX_RETRIES', '3')),
            retry_delay=int(os.getenv('RETRY_DELAY', '5')),
            auto_update=os.getenv('AUTO_UPDATE', 'false').lower() in ('true', '1', 'yes')
        )
