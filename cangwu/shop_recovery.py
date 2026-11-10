"""Decide a pending exchange from independent, stable shop observations."""
from __future__ import annotations

from datetime import datetime

from .feature_config import CATALOG, game_day, shop_period
from .runtime import CST
from .shop_balance import parse_balance


def timestamp(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(CST) if stamp.tzinfo is not None else None
    except ValueError:
        return None


def balance_bounds(value) -> tuple[int, int] | None:
    if not isinstance(value, dict) or not isinstance(value.get("display"), str):
        return None
    low, high = value.get("minimum"), value.get("maximum")
    parsed = parse_balance(value["display"])
    if (type(low) is not int or type(high) is not int or low < 0 or high < low or parsed is None
            or (low, high) != (parsed.minimum, parsed.maximum)):
        return None
    return low, high


def stock_not_reset(before: dict, after: dict) -> bool:
    start, end = timestamp(before.get("observed_at")), timestamp(after.get("observed_at"))
    if start is None or end is None or end < start:
        return False
    elapsed = (datetime.fromisoformat(game_day(end)) - datetime.fromisoformat(game_day(start))).days
    remaining, current = before.get("stock_reset_days"), after.get("stock_reset_days")
    if remaining == 0 or current == 0:
        return False
    if elapsed == 0:
        return True
    # A countdown read on this specific card can prove that a date change
    # did not refresh its stock. A target-period reset alone cannot do that.
    return (type(remaining) is int and type(current) is int and 0 < elapsed < remaining
            and max(1, remaining - elapsed - 1) <= current <= remaining - max(0, elapsed - 1))


def assess_receipt(state: dict, shop: str, before: dict, observations: list[dict]) -> tuple[str | None, str]:
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return None, "缺少待核对兑换"
    item = next((item for item in CATALOG.get(shop, []) if item["id"] == pending.get("item")), None)
    quantity = pending.get("quantity")
    if (item is None or type(quantity) is not int or quantity <= 0
            or type(pending.get("total")) is not int or pending["total"] != quantity * item["price"]
            or pending.get("currency") != item["currency"]):
        return None, "待核对商品、数量、币种或总价不一致"
    old_balance = balance_bounds(before.get("balance"))
    old_stock = before.get("stock")
    captured = timestamp(before.get("observed_at"))
    if (old_balance is None or type(old_stock) is not int or old_stock < quantity or captured is None
            or before.get("currency") != item["currency"]
            or shop_period(shop, captured) != state.get("day")):
        return None, "缺少可靠的兑换前余额、有限库存或原周期信息"
    if len(observations) != 2 or any(not isinstance(view, dict) for view in observations):
        return None, "需要两次重新进入商店后的识别结果"
    first, last = observations
    times = [timestamp(view.get("observed_at")) for view in observations]
    bounds = [balance_bounds(view.get("balance")) for view in observations]
    if (any(value is None for value in times) or not captured < times[0] < times[1]
            or game_day(times[0]) != game_day(times[1])
            or any(view.get("fresh") is not True or view.get("currency") != item["currency"]
                   or type(view.get("stock")) is not int or view["stock"] < 0 for view in observations)
            or any(value is None for value in bounds)):
        return None, "新画面、余额、库存或识别时间未能核实"
    if (bounds[0] != bounds[1] or first["stock"] != last["stock"]
            or first.get("stock_reset_days") != last.get("stock_reset_days")):
        return None, "两次识别结果尚未稳定"
    if not stock_not_reset(before, last):
        return None, "商品库存可能已经刷新，无法用旧库存判定兑换结果"
    low_delta, high_delta = old_balance[0] - bounds[1][1], old_balance[1] - bounds[1][0]
    used_stock = old_stock - last["stock"]
    if used_stock == quantity and low_delta <= pending["total"] <= high_delta:
        return "completed", "两次重识别均确认库存减少指定份数，余额变化与总价一致"
    if used_stock == 0 and low_delta <= 0 <= high_delta:
        return "not_exchanged", "两次重识别均确认原库存未减少，余额也未见扣款"
    return None, "余额或库存变化与本次兑换对不上，可能存在其他操作"
