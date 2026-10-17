"""Claim the verified zero-cost pack in 首页推荐 -> 通常礼包."""
from __future__ import annotations

import re

from .runtime import Context, Halt, NeedsSetup, ROOT
from .shops import field_integer
from .vision import Frame, normalized, template_match


def gift_page(frame: Frame) -> bool:
    return (frame.has("商城系统", region=(180, 0, 450, 90))
            and frame.has("首页推荐", region=(100, 70, 285, 190), exact=True)
            and frame.has("通常礼包", region=(0, 470, 235, 570), exact=True)
            and not frame.has("是否购买", region=(350, 140, 850, 225))
            and not frame.reward())


def claimed_layout(frame: Frame) -> bool:
    """The claimed pack disappears; verify the known first-page reflow."""
    if not gift_page(frame) or frame.is_loading() or frame.paid_dialog():
        return False
    if frame.has("免费礼包", region=(260, 190, 1279, 700), confidence=.8):
        return False
    return all(frame.has(title, region=region, exact=True, confidence=.9)
               for title, region in (
                   ("艾吉斯·调度补给包1", (270, 195, 530, 245)),
                   ("艾吉斯·调度补给包2", (270, 450, 530, 500)),
                   ("艾吉斯·调度补给包3", (545, 195, 800, 245)),
               ))


def gift_stock(frame: Frame) -> int:
    if not gift_page(frame) or frame.is_loading() or frame.paid_dialog():
        raise Halt("不是已确认的首页推荐礼包列表。")
    title = frame.find("免费礼包", region=(270, 195, 530, 245), exact=True, confidence=.9)
    if title is None:
        if claimed_layout(frame):
            return 0
        raise NeedsSetup("没有识别到免费礼包或已领取后的列表布局，停止领取。")
    if frame.has("已售罄", "售罄", region=(275, 275, 500, 375), exact=True, confidence=.9):
        return 0
    region = (390, 240, 505, 285)
    words = frame.words
    for attempt in range(2):
        values = [int(match[1]) for word in words if word.confidence >= .9
                  and region[0] <= word.center[0] <= region[2] and region[1] <= word.center[1] <= region[3]
                  and (match := re.fullmatch(r"剩余(\d+)", normalized(word.text)))]
        if len(values) == 1 and values[0] in (0, 1):
            stock = values[0]
            break
        if attempt == 0 and frame.vision is not None:
            words = frame.vision.region_words(frame, region)
        else:
            raise Halt("没有读清免费礼包剩余数量，停止领取。")
    if stock == 0:
        return 0
    if field_integer(frame, (380, 395, 450, 440)) != 0:
        raise Halt("免费礼包列表价格不是0，拒绝领取。")
    if not template_match(frame, ROOT / "assets/free_gift_currency_list.png", (325, 390, 385, 445), .88):
        raise Halt("免费礼包零价格对应的货币图标不明确。")
    return stock


def free_confirmation(frame: Frame) -> None:
    if frame.is_loading() or frame.paid_dialog():
        raise Halt("出现加载或付费提示，拒绝领取礼包。")
    title = frame.find("是否购买", region=(350, 140, 850, 225), confidence=.9)
    if title is None or normalized(title.text) != "是否购买免费礼包?":
        raise Halt("确认窗口不是免费礼包，拒绝领取。")
    if not frame.has("礼包包含", region=(500, 200, 790, 250), exact=True, confidence=.9):
        raise Halt("免费礼包内容窗口未识别完整。")
    if field_integer(frame, (795, 165, 1045, 215), stock=True) != 1:
        raise Halt("免费礼包确认窗口剩余数量不为1。")
    if field_integer(frame, (640, 420, 845, 466)) != 0:
        raise Halt("免费礼包确认消耗不是0，拒绝确认。")
    if not template_match(frame, ROOT / "assets/free_gift_currency_dialog.png", (590, 414, 646, 470), .88):
        raise Halt("免费礼包确认窗口货币图标不明确。")
    for text, region in (("取消", (300, 460, 620, 550)), ("确认", (680, 460, 950, 550))):
        if frame.find(text, region=region, exact=True, confidence=.9) is None:
            raise Halt("免费礼包确认窗口按钮未识别完整。")


def free_gifts(ctx: Context) -> None:
    frame = ctx.home()
    ctx.text(frame, "商城", region=(700, 630, 850, 719), exact=True)
    frame = ctx.wait(lambda f: f.has("商城系统", region=(180, 0, 450, 90)), "商城系统")
    ctx.text(frame, "首页推荐", region=(100, 70, 285, 190), exact=True)
    frame = ctx.wait(lambda f: f.has("通常礼包", region=(0, 470, 235, 570), exact=True), "首页推荐入口")
    ctx.text(frame, "通常礼包", region=(0, 470, 235, 570), exact=True)
    frame = ctx.wait(gift_page, "通常礼包列表")
    for _ in range(6):
        if frame.has("免费礼包", region=(270, 195, 530, 245), exact=True, confidence=.9) or claimed_layout(frame):
            break
        ctx.swipe(frame, (530, 440), (1050, 440), "返回通常礼包列表起点", duration=1200)
        frame = ctx.wait(gift_page, "通常礼包列表")
    stock = gift_stock(frame)
    ctx.save("free_gift_before", frame)
    if stock == 0:
        ctx.result("首页推荐免费礼包", "already_done", "免费礼包已领取／无剩余，跳过。", frame)
        return
    ctx.text(frame, "免费礼包", region=(270, 195, 530, 245), exact=True, reason="查看免费礼包，列表价格0")
    frame = ctx.wait(lambda f: f.has("是否购买", region=(350, 140, 850, 225)), "免费礼包领取确认")
    free_confirmation(frame)
    ctx.save("free_gift_zero_cost", frame)
    ctx.text(frame, "确认", region=(680, 460, 950, 550), exact=True, reason="领取免费礼包，已核对消耗为0")
    frame = ctx.wait(lambda f: f.reward(), "免费礼包资源奖励", timeout=25)
    ctx.save("free_gift_reward", frame)
    frame = ctx.dismiss_reward(frame)
    frame = ctx.wait(gift_page, "领取后礼包列表")
    if gift_stock(frame) != 0:
        raise Halt("免费礼包已提交，但未确认礼包下架或无剩余，已停止，不会再次确认。")
    ctx.result("首页推荐免费礼包", "completed", "已领取免费礼包1份，确认消耗0、奖励出现，并复核礼包已下架或无剩余。", frame)
