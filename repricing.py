from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import math
from typing import Any, Dict, Optional, Tuple


def current_rub_price(product: Dict[str, Any]) -> Optional[float]:
    """Return the first valid RUB price from documented V1 product shapes."""
    candidates = []
    if str(product.get("currency", "")).upper() in {"RUB", "RUR"}:
        candidates.append(product.get("price"))
    prices = product.get("prices")
    if isinstance(prices, dict):
        for price_type in ("default", "initial"):
            values = prices.get(price_type)
            if isinstance(values, dict):
                candidates.append(values.get("RUB"))
    for candidate in candidates:
        try:
            price = float(candidate)
        except (TypeError, ValueError):
            continue
        if price > 0 and price < float("inf"):
            return price
    return None


def repricing_settings_json(enabled: bool, targets: Dict[int, float]) -> str:
    """Validate and serialize persisted dry-run settings."""
    parsed = parse_repricing_targets(targets)
    if not isinstance(enabled, bool) or enabled and not parsed:
        raise ValueError("add at least one product before enabling")
    return json.dumps(
        {"enabled": enabled, "targets": parsed}, separators=(",", ":"), sort_keys=True
    )


def parse_repricing_settings(value: str) -> Tuple[bool, Dict[int, float]]:
    """Validate persisted dry-run settings."""
    try:
        saved = json.loads(value)
        enabled = saved["enabled"]
        targets = parse_repricing_targets(saved["targets"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid repricing settings") from exc
    if not isinstance(enabled, bool) or enabled and not targets:
        raise ValueError("invalid repricing settings")
    return enabled, targets


def parse_repricing_targets(raw: Any) -> Dict[int, float]:
    if not isinstance(raw, dict):
        raise ValueError("repricing targets must be an object")
    targets: Dict[int, float] = {}
    for product_id, target_usd in raw.items():
        try:
            parsed_id = int(product_id)
            parsed_target = float(target_usd)
        except (TypeError, ValueError) as exc:
            raise ValueError("repricing targets must map product IDs to USD numbers") from exc
        if parsed_id <= 0 or not math.isfinite(parsed_target) or parsed_target <= 0:
            raise ValueError("repricing targets require positive finite values")
        targets[parsed_id] = parsed_target
    return targets


def parse_repricing_target(text: str) -> Tuple[int, float]:
    """Parse a Telegram `PRODUCT_ID TARGET_USD` value."""
    parts = text.split()
    if len(parts) != 2:
        raise ValueError("send exactly: PRODUCT_ID TARGET_USD")
    try:
        product_id = int(parts[0])
        target_usd = float(parts[1])
    except ValueError as exc:
        raise ValueError("product ID must be an integer and target USD must be numeric") from exc
    if product_id <= 0 or not math.isfinite(target_usd) or target_usd <= 0:
        raise ValueError("product ID and target USD must be positive finite values")
    return product_id, target_usd


def calculate_repricing_rub(
    target_usd: float,
    usd_rub_rate: float,
    fee_percent: float = 0.0,
    fixed_rub: float = 0.0,
) -> int:
    """Return a loss-safe whole-RUB price or reject unsafe inputs."""
    try:
        target = Decimal(str(target_usd))
        rate = Decimal(str(usd_rub_rate))
        fee = Decimal(str(fee_percent))
        fixed = Decimal(str(fixed_rub))
    except InvalidOperation as exc:
        raise ValueError("repricing values must be numeric") from exc
    if not all(value.is_finite() for value in (target, rate, fee, fixed)):
        raise ValueError("repricing values must be finite")
    if target <= 0 or not Decimal("20") <= rate <= Decimal("300"):
        raise ValueError(
            "target USD must be positive and USD/RUB must be between 20 and 300"
        )
    if not Decimal("0") <= fee < Decimal("100") or fixed < 0:
        raise ValueError(
            "fee must be below 100 percent and fixed cost cannot be negative"
        )

    gross = (target * rate + fixed) / (Decimal("1") - fee / Decimal("100"))
    # ponytail: whole-RUB ceiling; use GGSEL's documented increment if fractional prices are needed.
    return int(gross.to_integral_value(rounding=ROUND_CEILING))
