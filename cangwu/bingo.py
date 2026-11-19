"""Claim the four free mission rewards on the Bingo Pilgrimage's right side."""
from __future__ import annotations

from .runtime import Context, Halt
from .vision import Frame


TITLE = "宾果巡礼"
TABS = (0, 150, 215, 650)
# Label, required progress, and top of the right-hand reward card (1280x720).
MISSIONS = (("每日登录", 1, 240), ("通关战斗关卡5次", 5, 340),
            ("消耗100体力", 100, 440), ("消耗200体力", 200, 540))
DISMISS = (1260, 660)  # Blank margin below the right-hand reward cards.


def bingo_page(frame: Frame) -> bool:
    if frame.has("网络连接异常", "网络异常", "连接失败", "重新连接", "重新登录", "重试",
                 region=(250, 180, 1080, 540)):
        raise Halt("宾果巡礼出现连接异常，停止领取。")
    return (not frame.is_loading() and not frame.paid_dialog() and not frame.reward()
            and frame.has(TITLE, region=TABS, confidence=.9)
            and frame.has(TITLE, region=(875, 100, 1245, 200), confidence=.9)
            and all(frame.has(label, region=(890, top, 1045, top + 40), exact=True, confidence=.9)
                    for label, _, top in MISSIONS))


def mission_state(frame: Frame, mission: tuple[str, int, int]) -> tuple[bool, bool]:
    """Return (condition reached, already claimed) from this mission's own row."""
    if not bingo_page(frame):
        raise Halt("当前不是已确认的宾果巡礼任务页面，停止领取。")
    label, goal, top = mission
    progress = frame.number_fraction((890, top + 40, 1030, top + 78))
    if progress is None or progress[1] != goal:
        raise Halt(f"宾果巡礼「{label}」进度未读清，停止领取。")
    claimed = frame.has("已领取", region=(1020, top, 1140, top + 80), exact=True, confidence=.9)
    return progress[0] >= goal, claimed


def open_bingo(ctx: Context) -> Frame | None:
    frame = ctx.home()
    ctx.text(frame, "活动", region=(1150, 170, 1279, 260), exact=True, reason="打开活动合集")
    frame = ctx.wait(lambda f: f.activity_hub(), "活动合集页面")
    tab = frame.find(TITLE, region=TABS, confidence=.9)
    # Inspect both directions before treating a limited-time entry as absent.
    for start, end in (((105, 300), (105, 600)), ((105, 600), (105, 250))):
        if tab is not None:
            break
        ctx.swipe(frame, start, end, "翻动活动列表，查找宾果巡礼", duration=600)
        frame = ctx.wait(lambda f: f.activity_hub(), "活动合集页面")
        tab = frame.find(TITLE, region=TABS, confidence=.9)
    if tab is None:
        ctx.result(TITLE, "already_done", "活动合集里没有宾果巡礼，限时活动可能已结束，跳过。", frame)
        return None
    ctx.tap(frame, tab.center, "切换到宾果巡礼")
    return ctx.wait(bingo_page, "宾果巡礼任务列表")


def bingo(ctx: Context) -> None:
    frame = open_bingo(ctx)
    if frame is None:
        return
    ctx.save("bingo_before", frame)
    # Read all four rows before the first claim, so missing progress never
    # starts a partially understood sequence.
    for mission in MISSIONS:
        mission_state(frame, mission)
    claimed = 0
    for mission in MISSIONS:
        reached, already_claimed = mission_state(frame, mission)
        if not reached or already_claimed:
            continue
        label, _, top = mission
        ctx.save("bingo_" + label + "_before", frame)
        ctx.tap(frame, (1220, top + 40), "领取宾果巡礼：" + label, pause=.6)
        frame = ctx.wait(lambda f: bingo_page(f) or f.reward(), "宾果巡礼领取反馈", timeout=8)
        ctx.save("bingo_reward_icon", frame)
        # The game can show only the reward icon, without a reward title.
        # After this one claim, tap the observed safe margin once, then verify
        # the claimed marker. Never dismiss by touching the central card grid.
        ctx.tap(frame, DISMISS, "收起宾果巡礼奖励图标", pause=.5)
        frame = ctx.wait(lambda f: bingo_page(f) and mission_state(f, mission)[1],
                         "宾果巡礼「" + label + "」已领取", timeout=8)
        ctx.save("bingo_" + label + "_verified", frame)
        claimed += 1
    ctx.result(TITLE, "completed" if claimed else "already_done",
               f"已领取右侧 {claimed} 项达标奖励。" if claimed else "右侧奖励已领取或条件尚未达成，跳过。", frame)
