"""Prepare the game once before a daily queue, using the bound Maa controller."""
from __future__ import annotations

import time
import numpy as np

from .device import DeviceError
from .runtime import Context, Halt, NeedsSetup
from .vision import Frame, Word

START_TEXTS = ("TOUCH TO START", "开始游戏", "进入游戏", "点击开始", "点击进入游戏")
START_REGION = (80, 220, 1200, 719)


def start_button(frame: Frame) -> Word | None:
    if frame.is_loading() or frame.paid_dialog() or frame.reward():
        return None
    return frame.find(*START_TEXTS, region=START_REGION, exact=True)


def manual_step(frame: Frame) -> str | None:
    region = (230, 120, 1050, 625)
    if frame.has("实名认证", "请输入验证码", "短信验证码", "账号登录", "手机登录", "扫码登录", region=region):
        return "请先在游戏中完成登录或认证，再重新开始任务。"
    if frame.has("资源更新", "资源下载", "更新游戏", "立即更新", region=region):
        return "请先在游戏中完成资源下载或更新，再重新开始任务。"
    if frame.has("同意并继续", "请阅读并同意", region=region):
        return "请先在游戏中确认账号协议，再重新开始任务。"
    return None


def signin_page(frame: Frame) -> bool:
    return (frame.has("签到", region=(0, 500, 220, 660), exact=True)
            and frame.has("月签到奖励", region=(210, 620, 600, 719))
            and frame.has("月累计签到", region=(1000, 650, 1279, 719)))


def startup_loading(frame: Frame) -> bool:
    # Android's status bar can contain readable text over a black splash screen.
    content = frame.image[80:650, 70:1220]
    return frame.is_loading() or (content.size > 0 and float(np.mean(np.all(content < 12, axis=2))) > .995)


def enter_game(ctx: Context, first: Frame | None = None, *, timeout: float = 120) -> Frame:
    deadline = min(ctx.deadline, time.monotonic() + timeout)
    frame = first
    entered = closed_notice = closed_signin = False
    while time.monotonic() < deadline:
        ctx.check()
        frame = frame if frame is not None else ctx.frame()
        if frame.is_home():
            ctx.log("苍雾世界已进入大厅。")
            return frame
        if message := manual_step(frame):
            ctx.save("startup_manual_step", frame)
            raise NeedsSetup(message)
        button = start_button(frame)
        if frame.reward():
            ctx.dismiss_reward(frame)
            closed_signin = False
        elif button is not None and not entered:
            ctx.tap(frame, button.center, "点击开始游戏，等待进入大厅", pause=.6)
            entered = True
        elif not frame.is_loading() and frame.all("活动公告", "游戏公告") and not closed_notice:
            ctx.tap(frame, (640, 692), "关闭登录公告", pause=.6)
            closed_notice = True
            # On this game's cold start, the notice can appear after the first
            # start tap. Closing it returns to the title and needs one more tap.
            entered = False
        elif not frame.is_loading() and signin_page(frame) and not closed_signin:
            ctx.tap(frame, (25, 38), "关闭登录签到页面，返回大厅", pause=.6)
            closed_signin = True
        else:
            ctx.pause(.6)
        frame = None
    ctx.save("startup_timeout", frame or ctx.last)
    raise Halt("等待游戏进入大厅超时。请检查登录、更新或网络提示后重新开始任务。")


def prepare_game(ctx: Context, *, foreground_timeout: float = 25) -> None:
    ctx.check()
    if ctx.device.foreground() == ctx.device.package:
        frame = ctx.frame()
        if message := manual_step(frame):
            ctx.save("startup_manual_step", frame)
            raise NeedsSetup(message)
        if frame.reward():
            return
        if start_button(frame) is not None or startup_loading(frame) or signin_page(frame):
            enter_game(ctx, frame)
        # Leave existing in-game pages intact so expedition, shop, and armor
        # tasks can reconcile an unfinished operation before navigating away.
        return
    ctx.log("正在打开苍雾世界。")
    ctx.check()
    ctx.device.launch()
    ctx.actions += 1
    deadline = min(ctx.deadline, time.monotonic() + foreground_timeout)
    while time.monotonic() < deadline:
        ctx.check()
        try:
            foreground = ctx.device.foreground()
        except DeviceError:
            foreground = None
        if foreground == ctx.device.package:
            enter_game(ctx)
            return
        ctx.pause(.5)
    raise Halt("游戏尚未进入前台。请检查游戏是否需要登录，再重新开始任务。")
