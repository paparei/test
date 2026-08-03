import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot_service as bot_service_module
from bot_service import BotService
from database import Database


def make_config(database_path):
    return SimpleNamespace(
        database_path=database_path,
        ggsel_base_url="https://seller.ggsel.com/api_sellers/api",
        max_retries=3,
        retry_delay=1,
        telegram_bot_token="123:token",
        telegram_group_id=-1001,
    )


class SyncControlTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.addCleanup(self.loop.close)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = str(Path(self.temp_dir.name) / "state.db")

    def make_service(self):
        api = Mock()
        telegram = Mock()
        with patch.object(bot_service_module, "GGSelAPI", return_value=api), patch.object(
            bot_service_module, "TelegramBot", return_value=telegram
        ), patch.object(bot_service_module, "AutoResponder", return_value=Mock()):
            service = BotService(make_config(self.database_path))
        return service

    def test_pause_state_survives_restart(self):
        service = self.make_service()
        self.assertTrue(service.sync_enabled)

        result = self.loop.run_until_complete(service.pause_sync())

        self.assertIn("stopped", result)
        self.assertFalse(service.sync_enabled)
        self.assertEqual(
            "false", Database(self.database_path).get_setting("ggsel_sync_enabled")
        )
        self.assertFalse(self.make_service().sync_enabled)

    def test_start_is_idempotent_and_persistent(self):
        service = self.make_service()
        self.loop.run_until_complete(service.pause_sync())

        first = self.loop.run_until_complete(service.start_sync())
        second = self.loop.run_until_complete(service.start_sync())

        self.assertIn("started", first)
        self.assertIn("already running", second)
        self.assertTrue(service.sync_enabled)
        self.assertEqual(
            "true", Database(self.database_path).get_setting("ggsel_sync_enabled")
        )

    def test_paused_mode_blocks_customer_writes(self):
        service = self.make_service()
        self.loop.run_until_complete(service.pause_sync())

        sent = self.loop.run_until_complete(
            service._send_customer_message(123, "hello")
        )

        self.assertFalse(sent)
        service.ggsel_api.send_message.assert_not_called()

    def test_pause_waits_for_an_admitted_customer_write(self):
        service = self.make_service()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def scenario():
            async def admitted_write():
                async with service._customer_write_lock:
                    entered.set()
                    await release.wait()

            write_task = asyncio.create_task(admitted_write())
            await entered.wait()
            pause_task = asyncio.create_task(service.pause_sync())
            await asyncio.sleep(0)
            self.assertFalse(pause_task.done())
            release.set()
            await write_task
            self.assertIn("stopped", await pause_task)

        self.loop.run_until_complete(scenario())

    def test_long_sync_does_not_block_message_polling(self):
        service = self.make_service()
        service.running = True
        long_started = asyncio.Event()
        release_long = asyncio.Event()
        message_polled = asyncio.Event()

        async def scenario():
            async def long_topic_sync():
                long_started.set()
                await release_long.wait()

            async def message_poll():
                message_polled.set()

            topic_task = asyncio.create_task(
                service._run_sync_operation(long_topic_sync)
            )
            await long_started.wait()
            poll_task = asyncio.create_task(service._run_sync_operation(message_poll))

            await asyncio.wait_for(message_polled.wait(), timeout=0.2)
            self.assertFalse(topic_task.done())
            release_long.set()
            await asyncio.gather(topic_task, poll_task)

        self.loop.run_until_complete(scenario())

    def test_pause_waits_for_all_active_sync_operations(self):
        service = self.make_service()
        service.running = True
        entered = asyncio.Event()
        release = asyncio.Event()

        async def scenario():
            async def active_sync():
                entered.set()
                await release.wait()

            sync_task = asyncio.create_task(service._run_sync_operation(active_sync))
            await entered.wait()
            pause_task = asyncio.create_task(service.pause_sync())
            await asyncio.sleep(0)

            self.assertFalse(pause_task.done())
            release.set()
            await sync_task
            self.assertIn("stopped", await pause_task)

        self.loop.run_until_complete(scenario())

    def test_topic_sync_matches_string_invoice_ids_to_existing_topics(self):
        service = self.make_service()
        service.running = True
        service.topic_manager.topics["purchase_42"] = {"invoice_id": 42}
        service.ensure_ggsel_auth = AsyncMock(return_value=True)
        service.ggsel_api.get_last_sales.return_value = {
            "retval": 0,
            "sales": [{"invoice_id": "42"}],
        }

        self.loop.run_until_complete(service.sync_topics_with_purchases())

        service.ggsel_api.get_purchase_info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
