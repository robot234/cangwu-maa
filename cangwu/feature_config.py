from __future__ import annotations

import json
import copy
from datetime import datetime, timedelta
from pathlib import Path

from .runtime import ROOT, CST, Halt, NeedsSetup


CATALOG = json.loads((ROOT / "assets/shop_catalog.json").read_text(encoding="utf-8"))
SHOPS = {
    "elite": {"label": "精英商店", "entry": "CangwuEliteShop", "period": "day"},
    "logistics": {"label": "后勤商店", "entry": "CangwuLogisticsShop", "period": "week"},
    "union": {"label": "联合商店", "entry": "CangwuUnionShop", "period": "day"},
    "master": {"label": "大师商店", "entry": "CangwuMasterShop", "period": "day"},
}
SHOP_TASKS = {f"shop_{key}": key for key in SHOPS}
BOSSES = {
    "niai": {"name": "逆爱", "region": (0, 110, 126, 210)},
    "sobek": {"name": "索贝克", "region": (0, 220, 126, 325)},
    "leviathan": {"name": "利维坦", "region": (0, 335, 126, 445)},
}


def item_node(shop: str, item_id: str) -> str:
    return f"CangwuParam_{shop}_{item_id}"


def read_parameter(context, name: str) -> dict:
    node = context.get_node_data(name)
    if not isinstance(node, dict):
        raise NeedsSetup("新功能资源尚未加载，请重新启动助手后再试。")
    action = node.get("action", {})
    param = action.get("param", {}) if isinstance(action, dict) else {}
    value = param.get("custom_action_param", node.get("custom_action_param"))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise Halt(f"选项 {name} 不是有效参数。") from exc
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise Halt(f"选项 {name} 数据不完整，停止执行。")
    quantity = value.get("quantity")
    if type(quantity) is not int or quantity < 0:
        raise Halt(f"选项 {name} 数量无效。")
    return value


def shop_selection(context, shop: str) -> list[tuple[dict, int]]:
    if shop not in SHOPS:
        raise Halt("不支持的兑换商店；调度商店已移除。")
    selected = []
    for item in CATALOG[shop]:
        option = read_parameter(context, item_node(shop, item["id"]))
        if option["enabled"]:
            if not 1 <= option["quantity"] <= 999:
                raise Halt("已勾选商品的兑换数量必须为 1～999。")
            selected.append((item, option["quantity"]))
    return selected


def game_day(now: datetime | None = None) -> str:
    # CN reset inferred from the in-game task countdown. The armor ledger also
    # requires an increase in the game's remaining counter before resetting.
    return ((now or datetime.now(CST)).astimezone(CST) - timedelta(hours=5)).date().isoformat()


def game_week(now: datetime | None = None) -> str:
    day = (now or datetime.now(CST)).astimezone(CST) - timedelta(hours=5)
    return (day.date() - timedelta(days=day.weekday())).isoformat()


def shop_period(shop: str, now: datetime | None = None) -> str:
    return "week:" + game_week(now) if SHOPS[shop]["period"] == "week" else game_day(now)


def armor_selection(context) -> dict[str, int]:
    selected = {}
    for boss in BOSSES:
        option = read_parameter(context, item_node("armor", boss))
        if option["enabled"]:
            if not 1 <= option["quantity"] <= 5:
                raise Halt("每种共鸣次数必须为 1～5。")
            selected[boss] = option["quantity"]
    if sum(selected.values()) > 5:
        raise Halt("三种共鸣的每周配置合计不能超过 5 次，请调整数量。")
    return selected


def saved_feature_overrides(definition: dict, values: dict) -> dict:
    """Developer API runner must reset cached selections just like native MXU."""
    key = {**SHOP_TASKS, "armor": "armor"}[definition["name"]]
    override = copy.deepcopy(definition["pipeline_override"])
    choices = BOSSES if key == "armor" else {item["id"]: item for item in CATALOG[key]}
    for item_id in choices:
        selected = values.get(f"choice_{key}_{item_id}", {"type": "switch", "value": False})
        if selected.get("type") != "switch" or type(selected.get("value")) is not bool:
            raise Halt("已保存的商品／共鸣开关格式无效。")
        if not selected["value"]:
            continue
        quantity = values.get(f"quantity_{key}_{item_id}", {}).get("values", {}).get("amount", "1")
        if not isinstance(quantity, str) or not quantity.isascii() or not quantity.isdecimal():
            raise Halt("已保存的商品／共鸣数量格式无效。")
        count = int(quantity)
        if not 1 <= count <= (5 if key == "armor" else 999):
            raise Halt("已保存的商品／共鸣数量超出范围。")
        override[item_node(key, item_id)]["action"]["param"]["custom_action_param"] = {"enabled": True, "quantity": count}
    return override


def save_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_state(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise NeedsSetup("新功能运行记录损坏，请先核对上次执行结果。") from exc
    if not isinstance(value, dict):
        raise NeedsSetup("新功能运行记录格式无效。")
    return value
