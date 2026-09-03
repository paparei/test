import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


def install_http_stubs():
    requests = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    class Timeout(RequestException):
        pass

    class ReadTimeout(Timeout):
        pass

    class ConnectTimeout(Timeout):
        pass

    class ConnectionError(RequestException):
        pass

    class Session:
        def __init__(self):
            self.headers = {}
            self.adapters = {}

        def mount(self, prefix, adapter):
            self.adapters[prefix] = adapter

        def get_adapter(self, url):
            return self.adapters["https://"]

        def request(self, *args, **kwargs):
            raise NotImplementedError

        def close(self):
            pass

    requests.Session = Session
    requests.Response = object
    requests.RequestException = RequestException
    requests.Timeout = Timeout
    requests.ReadTimeout = ReadTimeout
    requests.ConnectTimeout = ConnectTimeout
    requests.ConnectionError = ConnectionError
    requests.exceptions = SimpleNamespace(
        RequestException=RequestException,
        Timeout=Timeout,
        ReadTimeout=ReadTimeout,
        ConnectTimeout=ConnectTimeout,
        ConnectionError=ConnectionError,
    )

    adapters = types.ModuleType("requests.adapters")

    class HTTPAdapter:
        def __init__(self, **kwargs):
            self.max_retries = kwargs.get("max_retries")

    adapters.HTTPAdapter = HTTPAdapter
    requests.adapters = adapters

    retry_module = types.ModuleType("urllib3.util.retry")

    class Retry:
        def __init__(self, **kwargs):
            self.total = kwargs.get("total")
            self.connect = kwargs.get("connect")
            self.read = kwargs.get("read")
            self.status = kwargs.get("status")
            self.allowed_methods = kwargs.get("allowed_methods", frozenset())

    retry_module.Retry = Retry
    urllib3 = types.ModuleType("urllib3")
    urllib3_util = types.ModuleType("urllib3.util")
    urllib3_util.retry = retry_module
    urllib3.util = urllib3_util

    sys.modules["requests"] = requests
    sys.modules["requests.adapters"] = adapters
    sys.modules["urllib3"] = urllib3
    sys.modules["urllib3.util"] = urllib3_util
    sys.modules["urllib3.util.retry"] = retry_module
    return requests


requests = install_http_stubs()
module_path = Path(__file__).resolve().parents[1] / "ggsel_api.py"
spec = importlib.util.spec_from_file_location("ggsel_api_hardened_under_test", module_path)
ggsel_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ggsel_api)


