"""Generate native MXU options, with one isolated Maa data node per choice."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cangwu.feature_config import CATALOG, BOSSES, SHOPS, item_node


def parameter(value):
    return {"action": {"type": "Custom", "param": {"custom_action": "CangwuParameter", "custom_action_param": value}}}


def main():
    interface_path = ROOT / "app/interface.json"
    pipeline_path = ROOT / "app/resource/pipeline/daily.json"
    interface = json.loads(interface_path.read_text(encoding="utf-8"))
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    generated = (*SHOPS, "dispatch", "armor")
    for key in list(interface["option"]):
        if any(key.startswith(prefix + name + "_") or key == "choices_" + name
               for name in generated for prefix in ("choice_", "quantity_")):
            del interface["option"][key]
    for key in list(pipeline):
        if key.startswith("CangwuParam_") or key == "CangwuDispatchShop":
            del pipeline[key]
    tasks = []
    definitions = [(key, spec["entry"], spec["label"] + " · 自选兑换") for key, spec in SHOPS.items()]
    definitions.append(("armor", "CangwuArmor", "装甲进阶 · 每周共鸣"))
    for key, entry, label in definitions:
        is_armor = key == "armor"
        weekly = is_armor or SHOPS[key]["period"] == "week"
        period = "每周" if weekly else "每日"
        task_name = "armor" if is_armor else f"shop_{key}"
        choices = [{"id": k, "name": v["name"]} for k, v in BOSSES.items()] if is_armor else CATALOG[key]
        reset, cases, choice_keys = {}, [], []
        for item in choices:
            node = item_node(key, item["id"])
            reset[node] = parameter({"enabled": False, "quantity": 0})
            pipeline[node] = {"recognition": "DirectHit", **reset[node]}
            quantity_key = f"quantity_{key}_{item['id']}"
            interface["option"][quantity_key] = {
                "type": "input", "label": f"{item['name']} · {'每周次数' if is_armor else period + '份数'}",
                "inputs": [{"name": "amount", "label": "每周次数（所有共鸣合计最多5次）" if is_armor else period + "兑换份数（超过库存或资产上限时按当前MAX兑换）",
                            "default": "1", "pipeline_type": "int", "verify": "^[1-5]$" if is_armor else "^[1-9][0-9]{0,2}$",
                            "pattern_msg": "请输入1～5，三项合计不超过5" if is_armor else "请输入1～999的整数"}],
                "pipeline_override": {node: parameter({"enabled": True, "quantity": "{amount}"})},
            }
            item_label = item["name"]
            if not is_armor:
                item_label += f" · {item['price']} {CATALOG['currency_labels'][item['currency']]}"
                if item.get("variant"):
                    item_label += f"（{item['variant']}）"
            choice_key = f"choice_{key}_{item['id']}"
            choice_keys.append(choice_key)
            # MXU 2.7.1 only recurses into switch/select suboptions; checkbox
            # case.option is not traversed. Each item therefore has a switch.
            interface["option"][choice_key] = {"type": "switch", "label": item_label, "default_case": "No", "cases": [
                {"name": "No", "label": "不兑换" if not is_armor else "关闭", "pipeline_override": {node: parameter({"enabled": False, "quantity": 0})}},
                {"name": "Yes", "label": "兑换" if not is_armor else "启用", "option": [quantity_key],
                 "pipeline_override": {node: parameter({"enabled": True, "quantity": 1})}}]}
        interface["option"].pop(f"choices_{key}", None)
        description = ("逆爱、索贝克、利维坦分别设置每周目标，合计≤5。只扫荡已通关1-5地狱，每次40体力；游戏剩余0/5时跳过。应放在活动清体力之前。"
                       if is_armor else f"仅兑换勾选商品，每项单独设置{period}份数；默认不选。超过限购或现有资产可兑换数量时按游戏MAX兑换，售罄跳过。名称中*10/*100表示一份内含数量，同名商品按币种、价格和图片复核。" + ("后勤按周累计，周一北京时间05:00换周。" if weekly else "每日目标按北京时间05:00换日。") + "重复运行只补未完成数量。")
        tasks.append({"name": task_name, "label": label, "entry": entry, "group": ["作战" if is_armor else "兑换"],
                      "default_check": False, "description": description, "option": choice_keys, "pipeline_override": reset})
        pipeline[entry] = {"recognition": "DirectHit", "action": {"type": "Custom", "param": {
            "custom_action": "CangwuTask", "custom_action_param": {"task": task_name}}}, "max_hit": 1,
            "focus": {"Node.Action.Starting": f"开始：{label}", "Node.Action.Succeeded": f"完成：{label}",
                      "Node.Action.Failed": f"已停止：{label}，请查看日志"}}
    interface["task"] = [t for t in interface["task"] if t["name"] != "armor" and not t["name"].startswith("shop_")]
    position = next(i for i, t in enumerate(interface["task"]) if t["name"] == "event")
    interface["task"][position:position] = tasks
    if not any(g["name"] == "兑换" for g in interface["group"]):
        interface["group"].insert(1, {"name": "兑换"})
    interface["version"] = "0.4.0"
    interface_path.write_text(json.dumps(interface, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pipeline_path.write_text(json.dumps(pipeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 商店商品清单", "", "实机核对日期：2026-10-17。逐商品单独勾选并填写目标份数（1～999）；超过库存或已有资产可兑上限时按游戏MAX兑换，售罄跳过。", "", "后勤按每周目标累计，周一北京时间05:00换周；其余商店保留每日目标，05:00换日。同周期重跑只补未完成数量，手动购买以游戏剩余库存为准。调度商店已移除。", "", "货币标签按实机图标区分，价格或商品变化时停止并重新核对。名称中的*10、*100等表示每份内含数量。", ""]
    for shop, spec in SHOPS.items():
        lines.extend(["## " + spec["label"], "", "| 商品 | 每份价格 | 对应货币 |", "| --- | ---: | --- |"])
        lines.extend(f"| {item['name']} | {item['price']} | {CATALOG['currency_labels'][item['currency']]} |" for item in CATALOG[shop])
        lines.append("")
    (ROOT / "docs/商店商品清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
