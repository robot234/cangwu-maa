"""Live previews and skip paths only; never submits an exchange or sweep."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cangwu.mxu_api import discover, request
from cangwu.feature_config import CATALOG, SHOPS, item_node


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("empty", "armor-full", "shops-preview", "shop-preview", "cap-preview", "catalog-preview"), required=True)
    parser.add_argument("--shop", choices=tuple(SHOPS))
    parser.add_argument("--pick", action="append", default=[], help="商品ID=目标份数；只预览后取消")
    args = parser.parse_args()
    api = discover(ROOT)
    pi = request(api, "/interface")["interface"]
    instance = request(api, "/config")["instances"][0]
    instance_id = instance["id"]
    path = f"/maa/instances/{instance_id}"
    state = request(api, "/maa/state")["instances"][instance_id]
    if state["is_running"] or not state["connected"] or not state["resource_loaded"]:
        raise RuntimeError("助手未空闲、连接或加载新版资源。")
    definitions = {task["name"]: task for task in pi["task"]}
    saved = {task["taskName"]: task for task in instance["tasks"]}
    if args.case == "empty":
        plans = [(key, None, {}) for key in (*[f"shop_{name}" for name in SHOPS], "armor")]
    elif args.case == "armor-full":
        plans = [("armor", "armor", {"niai": 2, "sobek": 3})]
    elif args.case == "catalog-preview":
        plans = [("shop_elite", "elite", {"core_crystal10": 2, "crystal3": 1, "crystal2": 1, "crystal1": 1})]
    elif args.case == "shop-preview":
        if not args.shop or not args.pick:
            raise ValueError("单店预览需指定 --shop 与 --pick 商品ID=数量")
        choices = {}
        ids = {item["id"] for item in CATALOG[args.shop]}
        for value in args.pick:
            item_id, quantity = value.split("=", 1)
            if item_id not in ids or not quantity.isascii() or not quantity.isdecimal() or not 1 <= int(quantity) <= 999:
                raise ValueError("商品或数量不在允许范围")
            choices[item_id] = int(quantity)
        plans = [(f"shop_{args.shop}", args.shop, choices)]
    else:
        quantity = 999 if args.case == "cap-preview" else 2
        plans = [("shop_logistics", "logistics", {"core_block": quantity}),
                 ("shop_union", "union", {"core_crystal10": quantity}),
                 ("shop_master", "master", {"target_ticket": quantity})]
    tasks = []
    for key, feature, choices in plans:
        definition = definitions[key]
        override = copy.deepcopy(definition["pipeline_override"])
        for item, quantity in choices.items():
            override[item_node(feature, item)]["action"]["param"]["custom_action_param"] = {"enabled": True, "quantity": quantity}
        params = {"task": "feature_preview", "feature": feature} if feature else {"task": key}
        override[definition["entry"]] = {"action": {"type": "Custom", "param": {"custom_action": "CangwuTask", "custom_action_param": params}}}
        tasks.append({"entry": definition["entry"], "pipeline_override": json.dumps(override, ensure_ascii=False),
                      "selected_task_id": saved[key]["id"], "task_name": "只预览不提交 · " + key})
    before = set((ROOT / "runtime/runs").glob("*/report.json"))
    body = {"tasks": tasks, "agent_configs": [{"child_exec": str(ROOT / ".venv/Scripts/python.exe"), "child_args": ["-u", str(ROOT / "agent/main.py")]}],
            "cwd": str(ROOT / "app"), "reset_state": True}
    print(request(api, path + "/tasks/start", body))
    deadline = time.monotonic() + 15 * 60
    last = None
    while time.monotonic() < deadline:
        current = request(api, "/maa/state")["instances"][instance_id]
        status = current["task_run_state"]
        if status != last:
            print(json.dumps(status, ensure_ascii=False), flush=True)
            last = status
        if not current["is_running"] and status.get("overall_status") not in (None, "Running"):
            reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(set((ROOT / "runtime/runs").glob("*/report.json")) - before)]
            suffix = "-" + args.shop if args.shop else ""
            output = ROOT / f"runtime/feature-live-{args.case}{suffix}.json"
            output.write_text(json.dumps({"case": args.case, "state": current, "reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(output)
            try:
                request(api, path + "/agent/stop", {}, timeout=5)
            except OSError as exc:
                print(f"Agent cleanup request failed after completion: {exc}")
            good = len(reports) == len(tasks) and all(report["status"] == "completed" for report in reports)
            if args.case == "empty":
                good = good and all(report["actions"] == 0 for report in reports)
            return 0 if good and status["overall_status"] == "Succeeded" else 1
        time.sleep(1)
    request(api, path + "/tasks/stop", {})
    raise TimeoutError("实机预览超时，已请求停止；不推断未核对的结果。")


if __name__ == "__main__":
    raise SystemExit(main())
