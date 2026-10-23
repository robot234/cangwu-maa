from __future__ import annotations

import copy
import json
import threading
import traceback
import time
from maa.custom_action import CustomAction
from .device import DeviceError
from .runtime import Context, Halt, NeedsSetup, load_config
from .tasks import TASKS
from .feature_config import SHOPS, SHOP_TASKS
from .vision import Vision
from .startup import prepare_game


def task_config(params: dict) -> dict:
    config = copy.deepcopy(load_config())
    for group, key, low, high in (
        ("event", "reserve_stamina", 0, 99999),
        ("arena", "max_tickets", 0, 15),
        ("expedition", "hours", 4, 12),
    ):
        if key in params:
            value = int(params[key])
            if not low <= value <= high or (key == "hours" and value not in (4, 8, 12)):
                raise ValueError(f"{key} 参数超出允许范围。")
            config[group][key] = value
    return config


class CangwuTask(CustomAction):
    def __init__(self):
        super().__init__()
        self.started = time.monotonic()
        self.actions = 0
        # failed: stop every later entry. recover: the previous entry failed
        # on its own, so return to the lobby before the next entry continues.
        self.failed = False
        self.recover = False

    def run(self, context, argv) -> bool:
        if self.failed:
            print("上一个任务已停止，本轮后续任务已跳过。", flush=True)
            return False
        ctx = None
        key = None
        recovering = False
        try:
            params = json.loads(argv.custom_action_param or "{}")
            key = params["task"]
            special = {"startup": "开始游戏", "feature_preview": "功能预览", "inspect": "页面检查", "check": "检查连接"}
            name, task = (special[key], None) if key in special else next((name, fn) for task_key, name, fn in TASKS if task_key == key)
            ctx = Context(task_config(params), threading.Event(), lambda line: print(line, flush=True), maa=context)
            ctx.deadline = self.started + int(ctx.config["limits"]["run_minutes"]) * 60
            ctx.config["limits"]["max_actions"] = max(0, int(ctx.config["limits"]["max_actions"]) - self.actions)
            ctx.vision = Vision(context)
            ctx.check()
            ctx.device.connect()
            if self.recover and key != "check":
                recovering = True
                ctx.log("上一个任务没有完成，先回到大厅再继续后面的任务。")
                ctx.home()
                recovering = False
                self.recover = False
            if key == "startup":
                prepare_game(ctx)
                ctx.result(name, "completed", "游戏已就绪，继续执行后续勾选任务。", ctx.last)
            elif key == "inspect":
                from .inspection import inspect_ui
                inspect_ui(ctx, params)
            elif key == "feature_preview":
                feature = params.get("feature")
                if feature in SHOPS:
                    from .shops import exchange
                    exchange(ctx, feature, preview_only=True)
                elif feature == "armor":
                    from .armor import armor
                    armor(ctx, preview_only=True)
                else:
                    raise Halt("未知的新功能预览项目。")
                if ctx.actions:
                    ctx.home()
            elif key == "check":
                frame = ctx.frame()
                ctx.result(name, "completed", f"Maa 截图和 OCR 正常，识别到 {len(frame.words)} 段文字；" + ("当前为大厅。" if frame.is_home() else "当前页面请查看截图。"), frame)
            else:
                # These tasks reconcile their own pages before navigating away.
                if key not in ("expedition", "armor", "arena", *SHOP_TASKS):
                    ctx.home()
                task(ctx)
                if ctx.actions:
                    ctx.home()
            ctx.finish("completed")
            return True
        except Exception as exc:
            if ctx is not None:
                stopped = ctx.stop.is_set() or ctx.stop_file.exists() or context.tasker.stopping
                exhausted = time.monotonic() >= ctx.deadline or ctx.actions >= int(ctx.config["limits"]["max_actions"])
                status = "stopped" if stopped else "needs_configuration" if isinstance(exc, NeedsSetup) else "failed"
                ctx.result("任务停止", status, str(exc))
                ctx.finish(status)
            else:
                print(f"任务参数错误：{exc}", flush=True)
            if not isinstance(exc, (Halt, NeedsSetup, DeviceError)):
                traceback.print_exc()
            # MXU may submit all entries up front. A user stop, lost device,
            # failed startup, exhausted budget, or failed lobby recovery stays
            # sticky for this Agent, so later entries perform no input. Any
            # other single-task failure lets later entries continue from the
            # lobby; per-task ledgers still block repeating unconfirmed input.
            if (ctx is None or stopped or exhausted or recovering or key == "startup"
                    or isinstance(exc, DeviceError)):
                self.failed = True
            else:
                self.recover = True
                print("这一项没有完成；后面的任务会先回到大厅再继续。", flush=True)
            return False
        finally:
            if ctx is not None:
                self.actions += ctx.actions
