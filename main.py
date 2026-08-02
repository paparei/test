import asyncio
import logging
from logging.handlers import RotatingFileHandler
import signal
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from bot_service import BotService
from auto_updater import check_and_update, get_current_version

# How often to check GitHub for updates (2 hours)
UPDATE_CHECK_INTERVAL = 7200

async def update_checker(auto_update_enabled: bool):
    """Update check loop"""
    if not auto_update_enabled:
        return
    while True:
        await asyncio.sleep(UPDATE_CHECK_INTERVAL)
        try:
            logging.info("🔄 Checking for updates...")
            needs_restart, message = await check_and_update(auto_update_enabled)
            logging.info(f"ℹ️ {message}")
            if needs_restart:
                logging.info("🔄 Restarting for update...")
                sys.exit(1)
        except Exception as e:
            logging.error(f"Update check error: {e}")

def setup_logging():
    """Configure stdout logging and optional rotating file logging.

    Docker should normally capture stdout. Rotating an individually bind-mounted
    file can fail with ``Device or resource busy`` when the handler renames it.
    """
    handlers = [logging.StreamHandler(sys.stdout)]

    if os.getenv('LOG_TO_FILE', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}:
        log_path = os.getenv(
            'LOG_FILE_PATH',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ggsel_bot.log')
        )
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    log_path,
                    maxBytes=5 * 1024 * 1024,
                    backupCount=3,
                    encoding='utf-8',
                )
            )
        except OSError as error:
            print(
                f"Warning: file logging is unavailable ({error}); using stdout only.",
                file=sys.stderr
            )

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers,
        force=True,
    )
    # Silence noisy libraries
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('telegram.ext').setLevel(logging.WARNING)
    # --- Add this line below to silence the GGSel connection drops ---
    logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)

async def main():
    setup_logging()
    try:
        config = Config.from_env()
        logging.info(f"🚀 GGSel Bot v{get_current_version()}")
        
        # Initial update check on boot
        if config.auto_update:
            needs_restart, message = await check_and_update(config.auto_update)
            if needs_restart:
                sys.exit(1)

        required_configs = {
            'GGSEL_SELLER_ID': config.ggsel_seller_id,
            'GGSEL_API_KEY': config.ggsel_api_key,
            'TELEGRAM_BOT_TOKEN': config.telegram_bot_token,
            'TELEGRAM_GROUP_ID': config.telegram_group_id,
        }
        missing_configs = [name for name, value in required_configs.items() if not value]

        if missing_configs:
            configured_env_file = os.getenv(
                'ENV_FILE',
                os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
            )
            logging.error(
                "Missing required environment variables: %s. Configure the container "
                "with Docker Compose 'env_file', or mount an env file and set "
                "ENV_FILE. Current env-file path: %s",
                ', '.join(missing_configs),
                configured_env_file,
            )
            sys.exit(1)

        bot_service = BotService(config)
        
        def signal_handler(signum, frame):
            logging.info("Shutting down safely...")
            bot_service.stop_sync()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        try:
            # Start background tasks
            asyncio.create_task(update_checker(config.auto_update))
            
            # Start main bot service
            await bot_service.start()
        except KeyboardInterrupt:
            pass
        finally:
            await bot_service.stop()
            
    except Exception as e:
        logging.error(f"Critical error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
