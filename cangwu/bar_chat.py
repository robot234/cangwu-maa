"""Choose one of the three drink cans in Bar Chitchat."""
from .runtime import Context, ROOT, Halt, NeedsSetup
from .vision import Frame, template_match
from .feature_config import load_state, save_state, game_day
from .limited_events import open_activity


TITLE = "酒吧闲谈"


def bar_page(frame: Frame) -> bool:
    return (not frame.is_loading() and not frame.paid_dialog() and not frame.reward()
            and frame.has(TITLE, region=(0, 150, 215, 650), exact=True, confidence=.9)
            and frame.has(TITLE, region=(520, 40, 950, 190), exact=True, confidence=.8))


def request_drink(ctx: Context, frame: Frame, state: dict, path) -> Frame:
    if not bar_page(frame):
        raise Halt("没有确认酒吧闲谈页面，未选择罐子。")
    point = template_match(frame, ROOT / "assets/bar_chat_can.png", (640, 520, 820, 720), .9)
    if point is None:
        raise Halt("酒吧闲谈中间的罐子未识别，未点击。")
    state["pending"] = {"day": game_day(), "evidence": ctx.save("bar_chat_before", frame)}
    save_state(path, state)
    ctx.tap(frame, point, "酒吧闲谈选择中间一个罐子")
    return ctx.wait(lambda f: True, "酒吧闲谈选择反馈")


def participated(frame: Frame) -> bool:
    return bar_page(frame) and frame.has("今日已参与活动", region=(500, 580, 1000, 690), exact=True, confidence=.9)


def finish_drink(ctx: Context, state: dict, path) -> None:
    pending = state["pending"]
    frame = ctx.wait(lambda f: f.reward() and f.has(TITLE, region=(0, 150, 215, 650), exact=True, confidence=.9)
                     or (pending.get("reward_evidence") and participated(f)),
                     "酒吧闲谈奖励", timeout=12)
    if frame.reward():
        pending["reward_evidence"] = ctx.save("bar_chat_reward", frame)
        save_state(path, state)
        ctx.dismiss_reward(frame)
        frame = ctx.wait(participated, "酒吧闲谈今日已参与")
    state["last"] = {**pending, "after_evidence": ctx.save("bar_chat_verified", frame)}
    state["pending"] = None
    save_state(path, state)
    ctx.result(TITLE, "completed", "已选择中间一个罐子并核对奖励，今日已参与活动。", frame)


def bar_chat(ctx: Context) -> None:
    path = ROOT / "runtime/state/bar_chat.json"
    state = load_state(path, {"version": 1, "pending": None, "last": None})
    if state.get("version") != 1 or "pending" not in state:
        raise NeedsSetup("酒吧闲谈记录格式无效。")
    if state["pending"] is not None:
        finish_drink(ctx, state, path)
        return
    today = game_day()
    if state.get("last") is not None and state["last"]["day"] == today:
        ctx.result(TITLE, "already_done", "今日已完成酒吧闲谈，跳过。")
        return
    frame = open_activity(ctx, TITLE, bar_page)
    if frame is None:
        return
    if participated(frame):
        state["last"] = {"day": today, "after_evidence": ctx.save("bar_chat_already_done", frame)}
        save_state(path, state)
        ctx.result(TITLE, "already_done", "游戏显示今日已参与活动，跳过。", frame)
        return
    request_drink(ctx, frame, state, path)
    finish_drink(ctx, state, path)
