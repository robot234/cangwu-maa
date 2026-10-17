"""Run a selected set through the running MXU host and its real Agent channel."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cangwu.mxu_api import discover
from cangwu.feature_config import SHOP_TASKS, saved_feature_overrides
API = "http://127.0.0.1:12701/api"


def api(path, data=None):
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    request = Request(API + path, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    global API
    API = discover(ROOT)
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="check")
    args = parser.parse_args()
    pi = api("/interface")
    if pi["interface"]["name"] != "CangwuMaa" or Path(pi["basePath"]).resolve() != (ROOT / "app").resolve():
        raise RuntimeError("不是当前项目的 MXU，停止验收。")
    config = api("/config")
    instance = config["instances"][0]
    instance_id = instance["id"]
    path = f"/maa/instances/{instance_id}"
    state = api("/maa/state")["instances"][instance_id]
    if state["is_running"] or not state["connected"]:
        raise RuntimeError("窗口还在运行任务或未连接。")
    if not state["resource_loaded"]:
        print(api(path + "/resource/load", {"paths": [str(ROOT / "app/resource")]}))
        deadline = time.monotonic() + 40
        while not api("/maa/state")["instances"][instance_id]["resource_loaded"]:
            if time.monotonic() >= deadline:
                raise TimeoutError("MXU 资源加载超时。")
            time.sleep(.5)
    definitions = {task["name"]: task for task in pi["interface"]["task"]}
    saved = {task["taskName"]: task for task in instance["tasks"]}
    tasks = []
    for name in args.tasks.split(","):
        definition = definitions[name]
        params = {"task": name}
        values = saved[name]["optionValues"]
        if name == "event":
            params["reserve_stamina"] = int(values["reserve_stamina"]["values"]["amount"])
        if name == "arena":
            params["max_tickets"] = int(values["arena_limit"]["values"]["amount"])
        if name == "expedition":
            params["hours"] = int(values["expedition_hours"]["caseName"].removesuffix("h"))
        override = saved_feature_overrides(definition, values) if name in (*SHOP_TASKS, "armor") else {}
        override[definition["entry"]] = {"action": {"type": "Custom", "param": {"custom_action": "CangwuTask", "custom_action_param": params}}}
        tasks.append({"entry": definition["entry"], "pipeline_override": json.dumps(override, ensure_ascii=False), "selected_task_id": saved[name]["id"], "task_name": name})
    body = {"tasks": tasks, "agent_configs": [{"child_exec": str(ROOT / ".venv/Scripts/python.exe"), "child_args": ["-u", str(ROOT / "agent/main.py")]}], "cwd": str(ROOT / "app"), "pi_envs": {"PI_CLIENT_NAME": "MXU", "PI_CLIENT_VERSION": "2.7.1"}, "reset_state": True}
    before_reports = set((ROOT / "runtime/runs").glob("*/report.json"))
    print(api(path + "/tasks/start", body))
    last = None
    deadline = time.monotonic() + 21 * 60
    while True:
        current = api("/maa/state")["instances"][instance_id]
        report = current["task_run_state"]
        if report != last:
            print(json.dumps(report, ensure_ascii=False))
            last = report
        if not current["is_running"] and report.get("overall_status") is not None:
            destination = ROOT / "runtime" / f"mxu_{time.strftime('%Y%m%d_%H%M%S')}.json"
            reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(set((ROOT / "runtime/runs").glob("*/report.json")) - before_reports)]
            destination.write_text(json.dumps({"mxu": current, "task_reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"MXU report: {destination}")
            valid = report["overall_status"].lower() in ("completed", "succeeded") and len(reports) == len(tasks) and all(r["status"] == "completed" for r in reports)
            return 0 if valid else 1
        if time.monotonic() > deadline:
            api(path + "/tasks/stop", {})
            raise TimeoutError("MXU 执行超时，已发送停止指令。")
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
