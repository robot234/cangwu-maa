"""Configurable resource sweeps with screen-based opening and pending receipts."""
from __future__ import annotations

from datetime import datetime
import re
import time

import cv2
import numpy as np

from .feature_config import game_day, load_state, resource_selection, save_state
from .resource_catalog import RESOURCE_DUNGEONS
from .resource_navigation import check_connection, open_resources
from .runtime import CST, Context, Halt, NeedsSetup, ROOT
from .shops import field_integer
from .tasks import integer, spend_budget, sweep_budget
from .vision import Frame, normalized


SIDE = (0, 95, 127, 710)
TITLE = (245, 0, 410, 85)
DETAIL = (810, 85, 1260, 150)
CLOSED = ("未开放", "未开启", "不在开放时间", "未到开放时间", "未在开放时间", "未在开放时段", "开放时间未到")


def dungeon_page(frame: Frame, key: str) -> bool:
    check_connection(frame)
    return (not frame.is_loading() and frame.has(*RESOURCE_DUNGEONS[key]["family"], region=TITLE)
            and frame.has("作战准备", region=(980, 610, 1260, 710))
            and not frame.reward()
            and not frame.has("确认", "确定", "取消", region=(200, 140, 1080, 600), exact=True)
            and not frame.has("开始扫荡", region=(430, 490, 830, 590)))


def opening_badge(frame: Frame, word, now: datetime | None = None) -> str | None:
    """Use the selected row's live badge, never a bundled weekday timetable."""
    region = (0, word.center[1] + 12, 126, min(719, word.center[1] + 48))
    badge = frame.find("开放", "开启", region=region)
    if badge is None and frame.vision is not None:
        words = frame.vision.region_words(frame, region, scale=4)
        badge = Frame(frame.image, frame.original_size, words, frame.png).find("开放", "开启", region=region)
    if badge is None:
        return None
    text = normalized(badge.text)
    if any(value in text for value in CLOSED):
        return badge.text
    if "常驻开放" in text or "全天开放" in text or "今日开放" in text:
        return None
    match = re.fullmatch(r"(?:每)?(?:周|星期)([一二三四五六日天、,，/周和]+)(?:开放|开启)", text)
    # Around the reset boundary, defer to the actual navigation response.
    current = (now or datetime.now(CST)).astimezone(CST)
    if match:
        days = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        possible_days = {current.weekday()}
        if current.hour < 5:
            possible_days.add((current.weekday() - 1) % 7)
        if possible_days.isdisjoint({days[c] for c in match[1] if c in days}):
            return "当前游戏标注：" + badge.text
    return None


def open_dungeon(ctx: Context, key: str) -> Frame:
    frame = open_resources(ctx)
    spec = RESOURCE_DUNGEONS[key]
    ctx.text(frame, *spec["aliases"], region=spec["region"], exact=True)
    return ctx.wait(lambda f: dungeon_page(f, key), spec["label"] + "关卡列表")


def find_choice(ctx: Context, frame: Frame, key: str, choice: str):
    label = RESOURCE_DUNGEONS[key]["choices"][choice]["label"]
    for step in range(4):
        if not dungeon_page(frame, key):
            raise Halt("资源副本菜单未识别，停止选择关卡。")
        word = frame.find(label, region=SIDE, exact=True)
        if word is not None and 115 <= word.center[1] <= 680:
            return frame, word
        # First return to the start in case the game retained a scroll offset.
        start, end = ((65, 240), (65, 620)) if step == 0 else ((65, 620), (65, 240))
        ctx.swipe(frame, start, end, "查找资源子关卡：" + label, duration=800)
        frame = ctx.wait(lambda f: dungeon_page(f, key), "资源子关卡菜单")
    ctx.save("resource_missing_choice", frame)
    raise NeedsSetup("没有找到配置的资源子关卡：" + label)


def active_choice(frame: Frame, key: str, choice: str) -> bool:
    spec = RESOURCE_DUNGEONS[key]["choices"][choice]
    titles = (spec["title"], spec["title"].replace("异", "異"))
    if not dungeon_page(frame, key) or not frame.has(*titles, region=TITLE):
        return False
    if key != "resource_beast":
        return True
    word = frame.find(spec["label"], region=SIDE, exact=True)
    if word is None:
        return False
    # The yellow bar at the selected row's left edge independently confirms
    # the mode, including the experience page's OCR variant 異兽清剿.
    y = word.center[1]
    crop = frame.image[max(95, y - 35):min(710, y + 35), 0:6]
    if not crop.size:
        return False
    mask = cv2.inRange(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), (15, 80, 100), (40, 255, 255))
    return float(np.mean(mask > 0)) > .5


