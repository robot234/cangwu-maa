from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
from launcher import environment, prepare_adb, settings


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 MaaFramework 原生任务器检查或运行任务")
    parser.add_argument("--tasks", default="check", help="check 或 base,expedition,mail,friends,event,arena,missions,pass")
    args = parser.parse_args()
    os.environ.update(environment())
    adb = prepare_adb(dict(os.environ))
    from maa.library import Library
    Library.open(ROOT / "app/maafw")
    from maa.toolkit import Toolkit
    from maa.resource import Resource
    from maa.controller import AdbController
    from maa.tasker import Tasker
    from maa.define import MaaAdbInputMethodEnum, MaaAdbScreencapMethodEnum
    from cangwu.device import Device
    from cangwu.maa_action import CangwuTask
    from cangwu.runtime import RunLock

    config = settings()
    with RunLock():
        (ROOT / "runtime/STOP").unlink(missing_ok=True)
        Toolkit.init_option(ROOT / "runtime/maa", {"logging": True, "stdout_level": 2, "save_draw": False})
        resource = Resource()
        resource.register_custom_action("CangwuTask", CangwuTask())
        Device.completed(resource.post_bundle(ROOT / "app/resource"), "加载资源")
        controller = AdbController(adb, config["device"]["serial"],
            screencap_methods=MaaAdbScreencapMethodEnum.Encode,
            input_methods=MaaAdbInputMethodEnum.AdbShell)
        controller.set_screenshot_target_short_side(720)
        Device.completed(controller.post_connection(), "连接 MuMu")
        tasker = Tasker()
        tasker.bind(resource, controller)
        entries = {"check": "CangwuCheck", "base": "CangwuBase", "expedition": "CangwuExpedition", "mail": "CangwuMail", "friends": "CangwuFriends", "event": "CangwuEvent", "arena": "CangwuArena", "missions": "CangwuMissions", "pass": "CangwuPass"}
        for key in args.tasks.split(","):
            if key not in entries:
                raise ValueError(f"未知任务：{key}")
            job = tasker.post_task(entries[key])
            import time
            deadline = time.monotonic() + 21 * 60
            while not job.done:
                if time.monotonic() >= deadline:
                    tasker.post_stop()
                    raise TimeoutError("Maa 任务超时，已发送停止指令。")
                time.sleep(.2)
            print(json.dumps({"task": key, "maa_status": "succeeded" if job.succeeded else "failed", "task_id": job.job_id}, ensure_ascii=False), flush=True)
            if not job.succeeded:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
