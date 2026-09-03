import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import Config
from database import Database
from repricing import (
    calculate_repricing_rub,
    current_rub_price,
    parse_repricing_settings,
    parse_repricing_target,
    repricing_settings_json,
)


class RepricingCalculationTests(unittest.TestCase):
    def test_calculation_ceil_and_safety_guards(self):
        self.assertEqual(926, calculate_repricing_rub(10, 90, 2.5, 2))
        self.assertEqual(901, calculate_repricing_rub(10, 90.01))

        for values in (
            (0, 90, 0, 0),
            (10, 0, 0, 0),
            (10, 301, 0, 0),
            (10, 90, 100, 0),
            (10, 90, 0, -1),
            (float("nan"), 90, 0, 0),
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                calculate_repricing_rub(*values)

    def test_menu_settings_round_trip_through_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.db"))
            database.set_setting(
                "repricing_dry_run",
                repricing_settings_json(True, {123456: 10.5}),
            )
            self.assertEqual(
                (True, {123456: 10.5}),
                parse_repricing_settings(database.get_setting("repricing_dry_run")),
            )

    def test_telegram_target_input_is_validated(self):
        self.assertEqual((123456, 10.5), parse_repricing_target("123456 10.50"))
        for value in ("", "123", "abc 10", "123 nan", "-1 10", "123 0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_repricing_target(value)

    def test_live_configuration_requires_telegram_approval_gate_and_key(self):
        with patch.dict(
            os.environ,
            {
                "REPRICING_LIVE_ENABLED": "true",
                "REPRICING_TARGETS_USD": '{"123456":10}',
                "GGSEL_V2_PRICE_WRITE_ENABLED": "true",
                "GGSEL_V2_API_KEY": "test-v2-key",
            },
            clear=True,
        ), self.assertRaises(ValueError):
            Config.from_env()

        with patch.dict(
            os.environ,
            {"GGSEL_V2_PRICE_WRITE_ENABLED": "true"},
            clear=True,
        ), self.assertRaises(ValueError):
            Config.from_env()

        with patch.dict(
            os.environ,
            {
                "GGSEL_V2_PRICE_WRITE_ENABLED": "true",
                "GGSEL_V2_API_KEY": "test-v2-key",
            },
            clear=True,
        ):
            config = Config.from_env()
            self.assertTrue(config.ggsel_v2_price_write_enabled)
            self.assertFalse(config.repricing_live_enabled)

    def test_current_rub_price_accepts_only_valid_documented_shapes(self):
        self.assertEqual(900, current_rub_price({"currency": "RUB", "price": "900"}))
        self.assertEqual(
            901,
            current_rub_price({"prices": {"default": {"RUB": 901}}}),
        )
        self.assertIsNone(current_rub_price({"currency": "USD", "price": 10}))
        self.assertIsNone(current_rub_price({"currency": "RUB", "price": "nan"}))


if __name__ == "__main__":
    unittest.main()