def select_choice(ctx: Context, frame: Frame, key: str, choice: str) -> tuple[Frame, str | None]:
    frame, word = find_choice(ctx, frame, key, choice)
    closed = opening_badge(frame, word)
    if closed:
        return frame, closed
    ctx.tap(frame, word.center, "选择资源子关卡：" + RESOURCE_DUNGEONS[key]["choices"][choice]["label"], pause=.4)
    until = min(ctx.deadline, time.monotonic() + 8)
    while time.monotonic() < until:
        frame = ctx.frame()
        check_connection(frame)
        if not frame.is_loading():
            if frame.has(*CLOSED, region=(200, 170, 1080, 600)):
                ctx.save("resource_closed", frame)
                # Close only a clearly identified unavailable-dungeon dialog.
                button = frame.find("确认", "确定", region=(400, 380, 900, 580), exact=True)
                if button is not None:
                    ctx.tap(frame, button.center, "关闭副本未开放提示")
                    frame = ctx.wait(lambda f: dungeon_page(f, key), "返回资源关卡列表")
                return frame, "游戏提示当前未开放"
            if active_choice(frame, key, choice):
                return frame, None
        ctx.pause(.4)
    ctx.save("resource_choice_no_response", frame)
    raise Halt("资源子关卡没有切换成功；没有把识别失败记作未开放。")


def stage_prefixes(key: str, choice: str) -> tuple[str, ...]:
    value = RESOURCE_DUNGEONS[key]["choices"][choice]["prefix"]
    return (value, value.replace("异", "異"))


def detail_matches(frame: Frame, key: str, choice: str, stage: int) -> bool:
    return (active_choice(frame, key, choice)
            and frame.has(f"1-{stage}", region=(810, 85, 950, 150), exact=True)
            and any(frame.has(f"{prefix}-{stage}", region=(940, 85, 1260, 150), exact=True)
                    for prefix in stage_prefixes(key, choice)))


def select_stage(ctx: Context, frame: Frame, key: str, choice: str, stage: int) -> Frame:
    if not 1 <= stage <= RESOURCE_DUNGEONS[key]["max_stage"]:
        raise Halt("资源关卡编号超出允许范围。")
    prefixes = "|".join(re.escape(normalized(p)) for p in stage_prefixes(key, choice))
    pattern = re.compile(rf"1-{stage}(?:(?:{prefixes})-{stage})?")
    for _ in range(5):
        if not active_choice(frame, key, choice):
            raise Halt("资源子关卡与配置不符，未选择或开始作战。")
        rows = [w for w in frame.words if w.confidence >= .85 and 128 <= w.center[0] <= 400 and 90 <= w.center[1] <= 650]
        row = next((w for w in rows if pattern.fullmatch(normalized(w.text))), None)
        if row is not None:
            ctx.tap(frame, row.center, f"选择{RESOURCE_DUNGEONS[key]['choices'][choice]['label']} 1-{stage}", pause=.4)
            return ctx.wait(lambda f: detail_matches(f, key, choice, stage), "配置的资源关卡详情")
        levels = [int(m[1]) for w in rows if (m := re.match(r"1-(\d+)", normalized(w.text)))]
        if not levels:
            break
        if stage in levels:
            ctx.save("resource_stage_name_mismatch", frame)
            raise NeedsSetup(f"已看到1-{stage}但关卡名称未匹配，未继续滑动或改打其他关卡。")
        start, end = ((260, 220), (260, 570)) if stage < min(levels) else ((260, 570), (260, 220))
        ctx.swipe(frame, start, end, f"查找资源关卡1-{stage}", duration=800)
        frame = ctx.wait(lambda f: active_choice(f, key, choice), "资源关卡列表")
    ctx.save("resource_missing_stage", frame)
    raise NeedsSetup(f"没有读到配置的1-{stage}，未改用其他关卡。")