def make_config(**overrides):
    values = {
        "ggsel_seller_id": 1,
        "ggsel_api_key": "secret-key",
        "ggsel_v2_api_key": "secret-v2-key",
        "ggsel_v2_price_write_enabled": False,
        "ggsel_base_url": "https://seller.ggsel.com/api_sellers/api",
        "ggsel_connect_timeout": 2,
        "ggsel_read_timeout": 9,
        "max_retries": 3,
        "retry_delay": 1,
        "repricing_targets_usd": {102615259: 10.0},
        "repricing_max_change_percent": 15.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class GGSelAPIHardeningTests(unittest.TestCase):
    def test_base_url_is_normalized_and_unsafe_origins_are_rejected(self):
        api = ggsel_api.GGSelAPI(
            make_config(ggsel_base_url="https://EXAMPLE.com/custom/")
        )
        self.assertEqual("https://example.com/custom", api.base_url)

        for origin in (
            "http://seller.example/api",
            "https://user:password@seller.example/api",
            "https://seller.example/api?token=leak",
            "//seller.example/api",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                ggsel_api.GGSelAPI(make_config(ggsel_base_url=origin))

    def test_tokens_are_parameters_and_every_request_has_a_timeout(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.token = "secret-token"
        api.session.request = Mock(
            return_value=Response(payload={"retval": 0, "content": {}})
        )

        self.assertIsNotNone(api.get_purchase_info(42))

        _, url = api.session.request.call_args.args
        kwargs = api.session.request.call_args.kwargs
        self.assertNotIn("secret-token", url)
        self.assertEqual("secret-token", kwargs["params"]["token"])
        self.assertEqual((2.0, 9.0), kwargs["timeout"])
        self.assertEqual("ru", kwargs["headers"]["locale"])

    def test_slow_reads_do_not_pin_polling_workers_with_retries(self):
        api = ggsel_api.GGSelAPI(make_config())
        retry = api.session.get_adapter("https://").max_retries
        self.assertNotIn("POST", retry.allowed_methods)
        self.assertEqual(0, retry.read)

    def test_invalid_messages_do_not_cross_the_http_boundary(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.token = "opaque"
        api.session.request = Mock()

        self.assertFalse(api.send_message(7, "\x00\x01"))
        self.assertFalse(api.send_message(-1, "hello"))
        api.session.request.assert_not_called()

    def test_ambiguous_message_read_timeout_is_not_duplicated(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.token = "opaque"
        api.session.request = Mock(side_effect=requests.ReadTimeout())

        self.assertTrue(api.send_message(7, "hello"))

    def test_transport_failures_are_classified_as_retryable(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.token = "opaque"
        api.session.request = Mock(side_effect=requests.ConnectTimeout())

        self.assertIsNone(api.get_last_sales())
        self.assertEqual(ggsel_api.APIFailure.RETRYABLE, api.last_failure)

    def test_product_lookup_preserves_feature_without_token_in_url(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.token = "secret-token"
        api.session.request = Mock(
            return_value=Response(payload={"product": {"name": "Example"}})
        )

        self.assertEqual("Example", api.get_real_product_name(99))
        self.assertEqual("Example", api.get_real_product_name(99))

        api.session.request.assert_called_once()
        _, url = api.session.request.call_args.args
        self.assertNotIn("secret-token", url)
        self.assertEqual(
            "secret-token", api.session.request.call_args.kwargs["params"]["token"]
        )

    def test_v2_offer_list_is_read_only_validated_and_paginated(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.session.request = Mock(
            return_value=Response(
                payload={
                    "data": [
                        {
                            "id": 102615259,
                            "title_en": "Example offer",
                            "price": 166,
                            "currency": "RUB",
                        }
                    ],
                    "pagination": {
                        "has_next_page": True,
                        "has_previous_page": True,
                    },
                }
            )
        )

        result = api.list_v2_offers(page=2, limit=8)

        self.assertEqual(102615259, result["offers"][0]["id"])
        self.assertTrue(result["has_next_page"])
        self.assertTrue(result["has_previous_page"])
        method, url = api.session.request.call_args.args
        kwargs = api.session.request.call_args.kwargs
        self.assertEqual("GET", method)
        self.assertEqual("https://seller.ggsel.com/api_sellers/v2/offers", url)
        self.assertNotIn("secret-v2-key", url)
        self.assertEqual({"page": 2, "limit": 8}, kwargs["params"])
        self.assertEqual("secret-v2-key", kwargs["headers"]["Authorization"])
        self.assertNotIn("json", kwargs)

    def test_v2_offer_list_rejects_invalid_pagination_before_http(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.session.request = Mock()

        for page, limit in ((0, 8), (True, 8), (1, 0), (1, 51)):
            with self.subTest(page=page, limit=limit):
                self.assertIsNone(api.list_v2_offers(page, limit))
        api.session.request.assert_not_called()

    def test_v2_offer_diagnostic_is_read_only_and_keeps_key_out_of_url(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.session.request = Mock(
            return_value=Response(
                payload={
                    "data": {
                        "id": 102615259,
                        "status": "active",
                        "price": 166,
                        "currency": "RUB",
                    }
                }
            )
        )

        offer = api.get_v2_offer(102615259)

        self.assertEqual(166, offer["price"])
        method, url = api.session.request.call_args.args
        kwargs = api.session.request.call_args.kwargs
        self.assertEqual("GET", method)
        self.assertEqual(
            "https://seller.ggsel.com/api_sellers/v2/offers/102615259", url
        )
        self.assertNotIn("secret-v2-key", url)
        self.assertEqual("secret-v2-key", kwargs["headers"]["Authorization"])
        self.assertNotIn("json", kwargs)

    def test_v2_price_write_rejects_disabled_invalid_and_unlisted_requests(self):
        cases = (
            (make_config(), 102615259, 110),
            (make_config(ggsel_v2_price_write_enabled=True), 0, 110),
            (make_config(ggsel_v2_price_write_enabled=True), 1.5, 110),
            (make_config(ggsel_v2_price_write_enabled=True), 102615259, True),
            (make_config(ggsel_v2_price_write_enabled=True), 102615259, 0),
            (make_config(ggsel_v2_price_write_enabled=True), 102615259, float("nan")),
            (
                make_config(
                    ggsel_v2_price_write_enabled=True,
                    repricing_targets_usd={102615259: 10.0},
                ),
                999,
                110,
            ),
        )
        for config, offer_id, price in cases:
            with self.subTest(offer_id=offer_id, price=price):
                api = ggsel_api.GGSelAPI(config)
                api.session.request = Mock()

                self.assertIsNone(api.update_v2_offer_price(offer_id, price))
                api.session.request.assert_not_called()

    def test_v2_price_write_blocks_change_above_configured_limit(self):
        api = ggsel_api.GGSelAPI(
            make_config(ggsel_v2_price_write_enabled=True)
        )
        api.session.request = Mock(
            return_value=Response(
                payload={
                    "data": {
                        "id": 102615259,
                        "price": 100,
                        "currency": "RUB",
                    }
                }
            )
        )

        self.assertIsNone(api.update_v2_offer_price(102615259, 116))
        api.session.request.assert_called_once()
        self.assertEqual("GET", api.session.request.call_args.args[0])

    def test_v2_price_write_patches_once_then_returns_verified_read_back(self):
        api = ggsel_api.GGSelAPI(
            make_config(ggsel_v2_price_write_enabled=True)
        )
        api.session.request = Mock(
            side_effect=(
                Response(
                    payload={
                        "data": {
                            "id": 102615259,
                            "price": 100,
                            "currency": "RUB",
                        }
                    }
                ),
                Response(
                    payload={
                        "data": {
                            "id": 102615259,
                            "price": 110,
                            "currency": "RUB",
                        }
                    }
                ),
            )
        )
        api._v2_write_session.request = Mock(
            return_value=Response(
                payload={"data": {"id": 102615259, "price": 110}}
            )
        )

        offer = api.update_v2_offer_price(102615259, 110)

        self.assertEqual(110, offer["price"])
        self.assertEqual(
            ["GET", "GET"],
            [call.args[0] for call in api.session.request.call_args_list],
        )
        api._v2_write_session.request.assert_called_once()
        method, url = api._v2_write_session.request.call_args.args
        kwargs = api._v2_write_session.request.call_args.kwargs
        self.assertEqual("PATCH", method)
        self.assertEqual(
            "https://seller.ggsel.com/api_sellers/v2/offers/102615259", url
        )
        self.assertNotIn("secret-v2-key", url)
        self.assertEqual("secret-v2-key", kwargs["headers"]["Authorization"])
        self.assertEqual("application/json", kwargs["headers"]["Content-Type"])
        self.assertEqual({"price": 110.0}, kwargs["json"])
        self.assertFalse(kwargs["allow_redirects"])

    def test_v2_price_write_timeout_is_not_retried_or_read_back(self):
        api = ggsel_api.GGSelAPI(
            make_config(ggsel_v2_price_write_enabled=True)
        )
        api.session.request = Mock(
            return_value=Response(
                payload={
                    "data": {
                        "id": 102615259,
                        "price": 100,
                        "currency": "RUB",
                    }
                }
            )
        )
        api._v2_write_session.request = Mock(side_effect=requests.ReadTimeout())

        self.assertIsNone(api.update_v2_offer_price(102615259, 110))
        api.session.request.assert_called_once()
        self.assertEqual("GET", api.session.request.call_args.args[0])
        api._v2_write_session.request.assert_called_once()
        retry = api._v2_write_session.get_adapter("https://").max_retries
        self.assertEqual((0, 0, 0, 0), (retry.total, retry.connect, retry.read, retry.status))
        self.assertEqual(ggsel_api.APIFailure.RETRYABLE, api.last_failure)

    def test_v2_price_write_rejects_mismatched_read_back(self):
        api = ggsel_api.GGSelAPI(
            make_config(ggsel_v2_price_write_enabled=True)
        )
        api.session.request = Mock(
            side_effect=(
                Response(
                    payload={
                        "data": {
                            "id": 102615259,
                            "price": 100,
                            "currency": "RUB",
                        }
                    }
                ),
                Response(
                    payload={
                        "data": {
                            "id": 102615259,
                            "price": 109,
                            "currency": "RUB",
                        }
                    }
                ),
            )
        )
        api._v2_write_session.request = Mock(
            return_value=Response(
                payload={"data": {"id": 102615259, "price": 110}}
            )
        )

        self.assertIsNone(api.update_v2_offer_price(102615259, 110))
        api._v2_write_session.request.assert_called_once()
        self.assertEqual(ggsel_api.APIFailure.PERMANENT, api.last_failure)

    def test_product_lookup_accepts_content_wrapped_product(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.token = "secret-token"
        api.session.request = Mock(
            return_value=Response(
                payload={"retval": 0, "content": {"product": {"name": "Wrapped"}}}
            )
        )

        self.assertEqual("Wrapped", api.get_real_product_name(99))

    def test_rate_limit_logs_server_cooldown_and_exhausted_retries(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.token = "opaque"
        response = Response(status=429)
        response.text = ""
        response.headers = {
            "Retry-After": "120",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1787217068",
        }
        response.raw = SimpleNamespace(
            retries=SimpleNamespace(history=(object(), object(), object()))
        )
        api.session.request = Mock(return_value=response)

        with self.assertLogs(level="WARNING") as cm:
            self.assertIsNone(api.get_real_product_name(99))
        self.assertIsNone(api.get_real_product_name(100))

        api.session.request.assert_called_once()
        self.assertEqual(ggsel_api.APIFailure.RETRYABLE, api.last_failure)
        self.assertTrue(
            any(
                "retry_after=120" in message
                and "remaining=0" in message
                and "retries=3" in message
                for message in cm.output
            ),
            cm.output,
        )

    def test_failed_requests_log_endpoint_and_body(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.token = "opaque"

        class FakeResponse:
            status_code = 400

            def text(self):
                return '{"error":"bad request"}'

        api.session.request = Mock(return_value=FakeResponse())

        with self.assertLogs(level="WARNING") as cm:
            self.assertIsNone(api.get_last_sales())

        self.assertTrue(
            any("GET" in msg and "seller-last-sales" in msg for msg in cm.output),
            cm.output,
        )

    def test_failed_message_posts_log_endpoint_and_body(self):
        api = ggsel_api.GGSelAPI(make_config())
        api.token = "opaque"

        class FakeResponse:
            status_code = 400

            def text(self):
                return '{"error":"invalid chat"}'

        api.session.request = Mock(return_value=FakeResponse())

        with self.assertLogs(level="WARNING") as cm:
            self.assertFalse(api.send_message(7, "hello"))

        self.assertTrue(
            any("POST" in msg and "debates/v2" in msg for msg in cm.output),
            cm.output,
        )


if __name__ == "__main__":
    unittest.main()
