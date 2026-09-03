import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict

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

    # GGSel transport settings. Kept after existing fields for positional compatibility.
    ggsel_connect_timeout: float = 5.0
    ggsel_read_timeout: float = 30.0
    chat_sweep_batch_size: int = 25

    # Price control; target IDs also form the explicit V2 write allowlist.
    repricing_dry_run: bool = False
    repricing_live_enabled: bool = False
    repricing_targets_usd: Dict[int, float] = field(default_factory=dict)
    repricing_fee_percent: float = 0.0
    repricing_fixed_rub: float = 0.0
    repricing_max_change_percent: float = 15.0

    # Public V2 credentials and disabled-by-default financial-write gate.
    ggsel_v2_api_key: str = ""
    ggsel_v2_price_write_enabled: bool = False

    @staticmethod
    def _env_float(name: str, default: str, minimum: float, maximum: float) -> float:
        try:
            value = float(os.getenv(name, default))
        except ValueError as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return value

    @staticmethod
    def _repricing_targets(value: str) -> Dict[int, float]:
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("REPRICING_TARGETS_USD must be a JSON object") from exc
        if not isinstance(raw, dict):
            raise ValueError("REPRICING_TARGETS_USD must be a JSON object")

        targets: Dict[int, float] = {}
        for product_id, target_usd in raw.items():
            try:
                parsed_id = int(product_id)
                parsed_target = float(target_usd)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "REPRICING_TARGETS_USD must map product IDs to USD numbers"
                ) from exc
            if parsed_id <= 0 or not math.isfinite(parsed_target) or parsed_target <= 0:
                raise ValueError(
                    "REPRICING_TARGETS_USD requires positive product IDs and USD amounts"
                )
            targets[parsed_id] = parsed_target
        return targets

    @classmethod
    def from_env(cls) -> 'Config':
        dry_run = os.getenv('REPRICING_DRY_RUN', 'false').lower() in ('true', '1', 'yes')
        live_enabled = os.getenv(
            'REPRICING_LIVE_ENABLED', 'false'
        ).lower() in ('true', '1', 'yes')
        price_write_enabled = os.getenv(
            'GGSEL_V2_PRICE_WRITE_ENABLED', 'false'
        ).lower() in ('true', '1', 'yes')
        targets = cls._repricing_targets(os.getenv('REPRICING_TARGETS_USD', '{}'))
        v2_api_key = os.getenv('GGSEL_V2_API_KEY', '')
        if live_enabled:
            raise ValueError(
                "REPRICING_LIVE_ENABLED must remain false; enable live repricing "
                "through Telegram confirmation"
            )
        if dry_run and not targets:
            raise ValueError(
                "REPRICING_TARGETS_USD must contain at least one product when repricing is enabled"
            )
        if price_write_enabled and not v2_api_key.strip():
            raise ValueError(
                "GGSEL_V2_PRICE_WRITE_ENABLED requires GGSEL_V2_API_KEY"
            )

        return cls(
            ggsel_seller_id=int(os.getenv('GGSEL_SELLER_ID', '0')),
            ggsel_api_key=os.getenv('GGSEL_API_KEY', ''),
            ggsel_v2_api_key=v2_api_key,
            ggsel_v2_price_write_enabled=price_write_enabled,
            telegram_bot_token=os.getenv('TELEGRAM_BOT_TOKEN', ''),
            telegram_group_id=int(os.getenv('TELEGRAM_GROUP_ID', '0')),
            database_path=os.getenv('DATABASE_PATH', 'ggsel_bot.db'),
            poll_interval=int(os.getenv('POLL_INTERVAL', '15')),
            chat_check_interval=int(os.getenv('CHAT_CHECK_INTERVAL', '40')),
            review_check_interval=int(os.getenv('REVIEW_CHECK_INTERVAL', '120')),
            topic_sync_interval=int(os.getenv('TOPIC_SYNC_INTERVAL', '3600')),
            chat_sweep_batch_size=int(os.getenv('CHAT_SWEEP_BATCH_SIZE', '25')),
            telegram_timeout=int(os.getenv('TELEGRAM_TIMEOUT', '30')),
            max_retries=int(os.getenv('MAX_RETRIES', '3')),
            retry_delay=int(os.getenv('RETRY_DELAY', '5')),
            ggsel_connect_timeout=float(os.getenv('GGSEL_CONNECT_TIMEOUT', '5')),
            ggsel_read_timeout=float(os.getenv('GGSEL_READ_TIMEOUT', '30')),
            auto_update=os.getenv('AUTO_UPDATE', 'false').lower() in ('true', '1', 'yes'),
            repricing_dry_run=dry_run,
            repricing_live_enabled=live_enabled,
            repricing_targets_usd=targets,
            repricing_fee_percent=cls._env_float(
                'REPRICING_FEE_PERCENT', '0', 0, 50
            ),
            repricing_fixed_rub=cls._env_float(
                'REPRICING_FIXED_RUB', '0', 0, 1000000
            ),
            repricing_max_change_percent=cls._env_float(
                'REPRICING_MAX_CHANGE_PERCENT', '15', 0.01, 100
            ),
        )
