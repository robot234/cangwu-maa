"""Shared weekly reward budget for the three verified armor resonance stages."""
from __future__ import annotations

from .feature_config import BOSSES, armor_selection, game_week, load_state, save_state
from .runtime import Context, Halt, NeedsSetup, ROOT
from .shops import field_integer
from .tasks import sweep_budget
from .vision import Frame


def remaining_rewards(frame: Frame) -> int:
    if not frame.has("超限共鸣") or not frame.has("本周可领取奖励次数", region=(800, 550, 1240, 620)):
        raise Halt("不是已确认的装甲进阶页面。")
    fraction = frame.number_fraction((800, 550, 1245, 620))
    if fraction is None and frame.vision is not None:
        words = frame.vision.region_words(frame, (1107, 574, 1145, 601), scale=4)
        reread = Frame(frame.image, frame.original_size, words, frame.png)
        fraction = reread.number_fraction((1095, 565, 1180, 613))
    if fraction is None or fraction[1] != 5 or not 0 <= fraction[0] <= 5:
        raise Halt("没有读清装甲进阶每周剩余奖励次数，停止作战。")
    return fraction[0]


def armor_ledger(path, remaining: int, week: str) -> dict:
    state = load_state(path, {"version": 1, "week": week, "remaining": remaining, "done": {}, "pending": None})
    if "pending" not in state or state["pending"] is not None:
        raise NeedsSetup(f"上次共鸣扫荡结果待核对，不会重复消耗。请核对游戏及记录：{path}")
    if (state.get("version") != 1 or not isinstance(state.get("week"), str)
            or type(state.get("remaining")) is not int or not 0 <= state["remaining"] <= 5
            or not isinstance(state.get("done"), dict)):
        raise NeedsSetup("共鸣每周记录格式无效。")
    if any(key not in BOSSES or type(value) is not int or not 0 <= value <= 5 for key, value in state["done"].items()) or sum(state["done"].values()) > 5:
        raise NeedsSetup("共鸣每周累计次数记录无效。")
    if remaining > state["remaining"]:
        # The game can only replenish this counter at weekly reset. This also
        # handles skipped weeks without trusting an inferred wall-clock reset.
        state = {"version": 1, "week": week, "remaining": remaining, "done": {}, "pending": None}
    elif week != state["week"] and remaining != 0 and any(state["done"].values()):
        # If other play has consumed the new week's quota before we observe it,
        # a calendar change alone cannot prove which week's allocation remains.
        raise NeedsSetup("已跨周，但游戏剩余次数未增加，无法确认新周分配。请先核对本周记录；助手未重置或消耗次数。")
    else:
        state["week"] = week
    state["remaining"] = remaining
    return state


def open_armor(ctx: Context) -> Frame:
    frame = ctx.home()
    ctx.text(frame, "作战", region=(1020, 620, 1279, 719), exact=True)
    frame = ctx.wait(lambda f: f.has("战斗副本", region=(180, 0, 520, 90)), "作战页面")
    ctx.text(frame, "资源", region=(450, 75, 655, 195), exact=True)
    frame = ctx.wait(lambda f: f.has("装甲进阶", region=(900, 450, 1150, 600)), "资源副本")
    ctx.text(frame, "装甲进阶", region=(900, 450, 1150, 600), exact=True)
    return ctx.wait(lambda f: f.has("本周可领取奖励次数") and f.has("超限共鸣"), "装甲进阶")


def select_stage(ctx: Context, frame: Frame, boss: str) -> Frame:
    info = BOSSES[boss]
    ctx.text(frame, "超限共鸣", region=info["region"], exact=True, reason=f"选择共鸣：{info['name']}")
    frame = ctx.wait(lambda f: f.has(info["name"], region=(240, 0, 405, 85), exact=True), "对应共鸣页面")
    # Only use the highest stage already verified on this account. Clicking a
    # stage selects details; it never starts an unverified team battle.
    ctx.text(frame, "1-5", region=(125, 470, 400, 560), reason="选择已通关的 1-5 地狱")
    frame = ctx.wait(lambda f: f.has(info["name"], region=(940, 85, 1255, 145))
                     and f.has("1-5", region=(815, 85, 950, 145), exact=True)
                     and f.has("地狱", region=(940, 85, 1255, 145)), "共鸣 1-5 地狱详情")
    cost = field_integer(frame, (1160, 630, 1235, 690))
    if cost != 40 or frame.find("扫荡", region=(790, 610, 980, 710), exact=True) is None:
        raise NeedsSetup("共鸣关卡消耗或扫荡入口发生变化，停止作战。")
    return frame


