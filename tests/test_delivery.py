import asyncio
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


telegram_stub = types.ModuleType("telegram")


class InlineKeyboardMarkup:
    @classmethod
    def de_json(cls, data, bot):
        return data


telegram_stub.Update = object
telegram_stub.InlineKeyboardButton = object
telegram_stub.InlineKeyboardMarkup = InlineKeyboardMarkup
sys.modules["telegram"] = telegram_stub

ggsel_stub = types.ModuleType("ggsel_api")
ggsel_stub.GGSelAPI = object
sys.modules["ggsel_api"] = ggsel_stub

telegram_bot_stub = types.ModuleType("telegram_bot")
telegram_bot_stub.TelegramBot = object
sys.modules["telegram_bot"] = telegram_bot_stub

from bot_service import BotService
from database import Database, Message
from message_manager import MessageManager


class FakeTelegramBot:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bot = object()

    async def send_message(self, text, topic_id, parse_mode=None, reply_markup=None):
        return self.responses.pop(0)


class DurableDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(str(Path(self.temp_dir.name) / "bot.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_service(self, responses):
        service = BotService.__new__(BotService)
        service.database = self.database
        service.message_manager = MessageManager(self.database)
        service.telegram_bot = FakeTelegramBot(responses)
        service.config = SimpleNamespace(retry_delay=1)
        service.message_flood_control_until = None
        return service

    def test_retryable_send_survives_until_later_success(self):
        message = Message(123, "message-1", "hello", datetime.now())
        self.assertTrue(self.database.save_message(message))
        service = self.make_service([(False, 2)])

        accepted = asyncio.run(
            service.send_message_with_cooldown(
                "hello",
                topic_id=10,
                chat_id=123,
                message_id="message-1",
            )
        )
        self.assertTrue(accepted)
        self.assertFalse(self.database.is_message_sent("message-1"))

        with self.database._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM telegram_outbox").fetchone()[0]
            connection.execute("UPDATE telegram_outbox SET next_attempt_at = 0")
        self.assertEqual(1, count)

        service.message_flood_control_until = None
        service.telegram_bot.responses.append((True, None))
        asyncio.run(service.process_pending_messages())

        self.assertTrue(self.database.is_message_sent("message-1"))
        with self.database._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM telegram_outbox").fetchone()[0]
        self.assertEqual(0, count)

    def test_permanent_send_failure_does_not_poison_queue(self):
        service = self.make_service([(False, None)])

        accepted = asyncio.run(service.send_message_with_cooldown("bad payload", topic_id=10))

        self.assertFalse(accepted)
        with self.database._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM telegram_outbox").fetchone()[0]
        self.assertEqual(0, count)

    def test_message_polling_fetches_only_unread_chat_topics(self):
        service = BotService.__new__(BotService)
        api_calls = []

        class API:
            def get_chats(self, **kwargs):
                api_calls.append(kwargs)
                return {"items": [{"id_i": 101}, {"id_i": 202}]}

        topics = {
            101: {"invoice_id": 101, "topic_id": 11},
        }

        class Topics:
            def get_topic_by_invoice(self, invoice_id):
                return topics.get(invoice_id)

        captured = []

        async def capture(selected_topics):
            captured.append(selected_topics)

        service.ggsel_api = API()
        service.topic_manager = Topics()
        service.check_topics_parallel = capture

        asyncio.run(service.check_new_message_chats())

        self.assertEqual(1, len(api_calls))
        self.assertEqual(1, api_calls[0]["filter_new"])
        self.assertEqual(["purchase_101"], list(captured[0]))

    def test_review_is_checkpointed_only_after_delivery_is_accepted(self):
        service = BotService.__new__(BotService)
        service.processed_reviews = {}
        service.autoresponder = SimpleNamespace(get_review_response=lambda review_type: None)
        saved = []
        service._save_processed_review_db = lambda review_id, value: saved.append((review_id, value))

        async def rejected(*args, **kwargs):
            return False

        review = {"id": 7, "invoice_id": 101, "type": "good", "info": "great"}
        topics = {101: {"topic_id": 11}}
        service.send_message_with_cooldown = rejected

        asyncio.run(service._process_reviews([review], topics, None))
        self.assertEqual([], saved)

        async def accepted(*args, **kwargs):
            return True

        service.send_message_with_cooldown = accepted
        asyncio.run(service._process_reviews([review], topics, None))
        self.assertEqual([("7", "good:great")], saved)


if __name__ == "__main__":
    unittest.main()
