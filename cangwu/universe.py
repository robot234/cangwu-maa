"""Use only the Cosmic Tournament's on-screen free matches."""
from __future__ import annotations

from datetime import datetime
import re

import cv2

from .feature_config import load_state, save_state, game_day
from .runtime import Context, ROOT, CST, Halt, NeedsSetup
from .vision import Frame, normalized
from .limited_events import open_activity


TITLE = "宇宙武道会"
COUNTER = (900, 575, 1160, 625)
MISSION_TABS = {"每日任务": (390, 45, 640, 110), "当期任务": (660, 45, 920, 110)}


def tournament_page(frame: Frame) -> bool:
    return (not frame.is_loading() and not frame.paid_dialog() and not frame.reward()
            and frame.has(TITLE, region=(240, 0, 435, 85), exact=True, confidence=.9)
            and frame.has("编辑队伍", region=(760, 480, 1000, 555), exact=True)
            and frame.has("活动任务", region=(50, 620, 260, 710), exact=True)
            and not frame.has("每日任务", region=(390, 45, 640, 110), exact=True))


def free_matches(frame: Frame) -> int:
    if not tournament_page(frame):
        raise Halt("没有确认宇宙武道会主页面，未匹配。")
    words = frame.words
    for attempt in range(2):
        for word in words:
            x, y = word.center
            if word.confidence < .9 or not (COUNTER[0] <= x <= COUNTER[2] and COUNTER[1] <= y <= COUNTER[3]):
                continue
            match = re.fullmatch(r"(?:今日|今)?免费匹配次数[:：](\d+)", normalized(word.text))
            if match and 0 <= int(match[1]) <= 5:
                return int(match[1])
        if attempt == 0 and frame.vision is not None:
            words = frame.vision.region_words(frame, COUNTER)
        else:
            break
    raise Halt("宇宙武道会免费匹配次数未读清，未匹配或购买次数。")


def open_tournament(ctx: Context) -> Frame | None:
    frame = open_activity(ctx, TITLE, lambda f: f.has(TITLE, region=(450, 120, 1000, 250), exact=True, confidence=.9))
    if frame is None:
        return None
    ctx.text(frame, "点击进入", region=(970, 480, 1220, 650), exact=True)
    return ctx.wait(tournament_page, TITLE + "主页面")


def request_match(ctx: Context, frame: Frame, state: dict, path) -> Frame:
    remaining = free_matches(frame)
    if not remaining:
        raise Halt("今日免费匹配次数已用完，未点击匹配。")
    state["pending"] = {"before_remaining": remaining, "day": game_day(),
                        "requested_at": datetime.now(CST).isoformat(),
                        "evidence": ctx.save("universe_match_before", frame)}
    save_state(path, state)
    ctx.text(frame, "匹配", region=(870, 625, 1160, 710), exact=True,
             reason=f"宇宙武道会使用一次免费匹配（剩余{remaining}次）")
    return ctx.wait(lambda f: True, "宇宙武道会匹配反馈")


def battle_result(frame: Frame) -> bool:
    return (not frame.is_loading() and not frame.paid_dialog()
            and frame.has("战斗胜利", "战斗失败", region=(90, 40, 500, 160), confidence=.9)
            and frame.has("回战", region=(560, 200, 740, 285), confidence=.9)
            and frame.all("二回战", "战斗总分")
            and frame.has("确认", region=(920, 600, 1210, 690), exact=True, confidence=.9))


def skip_confirmation(frame: Frame) -> bool:
    return (not frame.is_loading() and not frame.paid_dialog()
            and frame.has("是否跳过此次战斗", region=(400, 270, 880, 350), confidence=.9)
            and frame.has("直接显示结果", region=(400, 270, 880, 350), confidence=.9)
            and frame.has("取消", region=(350, 385, 595, 460), exact=True, confidence=.9)
            and frame.has("确认", region=(700, 385, 920, 460), exact=True, confidence=.9))


def finish_match(ctx: Context, state: dict, path) -> Frame:
    pending = state["pending"]
    skip_region = (1160, 140, 1279, 250)
    frame = ctx.wait(lambda f: battle_result(f) or skip_confirmation(f)
                     or f.has("跳过", region=skip_region, exact=True, confidence=.9)
                     or (pending.get("result_evidence") and tournament_page(f)),
                     "宇宙武道会战斗结算", timeout=240)
    # A match has an attack round and a defense round; each has its own skip.
    for _ in range(2):
        if battle_result(frame) or tournament_page(frame):
            break
        if not skip_confirmation(frame):
            ctx.save("universe_skip_before", frame)
            ctx.tap(frame, (1218, 216), "跳过宇宙武道会战斗演出")
            frame = ctx.wait(lambda f: skip_confirmation(f) or battle_result(f),
                             "武道会跳过确认或结算", timeout=20)
        if skip_confirmation(frame):
            ctx.save("universe_skip_confirm_before", frame)
            ctx.text(frame, "确认", region=(700, 385, 920, 460), exact=True,
                     reason="确认跳过宇宙武道会当前回战")
            ctx.pause(3)
            # Do not read the battle's skip label through the open modal.
            frame = ctx.wait(lambda f: battle_result(f) or (not skip_confirmation(f)
                             and f.has("跳过", region=skip_region, exact=True, confidence=.9)),
                             "武道会下一回战或结算", timeout=60)
    if not battle_result(frame) and not tournament_page(frame):
        frame = ctx.wait(battle_result, "宇宙武道会最终结算", timeout=240)
    if battle_result(frame):
        pending["result_evidence"] = ctx.save("universe_match_result", frame)
        save_state(path, state)
        ctx.text(frame, "确认", region=(920, 600, 1210, 690), exact=True, reason="确认宇宙武道会战斗结算")
        frame = ctx.wait(lambda f: tournament_page(f) or f.reward(), "宇宙武道会返回与奖励", timeout=20)
    if frame.reward():
        frame = ctx.dismiss_reward(frame)
        frame = ctx.wait(tournament_page, "宇宙武道会主页面")
    remaining = free_matches(frame)
    if game_day() != pending["day"] or remaining != pending["before_remaining"] - 1:
        raise Halt("武道会结算后的免费次数变化未核对成功，保留待确认记录，未再次匹配。")
    state["last"] = {**pending, "after_remaining": remaining,
                     "after_evidence": ctx.save("universe_match_verified", frame),
                     "verified_at": datetime.now(CST).isoformat()}
    state["pending"] = None
    save_state(path, state)
    return frame


