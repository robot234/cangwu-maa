"""Read the currency beside its own shop header icon before any submission."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import re

from .feature_config import CATALOG
from .runtime import Halt, ROOT
from .vision import Frame, normalized, template_match


@dataclass(frozen=True)
class Balance:
    display: str
    minimum: int
    maximum: int


def parse_balance(text: str) -> Balance | None:
    value = normalized(text)
    match = re.fullmatch(r"(\d{1,15}|\d{1,3}(?:,\d{3}){1,4})(\.\d{1,3})?([km万亿]?)", value)
    if match is None or (match[2] and not match[3]):
        return None
    number = Decimal(match[1].replace(",", "") + (match[2] or ""))
    if not match[3]:
        return Balance(text, int(number), int(number))
    scale = {"k": 1000, "m": 1000000, "万": 10000, "亿": 100000000}[match[3]]
    # The UI does not expose whether abbreviations truncate or round. Use a
    # whole last displayed unit on either side, never the displayed midpoint.
    step = Decimal(1).scaleb(-len(match[2][1:])) if match[2] else Decimal(1)
    low = max(0, int(((number - step) * scale).to_integral_value(rounding=ROUND_FLOOR)))
    high = int(((number + step) * scale).to_integral_value(rounding=ROUND_CEILING)) - 1
    return Balance(text, low, max(low, high))


def shop_balance(frame: Frame, currency: str) -> Balance:
    if (currency not in CATALOG["currency_labels"] or frame.is_loading() or frame.reward()
            or frame.paid_dialog() or not frame.has("商城系统", region=(180, 0, 450, 90), confidence=.9)
            or not (frame.exchange_list() or frame.has("是否购买", region=(350, 140, 850, 225), confidence=.9))):
        raise Halt("当前不是可核对余额的商店页面。")
    cached = getattr(frame, "_shop_balances", {})
    if currency in cached:
        return cached[currency]
    slots = (((855, 18, 905, 65), (902, 18, 1025, 65)),
             ((1045, 18, 1090, 65), (1087, 18, 1220, 65)))
    found = []
    for icon_region, number_region in slots:
        identities = [name for name in CATALOG["currency_labels"]
                      if template_match(frame, ROOT / f"assets/currency_{name}_balance.png", icon_region, .93)]
        if currency in identities:
            if len(identities) != 1:
                raise Halt("余额旁的货币图标存在歧义，无法核对余额。")
            found.append(number_region)
    if len(found) != 1:
        raise Halt(f"没有唯一识别到{CATALOG['currency_labels'][currency]}的余额图标，无法核对余额。")
    region = found[0]
    words = frame.words
    for attempt in range(2):
        values = [parse_balance(word.text) for word in words if word.confidence >= .9
                  and region[0] <= word.box[0] and word.box[2] <= region[2]
                  and region[1] <= word.center[1] <= region[3]]
        values = [value for value in values if value is not None]
        if len(values) == 1:
            cached[currency] = values[0]
            frame._shop_balances = cached
            return values[0]
        if len(values) > 1:
            break
        if attempt == 0 and frame.vision is not None:
            words = frame.vision.region_words(frame, region)
        else:
            break
    raise Halt(f"没有读清{CATALOG['currency_labels'][currency]}的余额，无法核对余额。")