def sweep_unlocked(frame: Frame) -> bool:
    if not frame.has("胜利条件", region=(800, 140, 1000, 190)):
        return False
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    return all(np.count_nonzero(cv2.inRange(hsv[y - 20:y + 21, 820:865], (5, 100, 150), (40, 255, 255))) >= 25
               for y in (228, 286, 345))


def resource_ledger(path, day: str, key: str) -> dict:
    state = load_state(path, {"version": 1, "day": day, "done": {}, "pending": None})
    if "pending" not in state or state["pending"] is not None:
        raise NeedsSetup(f"上次资源扫荡结果待核对，不会重复消耗。请核对游戏及记录：{path}")
    if (type(state.get("version")) is not int or state["version"] != 1
            or not isinstance(state.get("day"), str) or not isinstance(state.get("done"), dict)):
        raise NeedsSetup("资源副本每日记录格式无效。")
    try:
        if datetime.strptime(state["day"], "%Y-%m-%d").strftime("%Y-%m-%d") != state["day"] or state["day"] > day:
            raise ValueError("invalid or future ledger day")
    except ValueError as exc:
        raise NeedsSetup("资源副本记录日期无效或系统日期倒退，请先核对，未重置累计次数。") from exc
    for identity, quantity in state["done"].items():
        match = re.fullmatch(r"([a-z_]+):(\d+)", identity) if isinstance(identity, str) else None
        if (match is None or match[1] not in RESOURCE_DUNGEONS[key]["choices"]
                or not 1 <= int(match[2]) <= RESOURCE_DUNGEONS[key]["max_stage"]
                or type(quantity) is not int or not 0 <= quantity <= 100):
            raise NeedsSetup("资源副本累计次数记录无效。")
    if state["day"] != day:
        state = {"version": 1, "day": day, "done": {}, "pending": None}
    return state


def checked_sweep_frame(frame: Frame, cost: int, reserve: int) -> Frame:
    try:
        sweep_budget(frame, cost, reserve)
        return frame
    except Halt:
        if frame.vision is None or not frame.all("扫荡次数", "剩余次数", "开始扫荡") or frame.paid_dialog():
            raise
    region = (590, 385, 835, 450)
    words = frame.vision.region_words(frame, region, scale=4)
    outside = [word for word in frame.words if not (region[0] <= word.center[0] <= region[2]
                                                    and region[1] <= word.center[1] <= region[3])]
    reread = Frame(frame.image, frame.original_size, words + outside, frame.png, frame.vision)
    # Never guess away an extra digit or weaken the stamina/cost equation.
    # Only a new Maa recognition of these same pixels may repair the read.
    sweep_budget(reread, cost, reserve)
    return reread


def prepare_sweep(ctx: Context, frame: Frame, count: int, cost: int, reserve: int) -> Frame:
    spend_budget(ctx, count + 5, 12, "资源扫荡与核对")
    ctx.text(frame, "扫荡", region=(790, 610, 980, 710), exact=True)
    frame = ctx.wait(lambda f: f.all("扫荡次数", "剩余次数", "开始扫荡"), "资源扫荡设置")
    ctx.text(frame, "MIN", region=(755, 290, 845, 365), exact=True)
    frame = ctx.frame()
    for expected in range(1, count):
        if integer(frame, (440, 245, 695, 330)) != expected:
            raise Halt("资源扫荡次数调整不一致，未开始扫荡。")
        ctx.tap(frame, (725, 237), "增加一次已配置的资源扫荡", pause=.15)
        frame = ctx.frame()
    original = frame
    frame = checked_sweep_frame(frame, cost, reserve)
    if frame is not original:
        ctx.log("扫荡体力数字已局部放大复核，次数和消耗一致。")
    actual, _, _ = sweep_budget(frame, cost, reserve)
    if actual != count:
        raise Halt("资源扫荡预览次数与计划不一致。")
    return frame


