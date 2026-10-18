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
        self.failed = False

    def run(self, context, argv) -> bool:
        if self.failed:
            print("上一个任务已停止，本轮后续任务已跳过。", flush=True)
            return False
        ctx = None
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
            self.failed = True
            if ctx is not None:
                stopped = ctx.stop.is_set() or ctx.stop_file.exists() or context.tasker.stopping
                status = "stopped" if stopped else "needs_configuration" if isinstance(exc, NeedsSetup) else "failed"
                ctx.result("任务停止", status, str(exc))
                ctx.finish(status)
            else:
                print(f"任务参数错误：{exc}", flush=True)
            if not isinstance(exc, (Halt, NeedsSetup, DeviceError)):
                traceback.print_exc()
            # MXU may submit all entries up front. Keep failure sticky for the
            # lifetime of this Agent, so subsequent entries perform no input.
            return False
        finally:
            if ctx is not None:
                self.actions += ctx.actions