def prepare_armor(ctx: Context, frame: Frame, count: int, remaining: int) -> Frame:
    if not 1 <= count <= remaining <= 5:
        raise Halt("共鸣扫荡次数超出剩余周配额。")
    ctx.text(frame, "扫荡", region=(790, 610, 980, 710), exact=True)
    frame = ctx.wait(lambda f: f.all("扫荡次数", "剩余次数", "开始扫荡"), "共鸣扫荡设置")
    modal_remaining = field_integer(frame, (670, 445, 840, 500))
    if modal_remaining != remaining:
        raise Halt("扫荡窗口与页面的每周剩余次数不一致，拒绝开始。")
    ctx.text(frame, "MIN", region=(755, 290, 845, 365), exact=True)
    frame = ctx.frame()
    for expected in range(1, count):
        if field_integer(frame, (440, 245, 695, 330)) != expected:
            raise Halt("共鸣次数调整不一致，停止作战。")
        ctx.tap(frame, (725, 237), "增加一次已配置的共鸣扫荡", pause=.2)
        frame = ctx.frame()
    actual, _, _ = sweep_budget(frame, 40, 0)
    if actual != count or field_integer(frame, (670, 445, 840, 500)) != remaining:
        raise Halt("共鸣实际次数与计划或周配额不一致。")
    return frame


def armor(ctx: Context, *, preview_only: bool = False) -> None:
    selected = armor_selection(ctx.maa)
    if not selected:
        ctx.result("装甲进阶", "already_done", "没有设置共鸣配额，跳过。")
        return
    path = ROOT / "runtime/state/armor.json"
    frame = open_armor(ctx)
    remaining = remaining_rewards(frame)
    # A zero is confirmed by this account owner to mean no rewards remain.
    state = armor_ledger(path, remaining, game_week())
    save_state(path, state)
    if remaining == 0:
        ctx.result("装甲进阶", "already_done", "本周剩余奖励次数 0/5，已打满，跳过三种共鸣。", frame)
        return
    completed, skipped = [], []
    for boss, target in selected.items():
        wanted = max(0, target - state["done"].get(boss, 0))
        if not wanted:
            continue
        frame = select_stage(ctx, frame, boss)
        observed = remaining_rewards(frame)
        if observed != remaining:
            raise Halt("选择共鸣期间每周剩余次数发生变化，停止作战。")
        stamina = frame.number_fraction((1080, 0, 1279, 85))
        if stamina is None:
            raise Halt("没有读清现有体力，拒绝共鸣扫荡。")
        count = min(wanted, remaining, max(0, 5 - sum(state["done"].values())), stamina[0] // 40)
        if not count:
            skipped.append("本周奖励次数已用完" if not remaining else f"现有体力 {stamina[0]}，不足一次 40")
            break
        frame = prepare_armor(ctx, frame, count, remaining)
        _, before, expected = sweep_budget(frame, 40, 0)
        evidence = ctx.save(f"armor_{boss}_preview", frame)
        if preview_only:
            ctx.tap(frame, (280, 620), "取消共鸣扫荡预览")
            frame = ctx.wait(lambda f: not f.has("开始扫荡", region=(430, 490, 830, 590)), "扫荡设置关闭")
            completed.append(f"{BOSSES[boss]['name']} {count} 次预览已核对并取消")
            continue
        state["pending"] = {"boss": boss, "quantity": count, "before_remaining": remaining,
                            "before_stamina": before, "expected_stamina": expected, "evidence": evidence}
        save_state(path, state)
        ctx.text(frame, "开始扫荡", region=(490, 500, 790, 580), exact=True,
                 reason=f"扫荡 {BOSSES[boss]['name']} {count} 次，周奖励剩余 {remaining} → {remaining - count}")
        frame = ctx.wait(lambda f: f.settlement_confirm() is not None or f.reward(), "共鸣扫荡结算", timeout=90)
        ctx.save("armor_settlement", frame)
        if frame.reward():
            frame = ctx.dismiss_reward(frame)
        else:
            ctx.text(frame, frame.settlement_confirm().text, region=(420, 560, 850, 670), exact=True)
        frame = ctx.wait(lambda f: f.has("本周可领取奖励次数") and not f.has("扫荡结算"), "共鸣扫荡后详情")
        after_remaining = remaining_rewards(frame)
        after_stamina = frame.number_fraction((1080, 0, 1279, 85))
        if after_remaining != remaining - count or after_stamina is None or not expected <= after_stamina[0] <= expected + 2:
            raise Halt("共鸣结算后的周次数或体力未核对成功；保留待核对记录，不会重复扫荡。")
        state["done"][boss] = state["done"].get(boss, 0) + count
        state["remaining"] = remaining = after_remaining
        state["pending"] = None
        save_state(path, state)
        completed.append(f"{BOSSES[boss]['name']} ×{count}")
        if not remaining:
            break
    ctx.result("装甲进阶", "completed" if completed else "already_done",
               "；".join(completed + skipped) or "勾选共鸣本周配置目标已完成，不重复消耗。", frame)
