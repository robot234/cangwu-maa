"""Inspect a known navigation entry through the running MXU and guarded Agent."""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cangwu.mxu_api import discover
API = "http://127.0.0.1:12701/api"


def api(path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    with urlopen(Request(API + path, data=data, headers={"Content-Type": "application/json"}), timeout=30) as response:
        return json.load(response)


def main():
    global API
    API = discover(ROOT)
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", action="store_true")
    parser.add_argument("--page-right", action="count", default=0)
    parser.add_argument("--page-left", action="count", default=0)
    parser.add_argument("--menu-down", action="count", default=0)
    parser.add_argument("--route", choices=("elite", "logistics", "union", "master", "combat", "recommend"))
    parser.add_argument("--item", default="")
    parser.add_argument("--back", action="store_true")
    parser.add_argument("--armor-preview", action="store_true")
    parser.add_argument("--armor-rules", action="store_true")
    parser.add_argument("--shop-rules", action="store_true")
    parser.add_argument("--text", default="")
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--region", nargs=4, type=int)
    args = parser.parse_args()
    interface = api("/interface")
    if interface["interface"]["name"] != "CangwuMaa" or Path(interface["basePath"]).resolve() != (ROOT / "app").resolve():
        raise RuntimeError("Not this project's MXU.")
    instance = api("/config")["instances"][0]
    instance_id = instance["id"]
    selected_task_id = next(task["id"] for task in instance["tasks"] if task["taskName"] == "check")
    path = f"/maa/instances/{instance_id}"
    state = api("/maa/state")["instances"][instance_id]
    if state["is_running"] or not state["connected"] or not state["resource_loaded"]:
        raise RuntimeError("The game is busy or not connected/resource-loaded.")
    before = set((ROOT / "runtime/runs").glob("*/report.json"))
    params = {"task": "inspect", "home": args.home, "text": args.text, "require": args.require, "region": args.region, "page_right": args.page_right, "page_left": args.page_left, "item": args.item, "back": args.back, "route": args.route, "armor_preview": args.armor_preview, "armor_rules": args.armor_rules, "menu_down": args.menu_down, "shop_rules": args.shop_rules}
    override = {"CangwuCheck": {"action": {"type": "Custom", "param": {"custom_action": "CangwuTask", "custom_action_param": params}}}}
    body = {
        "tasks": [{"entry": "CangwuCheck", "pipeline_override": json.dumps(override, ensure_ascii=False), "task_name": "页面检查", "selected_task_id": selected_task_id}],
        "agent_configs": [{"child_exec": str(ROOT / ".venv/Scripts/python.exe"), "child_args": ["-u", str(ROOT / "agent/main.py")]}],
        "cwd": str(ROOT / "app"), "reset_state": True,
    }
    print(api(path + "/tasks/start", body))
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = api("/maa/state")["instances"][instance_id]
        if not state["is_running"] and state["task_run_state"].get("overall_status") not in (None, "Running"):
            api(path + "/agent/stop", {})
            reports = sorted(set((ROOT / "runtime/runs").glob("*/report.json")) - before)
            for report in reports:
                print(report)
            return 0 if len(reports) == 1 and json.loads(reports[0].read_text(encoding="utf-8"))["status"] == "completed" else 1
        time.sleep(.5)
    api(path + "/tasks/stop", {})
    raise TimeoutError("Inspection exceeded 120 seconds; stop requested.")


if __name__ == "__main__":
    raise SystemExit(main())