def mission_panel(frame: Frame) -> bool:
    return (not frame.is_loading() and not frame.paid_dialog() and not frame.reward()
            and frame.has("每日任务", region=(390, 45, 640, 110), exact=True)
            and frame.has("当期任务", region=(660, 45, 920, 110), exact=True)
            and frame.has("在宇宙武道会中", region=(330, 130, 660, 560)))


def mission_tab(frame: Frame, tab: str) -> bool:
    if not mission_panel(frame):
        return False
    left, _, right, _ = MISSION_TABS[tab]
    # The orange underline identifies the selected tab. Quest rows advance
    # after claiming tiers, so their wording cannot identify the season page.
    underline = frame.image[104:108, left + 30:right - 30]
    orange = cv2.inRange(cv2.cvtColor(underline, cv2.COLOR_BGR2HSV), (5, 105, 130), (40, 255, 255))
    return float((orange > 0).mean()) > .5


def dismiss_missions(ctx: Context, frame: Frame) -> Frame:
    ctx.tap(frame, (1260, 700), "收起宇宙武道会任务弹窗")
    return ctx.wait(tournament_page, TITLE + "主页面")


def claim_missions(ctx: Context, frame: Frame) -> tuple[Frame, int]:
    from .tasks import CLAIM_ALL
    def button_on(page: Frame):
        region = (490, 580, 790, 675)
        button = page.find(*CLAIM_ALL, region=region, confidence=.9)
        if button is None and page.vision is not None:
            words = page.vision.region_words(page, region)
            button = Frame(page.image, page.original_size, words, page.png).find(*CLAIM_ALL, region=region, confidence=.9)
        return button

    if not mission_panel(frame):
        ctx.text(frame, "活动任务", region=(50, 620, 260, 710), exact=True)
        frame = ctx.wait(mission_panel, "宇宙武道会任务")
    claimed = 0
    for tab, region in MISSION_TABS.items():
        ctx.text(frame, tab, region=region, exact=True)
        frame = ctx.wait(lambda f: mission_tab(f, tab), TITLE + tab)
        button = button_on(frame)
        if button is None:
            raise Halt("武道会任务的一键领取按钮未读清。")
        if not frame.colored(button):
            continue
        ctx.save("universe_" + tab + "_before", frame)
        ctx.tap(frame, button.center, "领取宇宙武道会" + tab + "奖励")
        frame = ctx.wait(lambda f: f.reward(), "宇宙武道会任务奖励", timeout=10)
        ctx.save("universe_" + tab + "_reward", frame)
        ctx.dismiss_reward(frame)
        frame = ctx.wait(lambda f: mission_tab(f, tab), "宇宙武道会奖励返回")
        button = button_on(frame)
        if button is None or frame.colored(button):
            raise Halt("武道会任务奖励领取后的按钮状态未核对成功，未重复领取。")
        claimed += 1
    return dismiss_missions(ctx, frame), claimed


def universe(ctx: Context) -> None:
    path = ROOT / "runtime/state/universe.json"
    state = load_state(path, {"version": 1, "pending": None})
    if state.get("version") != 1 or "pending" not in state:
        raise NeedsSetup("宇宙武道会匹配记录格式无效。")
    completed = 0
    if state["pending"] is not None:
        frame = finish_match(ctx, state, path)
        completed += 1
    else:
        frame = open_tournament(ctx)
        if frame is None:
            return
    initial = free_matches(frame)
    for _ in range(initial):
        from .tasks import spend_budget
        spend_budget(ctx, 4, 12, "宇宙武道会免费匹配与领奖")
        request_match(ctx, frame, state, path)
        frame = finish_match(ctx, state, path)
        completed += 1
        ctx.log(f"宇宙武道会已完成本轮第{completed}场，今日免费次数剩余{free_matches(frame)}次。")
    frame, claimed = claim_missions(ctx, frame)
    ctx.result(TITLE, "completed" if completed or claimed else "already_done",
               f"本轮已完成{completed}场免费匹配，剩余{free_matches(frame)}次；已领取{claimed}页达标任务奖励。", frame)
