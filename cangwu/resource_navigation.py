"""Guarded navigation shared by the resource dungeons."""
from __future__ import annotations

import time

from .runtime import Context, Halt
from .vision import Frame


def check_connection(frame: Frame) -> None:
    if frame.has("网络连接异常", "网络异常", "连接失败", "重新连接", "重新登录", "重试",
                 region=(250, 180, 1080, 540)):
        raise Halt("游戏连接异常，不能视作副本未开放；没有继续作战。")


def resource_page(frame: Frame) -> bool:
    return (frame.has("战斗副本", region=(180, 0, 520, 90))
            and frame.has("装甲进阶", region=(900, 450, 1150, 600))
            and frame.has("核心制造", region=(420, 450, 630, 600))
            and not frame.is_loading() and not frame.reward())


def open_resources(ctx: Context) -> Frame:
    frame = ctx.home()
    ctx.text(frame, "作战", region=(1020, 620, 1279, 719), exact=True)
    frame = ctx.wait(lambda f: f.has("战斗副本", region=(180, 0, 520, 90)), "作战页面")
    for attempt in range(2):
        check_connection(frame)
        ctx.text(frame, "资源", region=(450, 75, 655, 195), exact=True,
                 reason="点击资源分页" if not attempt else "资源分页未响应，核对原页面后补点一次")
        until = min(ctx.deadline, time.monotonic() + 8)
        while time.monotonic() < until:
            frame = ctx.frame()
            check_connection(frame)
            if resource_page(frame):
                return frame
            ctx.pause(.6)
        if (not attempt and not frame.is_loading() and not frame.reward()
                and frame.has("战斗副本", region=(180, 0, 520, 90))
                and frame.has("当前进度", region=(350, 230, 560, 320))
                and frame.has("资源", region=(450, 75, 655, 195), exact=True)):
            continue
        ctx.save("resource_no_response", frame)
        raise Halt("资源分页未切换，已停止操作；没有进入或消耗副本次数。")
    raise Halt("资源分页未切换。")
