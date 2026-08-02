import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()
