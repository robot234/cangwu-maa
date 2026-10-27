"""Opt-in stamina refill, with one shared daily ledger and verified UI quotes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
import re
import time

import cv2
import numpy as np

from .feature_config import STAMINA_DEFAULT, game_day, load_state, save_state, validate_stamina_config
from .runtime import Context, Halt, NeedsSetup, ROOT
from .vision import Frame, normalized, template_match

KINDS = {"small": (768, 50), "large": (640, 100), "crystal": (508, 100)}
TITLE = (540, 105, 740, 165)
CONFIRM = (540, 525, 780, 605)
BEFORE = (640, 414, 690, 452)
AFTER = (748, 414, 810, 452)


def refill_dialog(frame: Frame) -> bool:
    return (not frame.is_loading() and not frame.reward()
            and frame.has("补充体力", region=TITLE, exact=True)
            and frame.has("补充方式", region=(430, 165, 550, 215), exact=True)
            and frame.has("体力回复量", region=(435, 409, 570, 454), exact=True)
            and frame.has("确认", region=CONFIRM, exact=True)
            and not frame.has("充值", "支付", "是否购买", "是否消耗", "购买演习", "购买体力", "体力购买",
                              "网络异常", "网络连接异常", "连接失败", region=(200, 140, 1130, 620)))


def refill_tap_allowed(frame: Frame, point: tuple[int, int], mode: str) -> bool:
    if not refill_dialog(frame):
        return False
    if point in {(x, 280) for x, _ in KINDS.values()}:
        return True
    minimum = minimum_button(frame)
    if minimum is not None and point == minimum:
        return True
    confirm = frame.find("确认", region=CONFIRM, exact=True)
    return mode == "commit" and confirm is not None and point == confirm.center


def minimum_button(frame: Frame):
    if not frame.has("使用数量", region=(440, 355, 545, 407)):
        return None
    return template_match(frame, ROOT / "assets/stamina_min.png", (550, 355, 615, 405), .92)


def selected_kind(frame: Frame) -> str:
    selected = []
    for kind, left in (("crystal", 450), ("large", 578), ("small", 706)):
        corners = [(left, 227, left + 16, 244), (left + 98, 227, left + 118, 244),
                   (left, 315, left + 16, 337), (left + 98, 315, left + 118, 337)]
        hits = 0
        for x1, y1, x2, y2 in corners:
            hsv = cv2.cvtColor(frame.image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
            hits += float(np.mean(cv2.inRange(hsv, (5, 105, 130), (40, 255, 255)) > 0)) > .22
        if hits >= 3:
            selected.append(kind)
    if len(selected) != 1:
        raise Halt("体力补充方式的选中边框不明确，未确认。")
    return selected[0]


def mode_selected(frame: Frame, kind: str) -> bool:
    if not refill_dialog(frame):
        return False
    try:
        return selected_kind(frame) == kind
    except Halt:
        return False  # Wait through the selection animation, without input.


def field(frame: Frame, region) -> int | None:
    from .shops import field_integer
    return field_integer(frame, region)


def read_stamina(frame: Frame) -> tuple[int, int]:
    region = (620, 0, 770, 75) if frame.is_home() else (1080, 0, 1279, 85)
    value = frame.number_fraction(region)
    if value is None and frame.vision is not None:
        crop = (650, 16, 732, 55) if frame.is_home() else (1120, 16, 1218, 64)
        words = frame.vision.region_words(frame, crop, scale=4)
        value = Frame(frame.image, frame.original_size, words, frame.png).number_fraction(region)
    if value is None or not 0 <= value[0] <= 99999 or not 1 <= value[1] <= 99999:
        raise Halt("没有读清当前体力，未补充。")
    return value


def crystal_balance(frame: Frame) -> tuple[int, bool]:
    region = (497, 233, 560, 265)
    words = frame.words
    for attempt in range(2):
        values = []
        for word in words:
            x, y = word.center
            if word.confidence < .9 or not (region[0] <= x <= region[2] and region[1] <= y <= region[3]):
                continue
            match = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb]?)", normalized(word.text))
            if match:
                amount, unit = Decimal(match[1]), match[2]
                # Abbreviated counters can round. Only their conservative lower
                # bound is used for affordability; receipts use the game counter.
                multiplier = {"": 1, "k": 1000, "m": 1000000, "b": 1000000000}[unit]
                values.append((max(0, int((amount - (1 if unit else 0)) * multiplier)), not unit))
        if len(values) == 1:
            return values[0]
        if attempt == 0 and frame.vision is not None:
            words = frame.vision.region_words(frame, region, scale=4)
    raise Halt("没有读清结晶余额，未购买体力。")


def remaining_purchases(frame: Frame) -> int:
    if not frame.has("今日剩余回复次数", region=(435, 455, 625, 502), exact=True):
        raise Halt("补体力购买次数标签发生变化，未确认结果。")
    remaining = frame.number_fraction((720, 455, 825, 502))
    if remaining is None and frame.vision is not None:
        words = frame.vision.region_words(frame, (730, 458, 818, 499), scale=4)
        remaining = Frame(frame.image, frame.original_size, words, frame.png).number_fraction((720, 455, 825, 502))
    if remaining is None or remaining[1] != 30 or not 0 <= remaining[0] <= 30:
        raise Halt("没有读清体力剩余购买次数。")
    return remaining[0]


@dataclass(frozen=True)
class RefillQuote:
    kind: str
    before: int
    after: int
    stock: int | None = None
    price: int = 0
    remaining: int | None = None
    balance: int | None = None
    exact_balance: bool = False


def refill_quote(frame: Frame, kind: str) -> RefillQuote:
    if kind not in KINDS or not refill_dialog(frame) or selected_kind(frame) != kind:
        raise Halt("不是已核对的体力补充方式，未确认。")
    x, gain = KINDS[kind]
    region = (x - 50, 257, x + 48, 320)
    if template_match(frame, ROOT / f"assets/stamina_{kind}.png", region, .92) is None:
        raise Halt("体力道具或结晶图标与已核对图片不一致，未确认。")
    before, after = field(frame, BEFORE), field(frame, AFTER)
    if before is None or after is None or not 0 <= before < after <= 99999 or after - before != gain:
        raise Halt("体力回复量与单个道具／单次购买不一致，未确认。")
    if kind == "crystal":
        if (not frame.has("消耗结晶", region=(435, 365, 570, 407), exact=True)
                or not frame.has("今日剩余回复次数", region=(435, 455, 625, 502), exact=True)):
            raise Halt("补体力的货币或购买次数标签发生变化，未购买。")
        price = field(frame, (745, 369, 815, 408))
        remaining = remaining_purchases(frame)
        if price is None or not 1 <= price <= 99999:
            raise Halt("没有读清本次结晶价格或剩余购买次数，未购买。")
        balance, exact = crystal_balance(frame)
        return RefillQuote(kind, before, after, price=price, remaining=remaining, balance=balance, exact_balance=exact)
    if not frame.has("使用数量", region=(440, 355, 545, 407)) or frame.has("消耗结晶", region=(435, 365, 570, 407)):
        raise Halt("道具补体力界面与预期不一致，未使用。")
    if field(frame, (675, 361, 715, 402)) != 1:
        raise Halt("体力道具使用数量不是明确的1，未使用。")
    stock = field(frame, (x, 233, x + 48, 265))
    if stock is None or not 0 <= stock <= 999999:
        raise Halt("体力道具库存没有读清，未使用。")
    return RefillQuote(kind, before, after, stock=stock)


def refill_ledger(path, day: str) -> dict:
    empty = {"version": 1, "day": day, "items": 0, "crystals": 0, "crystal_spent": 0, "pending": None}
    state = load_state(path, empty)
    if "pending" not in state or state["pending"] is not None:
        raise NeedsSetup(f"上次补体力结果待核对，不会再次补充。请核对游戏及记录：{path}")
    try:
        recorded = date.fromisoformat(state["day"])
        today = date.fromisoformat(day)
    except (KeyError, TypeError, ValueError) as exc:
        raise NeedsSetup("补体力每日记录的日期无效。") from exc
    if recorded.isoformat() != state["day"] or recorded > today:
        raise NeedsSetup("补体力记录日期异常或系统时间倒退，未重置额度。")
    if (state.get("version") != 1 or any(type(state.get(key)) is not int or not 0 <= state[key] <= high
            for key, high in (("items", 100), ("crystals", 30), ("crystal_spent", 99999)))
            or (state["crystals"] == 0) != (state["crystal_spent"] == 0)):
        raise NeedsSetup("补体力每日用量记录无效，未重置额度。")
    return empty if recorded < today else state


def open_refill(ctx: Context, frame: Frame, page_ok) -> Frame:
    if not page_ok(frame) or refill_dialog(frame) or frame.reward():
        raise Halt("当前不是已核对的关卡详情，未打开补体力。")
    read_stamina(frame)
    region = (738, 21, 767, 53) if frame.is_home() else (1212, 20, 1247, 54)
    plus = template_match(frame, ROOT / "assets/stamina_add.png", region, .85)
    if plus is None:
        raise Halt("没有识别到体力加号，未打开补充窗口。")
    ctx.tap(frame, plus, "打开已核对的体力补充窗口")
    return ctx.wait(refill_dialog, "补充体力窗口")


def choose_refill(ctx: Context, frame: Frame, kind: str) -> Frame:
    if not refill_dialog(frame):
        raise Halt("不是已核对的补体力窗口，未选择方式。")
    ctx.tap(frame, (KINDS[kind][0], 280), f"选择{'结晶' if kind == 'crystal' else str(KINDS[kind][1]) + '体力道具'}")
    frame = ctx.wait(lambda f: mode_selected(f, kind), "体力补充方式已选中")
    if kind != "crystal":
        if field(frame, (KINDS[kind][0], 233, KINDS[kind][0] + 48, 265)) == 0:
            return frame
        # Always reset the quantity; an old manual MAX is never inherited.
        minimum = minimum_button(frame)
        if minimum is None:
            raise Halt("没有识别到体力道具MIN按钮，未使用。")
        ctx.tap(frame, minimum, "体力道具每次只使用1个")
        frame = ctx.frame()
    return frame


def close_refill(ctx: Context, page_ok) -> Frame:
    ctx.back()
    return ctx.wait(lambda f: not refill_dialog(f) and not f.reward() and page_ok(f), "关闭补体力并回到原关卡")


def can_refill(quote: RefillQuote, policy: dict, state: dict) -> bool:
    if quote.kind != "crystal":
        return quote.stock > 0 and state["items"] < policy["item_limit"]
    return (policy["allow_crystals"] and state["crystals"] < policy["crystal_limit"]
            and quote.price <= policy["crystal_budget"] - state["crystal_spent"]
            and quote.remaining > 0 and quote.balance >= quote.price)


def confirm_refill(ctx: Context, frame: Frame, quote: RefillQuote, policy: dict, state: dict, path, page_ok) -> Frame:
    # Re-read immediately before the single submission, after all selection.
    frame = ctx.frame()
    if refill_quote(frame, quote.kind) != quote or not can_refill(quote, policy, state):
        raise Halt("补体力预览或额度发生变化，未确认。")
    if (game_day() != state["day"] or time.monotonic() + 45 >= ctx.deadline
            or ctx.config["limits"]["max_actions"] - ctx.actions < 8):
        raise Halt("补体力准备期间换日或运行预算不足，未确认。")
    ctx.check()
    evidence = ctx.save("stamina_refill_preview", frame)
    state["pending"] = {**asdict(quote), "evidence": evidence}
    save_state(path, state)
    ctx.text(frame, "确认", region=CONFIRM, exact=True, reason=f"补充体力 {quote.before}→{quote.after}，本次消耗" +
             (f"{quote.price}结晶" if quote.kind == "crystal" else "1个体力道具"))
    frame = ctx.wait(lambda f: f.reward() or (refill_dialog(f) and (field(f, BEFORE) or 0) >= quote.after)
                     or (not refill_dialog(f) and page_ok(f)), "补体力返回结果", timeout=25)
    if frame.reward():
        frame = ctx.dismiss_reward(frame)
    if not refill_dialog(frame):
        if not page_ok(frame) or not quote.after <= read_stamina(frame)[0] <= quote.after + 2:
            raise Halt("补体力后实际体力未核对成功，保留待确认记录。")
        frame = open_refill(ctx, frame, page_ok)
    frame = choose_refill(ctx, frame, quote.kind)
    # A last item can be gone. Read the inventory independently of a new quote.
    current = field(frame, BEFORE)
    if current is None or not quote.after <= current <= quote.after + 2:
        raise Halt("补体力结果没有读清，保留待确认记录。")
    if quote.kind == "crystal":
        if selected_kind(frame) != "crystal" or remaining_purchases(frame) != quote.remaining - 1:
            raise Halt("结晶购买剩余次数未减少1，保留待确认记录。")
        balance, exact = crystal_balance(frame)
        if quote.exact_balance and (not exact or balance != quote.balance - quote.price):
            raise Halt("补体力结晶余额变化不一致，保留待确认记录。")
    else:
        x = KINDS[quote.kind][0]
        if field(frame, (x, 233, x + 48, 265)) != quote.stock - 1:
            raise Halt("体力道具库存未减少1，保留待确认记录。")
    ctx.save("stamina_refill_receipt", frame)
    frame = close_refill(ctx, page_ok)
    if not quote.after <= read_stamina(frame)[0] <= quote.after + 2:
        raise Halt("关闭补体力窗口后的体力不一致，保留待确认记录。")
    if quote.kind == "crystal":
        state["crystals"] += 1
        state["crystal_spent"] += quote.price
    else:
        state["items"] += 1
    state["pending"] = None
    save_state(path, state)
    ctx.log(f"补体力已核对；今日共用量：道具{state['items']}个，结晶购买{state['crystals']}次／{state['crystal_spent']}结晶。")
    return frame


def ensure_stamina(ctx: Context, frame: Frame, required: int, page_ok, *, preview_only: bool = False) -> Frame:
    policy = validate_stamina_config(ctx.config.get("stamina_refill", STAMINA_DEFAULT))
    if preview_only or not policy["enabled"]:
        return frame
    if type(required) is not int or not 1 <= required <= 199999:
        raise Halt("所需体力预算无效。")
    path = ROOT / "runtime/state/stamina_refill.json"
    state = refill_ledger(path, game_day())
    if read_stamina(frame)[0] >= required:
        return frame
    kinds = []
    if state["items"] < policy["item_limit"]:
        kinds += ["small", "large"]
    if policy["allow_crystals"] and state["crystals"] < policy["crystal_limit"] and state["crystal_spent"] < policy["crystal_budget"]:
        kinds.append("crystal")
    if not kinds:
        ctx.log("自动补体力未设置可用额度或今日额度已用完，按现有体力继续／跳过。")
        return frame
    with ctx.stamina_scope("commit"):
        for _ in range(130):  # Hard cap in addition to daily, action and time budgets.
            if read_stamina(frame)[0] >= required:
                return frame
            modal = open_refill(ctx, frame, page_ok)
            chosen = None
            for kind in kinds:
                if kind != "crystal" and state["items"] >= policy["item_limit"]:
                    continue
                modal = choose_refill(ctx, modal, kind)
                if kind != "crystal" and field(modal, (KINDS[kind][0], 233, KINDS[kind][0] + 48, 265)) == 0:
                    continue
                quote = refill_quote(modal, kind)
                if not abs(quote.before - read_stamina(frame)[0]) <= 2:
                    raise Halt("补体力窗口与原页面体力不一致，未确认。")
                if can_refill(quote, policy, state):
                    chosen = quote
                    break
            if chosen is None:
                frame = close_refill(ctx, page_ok)
                ctx.log("体力道具不足、结晶不足或每日额度用完，按现有体力继续／跳过。")
                return frame
            frame = confirm_refill(ctx, modal, chosen, policy, state, path, page_ok)
            if state["items"] >= policy["item_limit"] and (not policy["allow_crystals"] or state["crystals"] >= policy["crystal_limit"] or state["crystal_spent"] >= policy["crystal_budget"]):
                return frame
    return frame


def inspect_refill(ctx: Context, params: dict) -> None:
    # Preview permission permits the three modes and MIN, never 确认.
    page_ok = lambda f: f.is_home()
    with ctx.stamina_scope("preview"):
        frame = ctx.frame()
        if not refill_dialog(frame):
            key = params.get("stamina_resource")
            if key is not None:
                from .resource_stages import detail_matches, open_dungeon, select_choice, select_stage
                if key != "resource_core":
                    raise Halt("补体力关卡预览只支持已核对的生命核心1-5。")
                frame = open_dungeon(ctx, key)
                frame, closed = select_choice(ctx, frame, key, "life")
                if closed:
                    raise Halt("生命核心未开放，未预览补体力。")
                frame = select_stage(ctx, frame, key, "life", 5)
                page_ok = lambda f: detail_matches(f, key, "life", 5)
            else:
                frame = ctx.home()
            frame = open_refill(ctx, frame, page_ok)
        results = []
        for kind in ("small", "large", "crystal"):
            frame = choose_refill(ctx, frame, kind)
            quote = refill_quote(frame, kind)
            ctx.save(f"stamina_{kind}_preview", frame)
            results.append(asdict(quote))
        frame = close_refill(ctx, page_ok)
    if params.get("stamina_resource"):
        frame = ctx.home()
    import json
    (ctx.directory / "stamina-preview.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.result("补体力预览", "completed", "50／100体力道具及结晶价格已核对，预览已关闭；未使用道具或结晶。", frame)