def resource_sweep(ctx: Context, key: str, *, preview_only: bool = False) -> None:
    selected, reserve = resource_selection(ctx.maa, key)
    label = RESOURCE_DUNGEONS[key]["label"]
    if not selected:
        ctx.result(label, "already_done", "没有勾选子关卡，跳过。")
        return
    path = ROOT / f"runtime/state/{key}.json"
    state = resource_ledger(path, game_day(), key)
    remaining = {choice: value for choice, value in selected.items()
                 if state["done"].get(f"{choice}:{value['stage']}", 0) < value["quantity"]}
    if not remaining:
        ctx.result(label, "already_done", "勾选关卡的每日目标已完成，不重复消耗。")
        return
    frame = open_dungeon(ctx, key)
    messages, completed = [], False
    for choice, value in remaining.items():
        name = RESOURCE_DUNGEONS[key]["choices"][choice]["label"]
        frame, closed = select_choice(ctx, frame, key, choice)
        if closed:
            ctx.save(f"{key}_{choice}_closed", frame)
            messages.append(f"{name}未开放，跳过（{closed}）")
            continue
        stage = value["stage"]
        frame = select_stage(ctx, frame, key, choice, stage)
        ctx.save(f"{key}_{choice}_{stage}_detail", frame)
        if not sweep_unlocked(frame):
            ctx.save(f"{key}_{choice}_locked", frame)
            messages.append(f"{name}1-{stage}未满足三星扫荡条件，跳过")
            continue
        cost = field_integer(frame, (1160, 630, 1235, 690))
        if type(cost) is not int or not 1 <= cost <= 100:
            raise Halt("没有读清资源关卡单次体力，未开始扫荡。")
        stamina = frame.number_fraction((1080, 0, 1279, 85))
        if stamina is None:
            raise Halt("没有读清现有体力，未开始资源扫荡。")
        identity = f"{choice}:{stage}"
        from .stamina import ensure_stamina
        frame = ensure_stamina(ctx, frame, reserve + cost * (value["quantity"] - state["done"].get(identity, 0)),
                               lambda f: detail_matches(f, key, choice, stage) and sweep_unlocked(f),
                               preview_only=preview_only)
        stamina = frame.number_fraction((1080, 0, 1279, 85))
        if (stamina is None or not detail_matches(frame, key, choice, stage) or not sweep_unlocked(frame)
                or field_integer(frame, (1160, 630, 1235, 690)) != cost):
            raise Halt("补体力后资源关卡或体力未核对成功，未开始扫荡。")
        count = min(value["quantity"] - state["done"].get(identity, 0), max(0, stamina[0] - reserve) // cost)
        if not count:
            messages.append(f"{name}1-{stage}现有体力不足或需保留{reserve}，跳过")
            continue
        frame = prepare_sweep(ctx, frame, count, cost, reserve)
        _, before, expected = sweep_budget(frame, cost, reserve)
        evidence = ctx.save(f"{key}_{choice}_{stage}_preview", frame)
        if preview_only:
            ctx.tap(frame, (280, 620), "取消资源扫荡预览")
            frame = ctx.wait(lambda f: detail_matches(f, key, choice, stage), "资源扫荡预览关闭")
            messages.append(f"{name}1-{stage} ×{count}，单次{cost}体力，预览已核对并取消")
            completed = True
            continue
        # A calendar reset must never erase an uncertain submission.
        if game_day() != state["day"]:
            raise Halt("扫荡准备期间已换日，未提交；请重新运行核对每日目标。")
        state["pending"] = {"choice": choice, "stage": stage, "quantity": count, "cost": cost,
                            "before_stamina": before, "expected_stamina": expected, "evidence": evidence}
        save_state(path, state)
        ctx.text(frame, "开始扫荡", region=(490, 500, 790, 580), exact=True,
                 reason=f"扫荡{name}1-{stage} ×{count}，体力{before}→{expected}")
        frame = ctx.wait(lambda f: f.settlement_confirm() is not None or f.reward(), "资源扫荡结算", timeout=90)
        ctx.save(f"{key}_{choice}_settlement", frame)
        if frame.reward():
            frame = ctx.dismiss_reward(frame)
        else:
            ctx.text(frame, frame.settlement_confirm().text, region=(420, 560, 850, 670), exact=True)
        frame = ctx.wait(lambda f: detail_matches(f, key, choice, stage) and not f.has("扫荡结算"), "资源扫荡后详情")
        after = frame.number_fraction((1080, 0, 1279, 85))
        if after is None or not expected <= after[0] <= expected + 2 or after[0] >= before:
            raise Halt("资源扫荡结算后的体力未核对成功；保留待确认记录，不会重复扫荡。")
        state["done"][identity] = state["done"].get(identity, 0) + count
        state["pending"] = None
        save_state(path, state)
        messages.append(f"{name}1-{stage} ×{count}")
        completed = True
    ctx.result(label, "completed" if completed else "already_done", "；".join(messages), frame)
