import argparse
import hashlib
import logging
import math
import threading
import time
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Config
from database import Chat


class APIFailure(str, Enum):
    """Machine-readable classification for the most recent API failure."""

    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    AUTHENTICATION = "authentication"


class TimeoutSession(requests.Session):
    """Requests session that applies the configured timeout to every call."""

    def __init__(self, default_timeout: Tuple[float, float]):
        super().__init__()
        self.default_timeout = default_timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self.default_timeout)
        return super().request(*args, **kwargs)


def create_ggsel_session(
    max_retries: int = 3,
    timeout: Tuple[float, float] = (5.0, 30.0),
) -> TimeoutSession:
    """Create a persistent session that never retries customer-message POSTs."""
    session = TimeoutSession(timeout)
    session.headers.update(
        {
            "User-Agent": "GGSel-Seller-Helper/1.1",
            "Accept": "application/json",
            "Connection": "keep-alive",
        }
    )

    retry_count = max(0, int(max_retries))
    retries = Retry(
        total=retry_count,
        connect=retry_count,
        # A read timeout already consumed the full configured timeout. Retrying
        # it can pin every message-poll worker for minutes and stop all sync.
        read=0,
        status=retry_count,
        backoff_factor=0.5,
        status_forcelist=(408, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
    session.mount("https://", adapter)
    return session


class GGSelAPI:
    DEFAULT_TIMEOUT: Tuple[float, float] = (5.0, 30.0)
    V2_BASE_URL = "https://seller.ggsel.com/api_sellers/v2"
    MAX_MESSAGE_LENGTH = 4000
    RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

    def __init__(self, config: Config):
        self.config = config
        self.base_url = self._validated_base_url(config.ggsel_base_url)
        self.timeout = self._validated_timeout(
            getattr(config, "ggsel_connect_timeout", self.DEFAULT_TIMEOUT[0]),
            getattr(config, "ggsel_read_timeout", self.DEFAULT_TIMEOUT[1]),
        )
        self.token: Optional[str] = None
        self.last_failure: Optional[APIFailure] = None
        self._session_local = threading.local()
        self._login_lock = threading.Lock()
        # ponytail: process-local serialization; add a distributed lock before multiple writer replicas.
        self._v2_price_write_lock = threading.Lock()
        self._v2_write_session = create_ggsel_session(0, self.timeout)
        self._auth_blocked_until = 0.0
        self._product_lookup_blocked_until = 0.0
        # ponytail: process-local cache; add TTL/shared storage if names change or replicas multiply.
        self._product_name_cache: Dict[int, str] = {}
        self.session = self._new_session()

    def _new_session(self) -> TimeoutSession:
        return create_ggsel_session(self.config.max_retries, self.timeout)

    @property
    def session(self) -> TimeoutSession:
        session = getattr(self._session_local, "session", None)
        if session is None:
            session = self._new_session()
            self._session_local.session = session
        return session

    @session.setter
    def session(self, value: TimeoutSession) -> None:
        self._session_local.session = value

    def _replace_session(self) -> None:
        try:
            self.session.close()
        finally:
            self.session = self._new_session()

    @staticmethod
    def _validated_base_url(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("GGSEL_BASE_URL must be a non-empty HTTPS URL")
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "GGSEL_BASE_URL must be HTTPS without credentials, query, or fragment"
            )
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("GGSEL_BASE_URL contains an invalid port") from exc

        host = parsed.hostname.encode("idna").decode("ascii").lower()
        rendered_host = f"[{host}]" if ":" in host else host
        netloc = f"{rendered_host}:{port}" if port is not None else rendered_host
        return urlunsplit(("https", netloc, parsed.path.rstrip("/"), "", ""))

    @staticmethod
    def _validated_timeout(connect: Any, read: Any) -> Tuple[float, float]:
        try:
            timeout = (float(connect), float(read))
        except (TypeError, ValueError) as exc:
            raise ValueError("GGSel HTTP timeouts must be numeric") from exc
        if any(value <= 0 or value > 300 for value in timeout):
            raise ValueError("GGSel HTTP timeouts must be greater than 0 and at most 300 seconds")
        return timeout

    @staticmethod
    def _positive_int(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _generate_sign(self, timestamp: str) -> str:
        data = f"{self.config.ggsel_api_key}{timestamp}"
        return hashlib.sha256(data.encode()).hexdigest()

    @staticmethod
    def _retry_after_seconds(value: Any) -> float:
        try:
            delay = float(value)
        except (TypeError, ValueError):
            try:
                delay = parsedate_to_datetime(str(value)).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                delay = 60.0
        return min(3600.0, max(1.0, delay))

    def _set_http_failure(self, status_code: int) -> None:
        if status_code in (401, 403):
            self.last_failure = APIFailure.AUTHENTICATION
        elif status_code in self.RETRYABLE_STATUS_CODES:
            self.last_failure = APIFailure.RETRYABLE
        else:
            self.last_failure = APIFailure.PERMANENT

    def _request(
        self,
        method: str,
        path: str,
        *,
        base_url: Optional[str] = None,
        request_session: Optional[requests.Session] = None,
        **kwargs: Any,
    ) -> Optional[requests.Response]:
        kwargs["timeout"] = self.timeout
        url = f"{base_url or self.base_url}/{path.lstrip('/')}"
        try:
            response = (request_session or self.session).request(method, url, **kwargs)
        except (requests.Timeout, requests.ConnectionError):
            self.last_failure = APIFailure.RETRYABLE
            logging.warning("GGSel API request failed due to a temporary transport error")
            return None
        except requests.RequestException:
            self.last_failure = APIFailure.PERMANENT
            logging.warning("GGSel API request failed before receiving a response")
            return None

        if not 200 <= response.status_code < 300:
            self._set_http_failure(response.status_code)
            body = None
            try:
                body = response.text
            except Exception:
                pass
            if response.status_code == 429:
                headers = getattr(response, "headers", {})
                retry_after = headers.get("Retry-After")
                cooldown = self._retry_after_seconds(retry_after)
                self._product_lookup_blocked_until = max(
                    self._product_lookup_blocked_until,
                    time.monotonic() + cooldown,
                )
                retry_history = getattr(
                    getattr(getattr(response, "raw", None), "retries", None),
                    "history",
                    (),
                )
                logging.warning(
                    "GGSel rate limit details: retry_after=%s remaining=%s reset=%s retries=%s cooldown=%ss",
                    retry_after,
                    headers.get("X-RateLimit-Remaining"),
                    headers.get("X-RateLimit-Reset"),
                    len(retry_history),
                    cooldown,
                )
            logging.warning(
                "GGSel API returned HTTP %s for %s %s: %s",
                response.status_code,
                method,
                path,
                body,
            )
            return None
        return response

    def _json(self, response: requests.Response) -> Optional[Any]:
        try:
            return response.json()
        except (ValueError, TypeError):
            self.last_failure = APIFailure.PERMANENT
            return None

    def login(self) -> bool:
        with self._login_lock:
            return self._login_locked()

    def _login_locked(self) -> bool:
        current_time = time.monotonic()
        if current_time < self._auth_blocked_until:
            return False

        timestamp = str(int(time.time()))
        response = self._request(
            "POST",
            "apilogin",
            headers={"Content-Type": "application/json"},
            json={
                "seller_id": self.config.ggsel_seller_id,
                "timestamp": timestamp,
                "sign": self._generate_sign(timestamp),
            },
        )
        if response is None:
            self.token = None
            if self.last_failure == APIFailure.RETRYABLE:
                self._auth_blocked_until = current_time + 300
                self._replace_session()
            else:
                self._auth_blocked_until = current_time + max(10, self.config.retry_delay)
            return False

        data = self._json(response)
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token.strip():
            self.token = None
            self.last_failure = APIFailure.PERMANENT
            self._auth_blocked_until = current_time + max(10, self.config.retry_delay)
            return False

        self.token = token.strip()
        self.last_failure = None
        self._auth_blocked_until = 0.0
        return True

    def _authenticated_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[requests.Response]:
        if not self.token and not self.login():
            return None

        request_params = dict(params or {})
        request_params["token"] = self.token
        response = self._request(method, path, params=request_params, **kwargs)
        if response is None and self.last_failure == APIFailure.AUTHENTICATION:
            self.token = None
            if self.login():
                request_params["token"] = self.token
                response = self._request(method, path, params=request_params, **kwargs)
        return response

    def get_chats(
        self,
        filter_new: Optional[int] = None,
        email: Optional[str] = None,
        id_ds: Optional[str] = None,
        pagesize: int = 100,
        page: int = 1,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(pagesize, int) or not 1 <= pagesize <= 1000:
            self.last_failure = APIFailure.PERMANENT
            return None
        if not isinstance(page, int) or page < 1:
            self.last_failure = APIFailure.PERMANENT
            return None

        params: Dict[str, Any] = {"pagesize": pagesize, "page": page}
        if filter_new is not None:
            params["filter_new"] = filter_new
        if email:
            params["email"] = email
        if id_ds:
            params["id_ds"] = id_ds

        response = self._authenticated_request("GET", "debates/v2/chats", params=params)
        data = self._json(response) if response is not None else None
        if isinstance(data, dict):
            self.last_failure = None
            return data
        if response is not None:
            self.last_failure = APIFailure.PERMANENT
        return None

    def get_chat_messages(self, chat_id: int) -> Optional[List[Dict[str, Any]]]:
        chat_id = self._positive_int(chat_id)
        if chat_id is None:
            self.last_failure = APIFailure.PERMANENT
            return None
        response = self._authenticated_request(
            "GET", "debates/v2", params={"id_i": chat_id}
        )
        data = self._json(response) if response is not None else None
        messages = data if isinstance(data, list) else data.get("messages") if isinstance(data, dict) else None
        if isinstance(messages, list) and all(isinstance(item, dict) for item in messages):
            self.last_failure = None
            return messages
        if response is not None:
            self.last_failure = APIFailure.PERMANENT
        return None

    @staticmethod
    def _clean_message(message: Any) -> Optional[str]:
        if not isinstance(message, str):
            return None
        cleaned = "".join(char for char in message if char in "\n\r\t" or ord(char) >= 32)
        if not cleaned.strip():
            return None
        return cleaned[:GGSelAPI.MAX_MESSAGE_LENGTH]

    def send_message(self, chat_id: int, message: str) -> bool:
        chat_id = self._positive_int(chat_id)
        if chat_id is None:
            self.last_failure = APIFailure.PERMANENT
            return False
        cleaned = self._clean_message(message)
        if cleaned is None:
            self.last_failure = APIFailure.PERMANENT
            return False
        if not self.token and not self.login():
            return False

        for attempt in range(2):
            try:
                response = self.session.request(
                    "POST",
                    self._url("debates/v2"),
                    params={"token": self.token, "id_i": chat_id},
                    json={"message": cleaned, "text": cleaned},
                    timeout=self.timeout,
                )
            except requests.ReadTimeout:
                # The server may already have accepted the message. Treat the
                # ambiguous result as delivered to avoid duplicate replies.
                self.last_failure = None
                logging.warning("GGSel message response timed out; suppressing duplicate retry")
                return True
            except (requests.Timeout, requests.ConnectionError):
                self.last_failure = APIFailure.RETRYABLE
                return False
            except requests.RequestException:
                self.last_failure = APIFailure.PERMANENT
                return False

            if response.status_code in (401, 403) and attempt == 0:
                self.token = None
                self.last_failure = APIFailure.AUTHENTICATION
                if self.login():
                    continue
                return False
            if not 200 <= response.status_code < 300:
                self._set_http_failure(response.status_code)
                body = None
                try:
                    body = response.text
                except Exception:
                    pass
                logging.warning(
                    "GGSel API returned HTTP %s for POST debates/v2: %s",
                    response.status_code,
                    body,
                )
                return False

            try:
                data = response.json()
            except (ValueError, TypeError):
                data = None
            if isinstance(data, dict) and "retval" in data and data.get("retval") != 0:
                self.last_failure = APIFailure.PERMANENT
                return False
            self.last_failure = None
            return True
        return False

    def get_last_sales(self, top: int = 10) -> Optional[Dict[str, Any]]:
        if not isinstance(top, int) or not 1 <= top <= 1000:
            self.last_failure = APIFailure.PERMANENT
            return None
        response = self._authenticated_request(
            "GET",
            "seller-last-sales",
            params={"top": top},
            headers={"Accept": "application/json", "locale": "ru"},
        )
        data = self._json(response) if response is not None else None
        if isinstance(data, dict):
            self.last_failure = None
            return data
        return None

    def get_balance_info(self) -> Optional[Dict[str, Any]]:
        response = self._authenticated_request(
            "GET",
            "sellers/account/balance/info",
            headers={"Accept": "application/json"},
        )
        data = self._json(response) if response is not None else None
        if isinstance(data, dict):
            self.last_failure = None
            return data
        return None

    def get_purchase_info(self, invoice_id: int) -> Optional[Dict[str, Any]]:
        invoice_id = self._positive_int(invoice_id)
        if invoice_id is None:
            self.last_failure = APIFailure.PERMANENT
            return None
        response = self._authenticated_request(
            "GET",
            f"purchase/info/{invoice_id}",
            headers={"Accept": "application/json", "locale": "ru"},
        )
        data = self._json(response) if response is not None else None
        if isinstance(data, dict) and data.get("retval") == 0:
            self.last_failure = None
            return data
        if response is not None:
            self.last_failure = APIFailure.PERMANENT
        return None

    def get_chats_by_email(
        self, email: str, pagesize: int = 100, page: int = 1
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(email, str) or not email.strip():
            self.last_failure = APIFailure.PERMANENT
            return None
        return self.get_chats(email=email.strip(), pagesize=pagesize, page=page)

    def parse_chats_response(self, response_data: Dict[str, Any]) -> List[Chat]:
        chats = []
        if not isinstance(response_data, dict):
            return chats
        for item in response_data.get("items", []):
            if not isinstance(item, dict) or item.get("id_i") is None:
                continue
            chats.append(
                Chat(
                    id_i=item["id_i"],
                    email=item.get("email") or None,
                    product=item.get("product", 0),
                    last_message=item.get("last_message", ""),
                    cnt_msg=item.get("cnt_msg", 0),
                    cnt_new=item.get("cnt_new", 0),
                )
            )
        return chats

    def get_reviews(
        self,
        count: int = 20,
        review_type: str = "all",
        page: int = 1,
        product_id: int = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(count, int) or not 1 <= count <= 100:
            self.last_failure = APIFailure.PERMANENT
            return None
        if not isinstance(page, int) or page < 1:
            self.last_failure = APIFailure.PERMANENT
            return None

        params: Dict[str, Any] = {
            "type": review_type,
            "page": page,
            "count": count,
        }
        if product_id:
            params["product_id"] = product_id
        response = self._authenticated_request(
            "GET",
            "reviews",
            params=params,
            headers={"Accept": "application/json", "locale": "ru-RU"},
        )
        data = self._json(response) if response is not None else None
        if isinstance(data, dict) and data.get("retval") == 0:
            self.last_failure = None
            return data
        if response is not None:
            self.last_failure = APIFailure.PERMANENT
        return None

    def get_review_by_invoice(self, invoice_id: int) -> Optional[Dict[str, Any]]:
        target = str(invoice_id)
        for page in range(1, 20):
            data = self.get_reviews(count=50, page=page)
            if not data:
                break
            reviews = data.get("reviews", [])
            if not reviews:
                break
            for review in reviews:
                if isinstance(review, dict) and str(review.get("invoice_id")) == target:
                    return review
        return None

    def get_product_info(self, item_id: int) -> Optional[Dict[str, Any]]:
        """Read one V1 product without placing the auth token in the URL."""
        item_id = self._positive_int(item_id)
        if item_id is None:
            self.last_failure = APIFailure.PERMANENT
            return None
        if time.monotonic() < self._product_lookup_blocked_until:
            self.last_failure = APIFailure.RETRYABLE
            return None
        response = self._authenticated_request(
            "GET",
            f"products/{item_id}/data",
            headers={"Accept": "application/json"},
        )
        data = self._json(response) if response is not None else None
        product = data.get("product") if isinstance(data, dict) else None
        if not isinstance(product, dict):
            content = data.get("content") if isinstance(data, dict) else None
            product = content.get("product") if isinstance(content, dict) else None
            if not isinstance(product, dict):
                product = content
        if isinstance(product, dict):
            self.last_failure = None
            return product
        if response is not None:
            self.last_failure = APIFailure.PERMANENT
        return None

    def list_v2_offers(
        self, page: int = 1, limit: int = 10
    ) -> Optional[Dict[str, Any]]:
        """Read one validated page of public V2 offers."""
        api_key = getattr(self.config, "ggsel_v2_api_key", "")
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or page < 1
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
            or not isinstance(api_key, str)
            or not api_key.strip()
        ):
            self.last_failure = APIFailure.PERMANENT
            return None
        response = self._request(
            "GET",
            "offers",
            base_url=self.V2_BASE_URL,
            params={"page": page, "limit": limit},
            headers={
                "Accept": "application/json",
                "Authorization": api_key.strip(),
            },
        )
        data = self._json(response) if response is not None else None
        payload = data.get("data") if isinstance(data, dict) else None
        if isinstance(payload, list):
            offers = payload
            pagination = data.get("pagination", data)
        elif isinstance(payload, dict):
            offers = payload.get("offers", payload.get("items"))
            pagination = payload.get("pagination", data.get("pagination", payload))
        else:
            offers = data.get("offers", data.get("items")) if isinstance(data, dict) else None
            pagination = data.get("pagination", data) if isinstance(data, dict) else None
        if not isinstance(offers, list) or not isinstance(pagination, dict):
            if response is not None:
                self.last_failure = APIFailure.PERMANENT
            return None
        validated = []
        for offer in offers:
            if not isinstance(offer, dict) or self._positive_int(offer.get("id")) is None:
                self.last_failure = APIFailure.PERMANENT
                return None
            validated.append(offer)
        self.last_failure = None
        return {
            "offers": validated,
            "page": page,
            "has_next_page": pagination.get("has_next_page") is True,
            "has_previous_page": page > 1 and pagination.get("has_previous_page") is not False,
        }

    def get_v2_offer(self, offer_id: int) -> Optional[Dict[str, Any]]:
        """Read and validate one public V2 offer."""
        offer_id = self._positive_int(offer_id)
        api_key = getattr(self.config, "ggsel_v2_api_key", "")
        if offer_id is None or not isinstance(api_key, str) or not api_key.strip():
            self.last_failure = APIFailure.PERMANENT
            return None

        response = self._request(
            "GET",
            f"offers/{offer_id}",
            base_url=self.V2_BASE_URL,
            headers={
                "Accept": "application/json",
                "Authorization": api_key.strip(),
            },
        )
        data = self._json(response) if response is not None else None
        offer = data.get("data") if isinstance(data, dict) else None
        if not isinstance(offer, dict):
            offer = data
        if not isinstance(offer, dict) or self._positive_int(offer.get("id")) != offer_id:
            if response is not None:
                self.last_failure = APIFailure.PERMANENT
            return None
        try:
            price = float(offer.get("price"))
        except (TypeError, ValueError):
            self.last_failure = APIFailure.PERMANENT
            return None
        if price <= 0 or not math.isfinite(price):
            self.last_failure = APIFailure.PERMANENT
            return None
        currency = offer.get("currency")
        if currency is not None and str(currency).upper() not in {"RUB", "RUR"}:
            self.last_failure = APIFailure.PERMANENT
            return None
        self.last_failure = None
        return offer

    def update_v2_offer_price(
        self, offer_id: int, price: float
    ) -> Optional[Dict[str, Any]]:
        """PATCH one allowlisted RUB price once, then return its verified read-back."""
        if (
            getattr(self.config, "ggsel_v2_price_write_enabled", False) is not True
            or isinstance(offer_id, bool)
            or not isinstance(offer_id, int)
            or offer_id <= 0
            or isinstance(price, bool)
            or not isinstance(price, (int, float))
        ):
            self.last_failure = APIFailure.PERMANENT
            return None
        proposed_price = float(price)
        targets = getattr(self.config, "repricing_targets_usd", {})
        try:
            max_change = float(
                getattr(self.config, "repricing_max_change_percent", 15.0)
            )
        except (TypeError, ValueError):
            max_change = math.nan
        if (
            not math.isfinite(proposed_price)
            or proposed_price <= 0
            or not isinstance(targets, dict)
            or offer_id not in targets
            or not math.isfinite(max_change)
            or not 0 < max_change <= 100
        ):
            self.last_failure = APIFailure.PERMANENT
            return None

        with self._v2_price_write_lock:
            current_offer = self.get_v2_offer(offer_id)
            if current_offer is None:
                return None
            current_price = float(current_offer["price"])
            if abs(proposed_price - current_price) / current_price * 100 > max_change:
                self.last_failure = APIFailure.PERMANENT
                return None
            if math.isclose(
                proposed_price, current_price, rel_tol=0.0, abs_tol=1e-9
            ):
                return current_offer

            api_key = str(getattr(self.config, "ggsel_v2_api_key", "")).strip()
            response = self._request(
                "PATCH",
                f"offers/{offer_id}",
                base_url=self.V2_BASE_URL,
                request_session=self._v2_write_session,
                headers={
                    "Accept": "application/json",
                    "Authorization": api_key,
                    "Content-Type": "application/json",
                },
                json={"price": proposed_price},
                allow_redirects=False,
            )
            if response is None:
                return None

            verified_offer = self.get_v2_offer(offer_id)
            if verified_offer is None or not math.isclose(
                float(verified_offer["price"]),
                proposed_price,
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                self.last_failure = APIFailure.PERMANENT
                return None
            self.last_failure = None
            return verified_offer

    def get_real_product_name(self, item_id: int) -> Optional[str]:
        """Fetch a product name without placing the auth token in the URL."""
        item_id = self._positive_int(item_id)
        if item_id is None:
            self.last_failure = APIFailure.PERMANENT
            return None
        cached = self._product_name_cache.get(item_id)
        if cached:
            self.last_failure = None
            return cached
        product = self.get_product_info(item_id)
        name = product.get("name") if product else None
        if isinstance(name, str) and name.strip():
            self._product_name_cache[item_id] = name.strip()
            return self._product_name_cache[item_id]
        return None


def _diagnose_v2_offer() -> None:
    parser = argparse.ArgumentParser(description="Read one GGSEL public-V2 offer")
    parser.add_argument("offer_id", type=int)
    args = parser.parse_args()
    api = GGSelAPI(Config.from_env())
    offer = api.get_v2_offer(args.offer_id)
    if offer is None:
        raise SystemExit(f"V2 GET failed: {api.last_failure or 'invalid response'}")
    print(
        f"offer_id={offer['id']} status={offer.get('status')} "
        f"price={offer['price']} currency={offer.get('currency', 'RUB')}"
    )


if __name__ == "__main__":
    _diagnose_v2_offer()
