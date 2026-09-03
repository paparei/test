import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


class InlineKeyboardButton:
    def __init__(self, text, callback_data=None):
        self.text = text
        self.callback_data = callback_data


if "telegram" not in sys.modules:
    telegram_stub = types.ModuleType("telegram")
    telegram_stub.Update = object
    telegram_stub.InlineKeyboardButton = InlineKeyboardButton
    telegram_stub.InlineKeyboardMarkup = object
    sys.modules["telegram"] = telegram_stub
if "ggsel_api" not in sys.modules:
    ggsel_stub = types.ModuleType("ggsel_api")
    ggsel_stub.GGSelAPI = object
    sys.modules["ggsel_api"] = ggsel_stub
if "telegram_bot" not in sys.modules:
    telegram_bot_stub = types.ModuleType("telegram_bot")
    telegram_bot_stub.TelegramBot = object
    sys.modules["telegram_bot"] = telegram_bot_stub

import bot_service as bot_service_module
bot_service_module.InlineKeyboardButton = InlineKeyboardButton
from bot_service import BotService
from database import Database


def make_config(database_path):
    return SimpleNamespace(
        database_path=database_path,
        ggsel_base_url="https://seller.ggsel.com/api_sellers/api",
        ggsel_v2_api_key="test-v2-key",
        ggsel_v2_price_write_enabled=False,
        repricing_dry_run=False,
        repricing_live_enabled=False,
        repricing_targets_usd={},
        repricing_fee_percent=0.0,
        repricing_fixed_rub=0.0,
        repricing_max_change_percent=15.0,
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

    def test_repricing_picker_lists_offers_with_pagination(self):
        service = self.make_service()
        service.config.repricing_targets_usd = {102615259: 10.0}
        service.telegram_bot.edit_message = AsyncMock()
        service.ggsel_api.list_v2_offers.return_value = {
            "offers": [
                {"id": 102615259, "title_en": "Configured product"},
                {"id": 102615260, "title_en": "Another product"},
            ],
            "page": 2,
            "has_previous_page": True,
            "has_next_page": True,
        }

        self.loop.run_until_complete(
            service.show_repricing_offer_picker(-1001, 77, 2)
        )

        service.ggsel_api.list_v2_offers.assert_called_once_with(2, 8)
        args = service.telegram_bot.edit_message.await_args.args
        callbacks = [button.callback_data for row in args[3] for button in row]
        labels = [button.text for row in args[3] for button in row]
        self.assertIn("repricing_select_102615259", callbacks)
        self.assertIn("repricing_select_102615260", callbacks)
        self.assertIn("repricing_browse_1", callbacks)
        self.assertIn("repricing_browse_3", callbacks)
        self.assertTrue(any(label.startswith("✅ 102615259") for label in labels))

    def test_repricing_picker_selection_adds_target_and_revokes_live_approval(self):
        service = self.make_service()
        service.config.repricing_dry_run = False
        service.config.repricing_live_enabled = True
        service.config.repricing_targets_usd = {102615259: 10.0}
        service.telegram_bot.edit_message = AsyncMock()
        service.telegram_bot.send_message_with_keyboard = AsyncMock()
        service.ggsel_api.get_v2_offer.return_value = {
            "id": 102615260,
            "title_en": "Another product",
            "price": 166,
            "currency": "RUB",
        }
        update = SimpleNamespace(
            callback_query=SimpleNamespace(
                message=SimpleNamespace(
                    chat=SimpleNamespace(id=-1001), message_id=77
                )
            )
        )

        self.loop.run_until_complete(
            service.handle_callback(
                "repricing_select_102615260", update, SimpleNamespace()
            )
        )
        handled = self.loop.run_until_complete(
            service.handle_text_input(-1001, "12.50")
        )

        self.assertTrue(handled)
        self.assertEqual(
            {102615259: 10.0, 102615260: 12.5},
            service.config.repricing_targets_usd,
        )
        self.assertFalse(service.config.repricing_live_enabled)
        self.assertEqual(
            "false",
            Database(self.database_path).get_setting("repricing_live_enabled"),
        )
        service.ggsel_api.update_v2_offer_price.assert_not_called()

    def test_live_repricing_requires_confirmation_and_server_gate(self):
        service = self.make_service()
        service.config.repricing_targets_usd = {102615259: 10.0}
        service.telegram_bot.edit_message = AsyncMock()
        update = SimpleNamespace(
            callback_query=SimpleNamespace(
                message=SimpleNamespace(
                    chat=SimpleNamespace(id=-1001), message_id=77
                )
            )
        )

        self.loop.run_until_complete(
            service.handle_callback(
                "repricing_live_toggle", update, SimpleNamespace()
            )
        )

        self.assertFalse(service.config.repricing_live_enabled)
        keyboard = service.telegram_bot.edit_message.await_args.args[3]
        self.assertEqual("repricing_live_confirm", keyboard[0][0].callback_data)

        self.loop.run_until_complete(
            service.handle_callback(
                "repricing_live_confirm", update, SimpleNamespace()
            )
        )
        self.assertFalse(service.config.repricing_live_enabled)
        service.ggsel_api.update_v2_offer_price.assert_not_called()

    def test_live_repricing_writes_verified_batch_only_once_per_day(self):
        service = self.make_service()
        service.config.ggsel_v2_price_write_enabled = True
        service.config.repricing_live_enabled = True
        service.config.repricing_targets_usd = {
            102615259: 10.0,
            102615260: 12.5,
        }
        service.ggsel_api.update_v2_offer_price.side_effect = [
            None,
            {"id": 102615260, "price": 1125, "currency": "RUB"},
        ]
        rate_at = bot_service_module.datetime.now(
            bot_service_module.timezone.utc
        )

        self.loop.run_until_complete(
            service._run_repricing_live(90.0, rate_at)
        )
        self.loop.run_until_complete(
            service._run_repricing_live(90.0, rate_at)
        )

        self.assertEqual(
            [
                unittest.mock.call(102615259, 900),
                unittest.mock.call(102615260, 1125),
            ],
            service.ggsel_api.update_v2_offer_price.call_args_list,
        )
        self.assertIsNotNone(
            Database(self.database_path).get_setting(
                "repricing_live_last_run_at"
            )
        )

    def test_live_repricing_write_gate_blocks_before_daily_checkpoint(self):
        service = self.make_service()
        service.config.repricing_live_enabled = True
        service.config.repricing_targets_usd = {102615259: 10.0}
        rate_at = bot_service_module.datetime.now(
            bot_service_module.timezone.utc
        )

        self.loop.run_until_complete(
            service._run_repricing_live(90.0, rate_at)
        )

        service.ggsel_api.update_v2_offer_price.assert_not_called()
        self.assertIsNone(
            Database(self.database_path).get_setting(
                "repricing_live_last_run_at"
            )
        )

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
