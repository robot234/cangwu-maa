from __future__ import annotations

from datetime import datetime
import re
import time

import numpy as np

from .runtime import CST, ROOT, Context, Halt, NeedsSetup
from .vision import Frame, Word, normalized, template_match

CLAIM_ALL = ("一键领取", "-键领取", "键领取", "全部领取", "一键收取", "领取全部")


def integer(frame: Frame, region: tuple[int, int, int, int]) -> int | None:
    for word in frame.words:
        x, y = word.center
        if region[0] <= x <= region[2] and region[1] <= y <= region[3] and word.confidence >= .85:
            if re.fullmatch(r"\d+", normalized(word.text)):
                return int(normalized(word.text))
    return None


# Actions kept back after a stamina/ticket spend for its settlement, the
# after-spend check and returning to the lobby.
EVENT_RESERVE = 15
ARENA_RESERVE = 25


def spend_budget(ctx: Context, steps: int, reserve: int, what: str) -> None:
    remaining = int(ctx.config["limits"]["max_actions"]) - int(ctx.actions)
    if remaining < steps + reserve:
        raise Halt(f"本轮剩余操作次数 {remaining} 次，不够完整走完{what}（约需 {steps + reserve} 次）；"
                   "没有开始，也没有消耗。")


def sweep_budget(frame: Frame, cost: int, reserve: int) -> tuple[int, int, int]:
    if not frame.all("扫荡次数", "剩余次数", "开始扫荡") or frame.paid_dialog():
        raise Halt("不是已确认的活动扫荡窗口，拒绝开始。")
    count = integer(frame, (440, 245, 695, 330))
    pair = None
    for word in frame.words:
        x, y = word.center
        if 590 <= x <= 835 and 385 <= y <= 450 and word.confidence >= .85:
            match = re.fullmatch(r"(\d+)[>»→]+(\d+)", normalized(word.text))
            if match:
                pair = int(match[1]), int(match[2])
    if pair is None:
        # Maa may return "44>>" and "2" as separate, adjacent OCR boxes.
        parts = sorted((word for word in frame.words if word.confidence >= .85
                        and 590 <= word.center[0] <= 835 and 385 <= word.center[1] <= 450),
                       key=lambda word: word.center[0])
        if parts and max(word.center[1] for word in parts) - min(word.center[1] for word in parts) <= 14:
            match = re.fullmatch(r"(\d+)[>»→]+(\d+)", "".join(normalized(word.text) for word in parts))
            if match:
                pair = int(match[1]), int(match[2])
    if count is None or pair is None:
        raise Halt("没有读清扫荡次数和体力消耗，拒绝开始。")
    before, after = pair
    visible = frame.number_fraction((1080, 0, 1279, 85))
    if not 1 <= count <= 100 or cost <= 0 or before - after != cost * count or after < reserve:
        raise Halt("扫荡消耗与配置不一致，拒绝开始。")
    if visible is None or visible[0] != before:
        raise Halt("扫荡预览与当前体力不一致，拒绝开始。")
    return count, before, after


def arena_budget(frame: Frame, maximum: int) -> tuple[int, int]:
    if not frame.all("代理次数", "代理消耗", "开始代理") or frame.paid_dialog():
        raise Halt("不是已确认的竞技场代理窗口，拒绝开始。")
    count = integer(frame, (440, 245, 695, 330))
    consumption = frame.number_fraction((680, 445, 835, 510))
    tickets = frame.number_fraction((1080, 0, 1279, 85))
    if count is None or consumption is None or tickets is None:
        raise Halt("没有读清竞技场次数或现有票数，拒绝开始。")
    if not 1 <= count <= maximum or consumption != (count, tickets[0]) or count > tickets[0]:
        raise Halt("竞技场消耗超过现有票数或本次上限，拒绝开始。")
    return count, tickets[0]


def base(ctx: Context) -> None:
    frame = ctx.home()
    ctx.text(frame, "指挥室", region=(30, 630, 210, 719))
    frame = ctx.wait(lambda f: f.all("战斗训练", "体能锻炼", "物资生产"), "指挥室")
    ctx.save("base_before", frame)
    ctx.text(frame, "全部收集", region=(1080, 130, 1279, 240))
    frame = ctx.wait(lambda f: f.reward() or f.has("暂无可收集", "没有可收集") or f.all("战斗训练", "体能锻炼", "物资生产"), "指挥室收集结果")
    claimed = frame.reward()
    frame = ctx.dismiss_reward(frame)
    ctx.result("指挥室收菜", "completed" if claimed else "already_done", "已收集设施资源。" if claimed else "已检查全部收集，没有出现新的资源奖励。", frame)


def expedition_conditions(frame: Frame) -> bool:
    if not frame.all("远征任务", "队伍需求"):
        return False
    # All three cyan check marks must be present; a missing or red mark fails.
    for x1, y1, x2, y2 in ((618, 415, 650, 453), (882, 415, 918, 453), (1146, 415, 1181, 453)):
        patch = frame.image[y1:y2, x1:x2].astype(np.int16)
        blue, green, red = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
        if np.count_nonzero((blue > 120) & (green > 135) & (red < 110) & (green > red + 60)) < 12:
            return False
    return True


def expedition_repeat_dialog(frame: Frame) -> bool:
    return (frame.has("远征探索", region=(500, 70, 800, 130), exact=True)
            and frame.has("再次派遣", region=(635, 550, 890, 640), exact=True)
            and frame.has("取消", region=(350, 550, 620, 640), exact=True))


def expedition_repeat_plan(frame: Frame, config: dict) -> list[str]:
    if not expedition_repeat_dialog(frame) or frame.is_loading() or frame.paid_dialog():
        raise Halt("不是已确认的远征续派窗口，拒绝派遣。")
    allowed = {normalized(name): name for name in config["missions"]}
    missions = []
    for y1, y2 in ((135, 245), (245, 355)):
        words = sorted((word for word in frame.words if word.confidence >= .85
                        and 275 <= word.center[0] <= 580 and y1 <= word.center[1] < y2),
                       key=lambda word: word.center[0])
        if not words:
            continue
        line = "".join(normalized(word.text) for word in words)
        match = re.fullmatch(r"(.+?)(\d+(?:\.\d+)?)小时", line)
        if match is None or match[1] not in allowed:
            raise NeedsSetup("远征续派列表中有未配置或未读清的任务，停止续派。")
        if float(match[2]) != int(config.get("hours", 12)):
            raise NeedsSetup("远征上次时长与本轮设置不同，停止续派，请先在游戏中核对。")
        mission = allowed[match[1]]
        if mission in missions:
            raise Halt("远征续派列表重复，拒绝派遣。")
        missions.append(mission)
    if not missions:
        raise Halt("远征续派列表未读清，拒绝派遣。")
    return missions


def repeat_expeditions(ctx: Context, frame: Frame, config: dict) -> tuple[Frame, int]:
    # The game reuses the teams of these completed expeditions. Never replace
    # a team or repeat an unconfigured mission or a different duration.
    missions = expedition_repeat_plan(frame, config)
    ctx.save("expedition_repeat", frame)
    ctx.text(frame, "再次派遣", region=(635, 550, 890, 640), exact=True,
             reason=f"沿用原队伍续派 {len(missions)} 项远征（{config.get('hours', 12)}小时）")
    frame = ctx.wait(lambda f: f.all("远征任务", "队伍需求") and not expedition_repeat_dialog(f)
                     and (teams := f.number_fraction((65, 70, 380, 140))) is not None
                     and len(missions) <= teams[0] <= teams[1], "远征续派完成")
    for mission in missions:
        ctx.text(frame, mission, region=(65, 160, 378, 585), exact=True, reason=f"核对续派远征：{mission}")
        frame = ctx.wait(lambda f: f.all("远征任务", "队伍需求") and f.has("剩余时间")
                         and f.has("取消", region=(1000, 540, 1225, 640), exact=True), "远征运行状态")
    ctx.save("expedition_redispatched", frame)
    return frame, len(missions)


def expedition(ctx: Context) -> None:
    config = ctx.config.get("expedition", {"hours": 12, "missions": []})
    frame = ctx.wait(lambda f: True, "远征页面加载完成")
    if expedition_repeat_dialog(frame):
        frame, dispatched = repeat_expeditions(ctx, frame, config)
        ctx.result("远征", "completed", f"恢复上次领取后的续派，{dispatched} 项远征已沿用原队伍开始（{config.get('hours', 12)}小时）。", frame)
        return
    frame = ctx.home()
    ctx.text(frame, "指挥室", region=(30, 630, 210, 719))
    frame = ctx.wait(lambda f: f.all("战斗训练", "体能锻炼", "远征探索"), "指挥室远征入口")
    ctx.text(frame, "远征探索", region=(560, 140, 800, 250))
    frame = ctx.wait(lambda f: f.all("远征任务", "队伍需求"), "远征任务列表")
    collected, dispatched = 0, 0
    for mission in config["missions"]:
        row = frame.find(mission, region=(65, 160, 378, 585), exact=True)
        if row is None:
            raise NeedsSetup(f"远征任务「{mission}」未识别到，需要重新核对任务名称。")
        ctx.tap(frame, row.center, f"查看远征：{mission}")
        frame = ctx.wait(lambda f: f.all("远征任务", "队伍需求"), "远征详情")
        ctx.save("expedition_" + mission, frame)
        # Never withdraw or replace an expedition that is still running.
        if frame.has("取消", region=(1000, 540, 1225, 640), exact=True) and frame.has("剩余时间"):
            continue
        claim = frame.find("点击领取", "领取", "领取奖励", "领取报酬", region=(1000, 540, 1225, 640), exact=True)
        if claim:
            ctx.tap(frame, claim.center, "领取已完成的远征奖励")
            frame = ctx.wait(lambda f: f.reward() or expedition_repeat_dialog(f), "远征奖励与续派确认")
            if expedition_repeat_dialog(frame):
                frame, count = repeat_expeditions(ctx, frame, config)
                collected += count
                dispatched += count
                continue
            collected += 1
            frame = ctx.dismiss_reward(frame)
            if not frame.has("远征任务"):
                ctx.text(frame, "远征探索", region=(560, 140, 800, 250))
                frame = ctx.wait(lambda f: f.has("远征任务"), "远征任务列表")
                ctx.text(frame, mission, region=(65, 160, 378, 585), exact=True)
                frame = ctx.wait(lambda f: f.all("远征任务", "队伍需求"), "远征详情")
        teams = frame.number_fraction((65, 70, 380, 140))
        if teams is None:
            raise Halt("远征队伍数量未读清，停止续派。")
        if teams[0] >= teams[1]:
            raise NeedsSetup(f"远征「{mission}」队伍已满，但没有识别到运行状态或领取按钮，已保留截图。")
        button = frame.find("派遣", "开始远征", "确认派遣", region=(1000, 540, 1225, 640), exact=True)
        if button is None:
            raise NeedsSetup("空闲远征未识别到派遣按钮，请手动检查一次。")
        if not expedition_conditions(frame):
            raise NeedsSetup(f"远征「{mission}」的已保存队伍不满足条件；请在游戏中配好一次队伍。")
        hours = int(config.get("hours", 12))
        if hours not in (4, 8, 12):
            raise NeedsSetup("远征时长只能为 4、8 或 12 小时。")
        ctx.text(frame, f"{hours}小时", region=(390, 540, 840, 635), exact=True)
        frame = ctx.frame()
        if not expedition_conditions(frame):
            raise Halt("远征条件发生变化，拒绝派遣。")
        ctx.text(frame, "派遣", "开始远征", "确认派遣", region=(1000, 540, 1225, 640), exact=True)
        frame = ctx.wait(lambda f: f.has("取消", region=(1000, 540, 1225, 640), exact=True) and (v := f.number_fraction((65, 70, 380, 140))) is not None and v[0] == teams[0] + 1, "远征已开始")
        dispatched += 1
    detail = f"收取 {collected} 次，续派 {dispatched} 次（{config.get('hours', 12)}小时）；运行中的远征保持原样。"
    ctx.result("远征", "completed" if collected or dispatched else "already_done", detail, frame)


def event(ctx: Context) -> None:
    config = ctx.config["event"]
    if datetime.now(CST) >= datetime.fromisoformat(config["expires_at"]):
        raise NeedsSetup("当前活动配置已到期；请重新选择活动入口和关卡。")
    frame = ctx.home()
    entry = template_match(frame, ROOT / config["entry_template"], (0, 60, 300, 390), .88)
    if entry is None:
        raise NeedsSetup("没有识别到已记录的活动横幅，可能已经换活动；请重新记录活动入口。")
    ctx.tap(frame, entry, f"进入活动：{config['name']}")
    frame = ctx.wait(lambda f: f.has("限时活动", region=(230, 0, 440, 90)) and f.has(config["chapter"]), "活动关卡地图")
    if not frame.has(config["stage"], region=(350, 70, 1100, 600)):
        chapter = frame.find(config["chapter"], region=(1140, 180, 1279, 470), exact=True)
        if chapter:
            ctx.tap(frame, chapter.center, f"选择章节：{config['chapter']}")
        frame = ctx.wait(lambda f: f.has(config["stage"], region=(350, 70, 1100, 600)), "指定活动关卡")
    ctx.text(frame, config["stage"], region=(350, 70, 1100, 600), reason=f"选择 {config['stage']}")
    frame = ctx.wait(lambda f: f.has("扫荡", region=(790, 600, 970, 710)) and f.has("作战准备"), "活动关卡详情")
    if not frame.has(config["stage"], region=(790, 70, 1279, 180)):
        raise Halt("关卡详情不是指定关卡，拒绝消耗体力。")
    cost, reserve = int(config["stamina_cost"]), int(config.get("reserve_stamina", 0))
    stamina = frame.number_fraction((1080, 0, 1279, 85))
    if stamina is None:
        raise Halt("无法读清现有体力，拒绝扫荡。")
    count = min(100, max(0, (stamina[0] - reserve) // cost))
    if count == 0:
        ctx.result("活动扫荡", "already_done", f"体力 {stamina[0]}，不足一次 {cost}；没有购买体力。", frame)
        return
    use_max = reserve == 0 and count < 100
    # Open the sweep dialog only if the whole spend can also be settled and verified.
    spend_budget(ctx, 2 + (1 if use_max else count), EVENT_RESERVE, "活动扫荡")
    ctx.text(frame, "扫荡", region=(790, 600, 970, 710), exact=True)
    frame = ctx.wait(lambda f: f.all("扫荡次数", "开始扫荡"), "扫荡次数设置")
    if use_max:
        ctx.text(frame, "MAX", region=(755, 200, 845, 270), exact=True)
    else:
        ctx.text(frame, "MIN", region=(755, 290, 845, 365), exact=True)
        for _ in range(count - 1):
            frame = ctx.frame()
            if not frame.all("扫荡次数", "开始扫荡"):
                raise Halt("扫荡设置窗口发生变化，停止操作。")
            ctx.tap(frame, (725, 237), "增加一次扫荡", pause=.25)
    frame = ctx.frame()
    actual, before, expected = sweep_budget(frame, cost, reserve)
    if actual != count:
        raise Halt(f"实际扫荡 {actual} 次，与计划 {count} 次不同，拒绝开始。")
    ctx.save("sweep_budget", frame)
    ctx.text(frame, "开始扫荡", region=(490, 500, 790, 580), reason=f"扫荡 {actual} 次，预计体力 {before} → {expected}")
    frame = ctx.wait(lambda f: (f.has("扫荡结算") and f.has("确认", "确定", region=(430, 560, 850, 660))) or f.reward(), "活动扫荡结算与确认按钮", timeout=90)
    ctx.save("event_settlement", frame)
    if frame.reward():
        ctx.dismiss_reward(frame)
    else:
        ctx.text(frame, "确认", "确定", region=(430, 560, 850, 660), reason="确认活动扫荡结算")
    frame = ctx.wait(lambda f: f.has("限时活动") and not f.has("扫荡结算"), "返回活动地图")
    after = frame.number_fraction((1080, 0, 1279, 85))
    if after is None or not expected <= after[0] <= expected + 2:
        raise Halt("扫荡后体力未能核对；已停止，不会重复提交扫荡。")
    ctx.result("活动扫荡", "completed", f"{config['stage']} 已扫荡 {actual} 次，体力 {before} → {after[0]}。", frame)


ENDURANCE_TAB = "续航计划"
ENDURANCE_TABS = (0, 150, 215, 650)


def endurance_page(frame: Frame) -> bool:
    return (frame.has("累计体力消耗", region=(560, 40, 900, 100))
            and frame.has(ENDURANCE_TAB, region=ENDURANCE_TABS, exact=True)
            and not frame.reward())


def endurance(ctx: Context) -> None:
    """Limited-time 续航计划: claim stamina-spend milestones; skip once the event is gone."""
    frame = ctx.home()
    ctx.text(frame, "活动", region=(1150, 170, 1279, 260), exact=True, reason="打开活动合集")
    frame = ctx.wait(lambda f: f.activity_hub(), "活动合集页面")
    tab = frame.find(ENDURANCE_TAB, region=ENDURANCE_TABS, exact=True)
    # The tab column may scroll; look both ways before deciding the event ended.
    for start, end in (((105, 300), (105, 600)), ((105, 600), (105, 250))):
        if tab is not None:
            break
        ctx.swipe(frame, start, end, "翻动活动列表，查找续航计划", duration=600)
        frame = ctx.wait(lambda f: f.activity_hub(), "活动合集页面")
        tab = frame.find(ENDURANCE_TAB, region=ENDURANCE_TABS, exact=True)
    if tab is None:
        ctx.result("续航计划", "already_done", "活动合集里没有续航计划，限时活动可能已结束，跳过。", frame)
        return
    ctx.tap(frame, tab.center, "切换到续航计划")
    frame = ctx.wait(endurance_page, "续航计划任务列表")
    spent = frame.find("累计体力消耗", region=(560, 40, 900, 100))
    progress = normalized(spent.text).replace("累计体力消耗", "") if spent else ""
    ctx.save("endurance_before", frame)
    button = frame.find(*CLAIM_ALL, region=(1040, 620, 1279, 710), exact=True)
    if button is None:
        raise NeedsSetup("续航计划页面没有识别到一键领取按钮，已保留截图。")
    # The disabled and enabled button share the page's amber tint, so tap and
    # judge by the outcome; the claim is free and an empty tap changes nothing.
    ctx.tap(frame, button.center, "续航计划：一键领取", pause=.6)
    claimed = False
    until = time.monotonic() + 4
    while time.monotonic() < until:
        frame = ctx.frame()
        if frame.reward():
            claimed = True
            frame = ctx.dismiss_reward(frame)
            break
        ctx.pause(.5)
    frame = ctx.wait(endurance_page, "续航计划任务列表")
    total = f"累计体力消耗 {progress}；" if progress else ""
    ctx.result("续航计划", "completed" if claimed else "already_done",
               total + ("已领取达成的体力消耗奖励。" if claimed else "当前没有新达成的奖励可领。"), frame)


def arena_page(frame: Frame) -> bool:
    if frame.has("作战演习", region=(180, 0, 390, 90)):
        return True
    # The reward overlay can hide the page title. These independent arena
    # controls remain readable in its dimmed background.
    return (frame.has("大师赛", region=(0, 450, 150, 550), exact=True)
            and frame.has("名人堂", region=(0, 650, 130, 719), exact=True)
            and frame.has("防御记录", region=(1000, 70, 1250, 140), exact=True)
            and frame.has("作战准备", region=(1000, 610, 1279, 710), exact=True))


def arena_settle(ctx: Context, frame: Frame) -> Frame:
    if not (frame.has("代理完成", region=(790, 610, 1279, 710)) or frame.has("扫荡结算", region=(420, 35, 850, 130))):
        return frame
    deadline = min(ctx.deadline, time.monotonic() + 180)
    # Refresh can close a result dialog while more completed results remain.
    # Drain only that existing proxy; never click the new-proxy button here.
    for receipt in range(16):
        opening_deadline = min(deadline, time.monotonic() + 35)
        attempts, next_retry = 0, 0.0
        while time.monotonic() < opening_deadline:
            if frame.settlement_confirm() is not None:
                break
            if not frame.is_loading() and attempts < 3 and time.monotonic() >= next_retry and arena_page(frame) and frame.has("代理完成", region=(790, 610, 1279, 710)):
                ctx.text(frame, "代理完成", region=(790, 610, 1279, 710), reason="领取已完成的竞技场代理结果")
                attempts += 1
                next_retry = time.monotonic() + 2
            else:
                ctx.pause(.6)
            frame = ctx.frame()
        else:
            raise Halt("竞技场领取结果没有打开，已停止，不会再次提交代理。")
        ctx.save("arena_settlement", frame)
        for attempt in range(2):
            button = frame.settlement_confirm()
            if button is None:
                raise Halt("竞技场结算确认按钮已变化，停止领取。")
            ctx.text(frame, button.text, region=(420, 560, 850, 670), exact=True,
                     reason="确认竞技场结算" if not attempt else "竞技场结算未收起，核对原弹窗后补点一次")
            until = min(deadline, time.monotonic() + 8)
            while time.monotonic() < until:
                frame = ctx.frame()
                if (not frame.is_loading() and arena_page(frame)
                        and not frame.has("扫荡结算", region=(420, 35, 850, 130))
                        and not frame.reward()
                        and not frame.has("网络异常", "连接失败", "重新连接", "重新登录", "重试", region=(250, 180, 1080, 540))):
                    if frame.has("代理作战", region=(780, 610, 1010, 710)):
                        return frame
                    if frame.has("代理完成", region=(790, 610, 1279, 710)):
                        break
                ctx.pause(.6)
            else:
                if (frame.settlement_confirm() is None or frame.has(
                        "网络异常", "连接失败", "重新连接", "重新登录", "重试", region=(250, 180, 1080, 540))):
                    break
                continue
            # The recognized result dialog closed. Reopen only the remaining
            # completed results instead of retrying a stale confirmation.
            ctx.log(f"竞技场第 {receipt + 1} 批结算已收起，继续核对剩余代理结果。")
            break
        else:
            ctx.save("arena_settlement_no_response", frame)
            raise Halt("竞技场结算确认未响应，已停止；不会再次提交代理。")
        if (frame.settlement_confirm() is None and not frame.is_loading() and arena_page(frame)
                and frame.has("代理完成", region=(790, 610, 1279, 710))
                and not frame.reward()
                and not frame.has("网络异常", "连接失败", "重新连接", "重新登录", "重试", region=(250, 180, 1080, 540))):
            continue
        ctx.save("arena_settlement_no_response", frame)
        raise Halt("竞技场结算确认未响应，已停止；不会再次提交代理。")
    ctx.save("arena_settlement_remaining", frame)
    raise Halt("竞技场结算仍有未领取结果，已达到本次核对上限；不会再次提交代理。")


def arena(ctx: Context) -> None:
    frame = ctx.frame()
    if arena_page(frame) and (
            frame.has("扫荡结算", region=(420, 35, 850, 130))
            or frame.has("代理完成", "中止代理", region=(790, 610, 1279, 710))):
        if not frame.has("扫荡结算", region=(420, 35, 850, 130)) and not frame.has("代理完成", region=(790, 610, 1279, 710)):
            frame = ctx.wait(lambda f: f.has("代理完成", region=(790, 610, 1279, 710)), "已有的竞技场代理完成", timeout=360)
        frame = arena_settle(ctx, frame)
        ctx.result("竞技场", "completed", "已收取已有竞技场代理的结算，本轮不再提交新的代理。", frame)
        return
    frame = ctx.home()
    ctx.text(frame, "演习", region=(1020, 540, 1279, 625), exact=True)
    frame = ctx.wait(lambda f: f.has("作战演习", region=(180, 0, 390, 90)), "竞技场")
    frame = arena_settle(ctx, frame)
    # A proxy left running by an earlier session must finish before a new one can start.
    if not frame.has("代理作战", region=(780, 610, 1010, 710)):
        frame = ctx.wait(lambda f: f.has("代理完成", region=(790, 610, 1279, 710)), "已有的竞技场代理完成", timeout=360)
        frame = arena_settle(ctx, frame)
    tickets = frame.number_fraction((1080, 0, 1279, 85))
    maximum = min(15, max(0, int(ctx.config["arena"]["max_tickets"])))
    if tickets is None:
        raise Halt("无法读清竞技场现有票数，停止操作。")
    count = min(tickets[0], maximum)
    if count == 0:
        ctx.result("竞技场", "already_done", f"现有票 {tickets[0]}，本次上限 {maximum}；没有购买次数。", frame)
        return
    spend_budget(ctx, 2 + (1 if count == tickets[0] else count), ARENA_RESERVE, "竞技场代理")
    ctx.text(frame, "代理作战", region=(780, 610, 1010, 710), exact=True)
    frame = ctx.wait(lambda f: f.all("代理次数", "代理消耗", "开始代理"), "竞技场代理次数")
    if count == tickets[0]:
        ctx.text(frame, "MAX", region=(755, 200, 845, 270), exact=True)
    else:
        ctx.text(frame, "MIN", region=(755, 290, 845, 365), exact=True)
        for _ in range(count - 1):
            frame = ctx.frame()
            if not frame.all("代理次数", "开始代理"):
                raise Halt("代理窗口发生变化，停止操作。")
            ctx.tap(frame, (725, 237), "增加一次竞技场代理", pause=.25)
    frame = ctx.frame()
    actual, before = arena_budget(frame, maximum)
    if actual != count:
        raise Halt("竞技场预览次数与计划不符，拒绝开始。")
    ctx.save("arena_budget", frame)
    ctx.text(frame, "开始代理", region=(490, 500, 790, 580), reason=f"启动 {actual} 次竞技场代理，只使用现有票")
    frame = ctx.wait(lambda f: f.has("作战演习") and not f.has("开始代理", region=(490, 500, 790, 580)), "竞技场代理已启动")
    ctx.save("arena_started", frame)
    ctx.log(f"竞技场代理已开始，预计约 {actual * 15} 秒；等待游戏结算。")
    frame = ctx.wait(lambda f: f.has("代理完成", region=(790, 610, 1279, 710)), "竞技场代理完成", timeout=min(600, actual * 15 + 100))
    frame = arena_settle(ctx, frame)
    after = frame.number_fraction((1080, 0, 1279, 85))
    if after is None or not before - actual <= after[0] <= before - actual + 1:
        raise Halt("竞技场票数未能核对，已停止，不会再次启动代理。")
    ctx.result("竞技场", "completed", f"已完成并结算 {actual} 次代理，票数 {before} → {after[0]}。", frame)


def collect_rewards(ctx: Context, name: str, frame: Frame) -> None:
    claimed = 0
    for _ in range(5):
        if frame.reward():
            claimed += 1
            frame = ctx.dismiss_reward(frame)
        if frame.has("暂无邮件", "没有邮件", "邮件为空", "暂无可领取", "无可领取", "没有可领取", "已全部领取"):
            ctx.result(name, "completed" if claimed else "already_done", "已领取奖励。" if claimed else "当前没有可领取的奖励。", frame)
            return
        button = frame.find(*CLAIM_ALL, exact=True)
        if button is None:
            raise NeedsSetup(f"{name}页面未识别到批量领取按钮，已保留截图。")
        if not frame.colored(button, "orange") and not frame.colored(button, "blue"):
            ctx.result(name, "completed" if claimed else "already_done", "已领取当前可领的奖励。" if claimed else "批量领取按钮为未激活状态。", frame)
            return
        ctx.tap(frame, button.center, f"{name}：批量领取")
        frame = ctx.wait(lambda f: f.reward() or f.has("暂无可领取", "无可领取", "没有可领取", "已全部领取") or ((b := f.find(*CLAIM_ALL, exact=True)) is not None and not f.colored(b, "orange") and not f.colored(b, "blue")), f"{name}领取结果")
    raise Halt(f"{name}连续领取超过上限，停止操作。")


def mail(ctx: Context) -> None:
    frame = ctx.home()
    ctx.tap(frame, (1143, 35), "打开邮件")
    frame = ctx.wait(lambda f: f.has("邮件", region=(150, 0, 450, 100)), "邮件页面")
    if frame.has("暂无邮件", "没有邮件", "邮件为空"):
        ctx.result("邮件", "already_done", "当前没有邮件。", frame)
        return
    button = frame.find("领取全部", "一键领取", "全部领取", exact=True)
    if button is None:
        raise NeedsSetup("邮件页面未识别到领取全部按钮。")
    ctx.tap(frame, button.center, "领取全部邮件附件")
    frame = ctx.wait(lambda f: f.reward() or f.has("邮件系统", region=(180, 0, 450, 100)), "邮件领取结果")
    if frame.reward():
        frame = ctx.dismiss_reward(frame)
        ctx.result("邮件", "completed", "已领取邮件附件。", frame)
    else:
        ctx.result("邮件", "already_done", "已检查领取全部，没有出现新的附件奖励。", frame)


def friend_stamina(frame: Frame) -> tuple[int, int] | None:
    if not frame.has("好友系统", region=(180, 0, 450, 100)):
        return None
    value = frame.number_fraction((420, 0, 750, 100))
    return value if value and 0 <= value[0] <= value[1] and value[1] > 0 else None


def friends(ctx: Context) -> None:
    frame = ctx.home()
    ctx.text(frame, "好友", region=(1140, 340, 1279, 450), exact=True)
    frame = ctx.wait(lambda f: f.has("好友系统", region=(180, 0, 450, 100)), "好友页面")
    before = friend_stamina(frame)
    if before and before[0] >= before[1]:
        ctx.result("好友点赞", "already_done", f"今日好友体力已领取 {before[0]}/{before[1]}。", frame)
        return
    button = frame.find("一键点赞", "全部点赞", exact=True)
    if button is None:
        if frame.has("暂无好友", "没有好友"):
            ctx.result("好友点赞", "already_done", "当前没有好友。", frame)
            return
        raise NeedsSetup("好友页面未识别到一键点赞按钮。")
    if before is None:
        raise Halt("无法读清今日好友体力领取数，停止操作。")

    def confirmed(screen: Frame) -> bool:
        current = friend_stamina(screen)
        if current is None or screen.is_loading():
            return False
        if current[1] != before[1] or current[0] < before[0]:
            raise Halt("好友体力计数发生异常变化，已停止点赞。")
        if current[0] > before[0]:
            ctx.result("好友点赞", "completed", f"已完成一键点赞，好友体力 {before[0]}/{before[1]} → {current[0]}/{current[1]}。", screen)
            return True
        if screen.has("没有可点赞", "暂无可点赞", "今日点赞已满", "今日已全部点赞", region=(250, 200, 1080, 520)):
            ctx.result("好友点赞", "already_done", "游戏提示当前没有可点赞的好友。", screen)
            return True
        return False

    for attempt in range(2):
        if attempt:
            frame = ctx.frame()
            if confirmed(frame):
                return
            if frame.is_loading() or friend_stamina(frame) != before or frame.reward() or frame.has(
                "网络异常", "连接失败", "重新连接", "重新登录", "重试", region=(250, 180, 1080, 540)
            ):
                raise Halt("未能确认好友页仍可点赞，已停止补点。")
            button = frame.find("一键点赞", "全部点赞", region=(0, 590, 300, 710), exact=True)
            if button is None:
                raise Halt("好友点赞按钮已变化，已停止补点。")
        ctx.tap(frame, button.center, "给好友一键点赞" if not attempt else "好友点赞未响应，核对原页面后补点一次")
        until = min(ctx.deadline, time.monotonic() + 8)
        while time.monotonic() < until:
            frame = ctx.frame()
            if confirmed(frame):
                return
            ctx.pause(.6)
    ctx.save("friends_no_response", frame)
    raise Halt("好友点赞两次均未得到游戏确认，已停止后续任务；请检查游戏连接后重试。")


MISSION_POINT_TIERS = {
    "每日任务": (100,),
    "每周任务": (90, 150),
    "每月任务": (150, 300, 450),
}
MISSION_POINT_RECEIVED = ("已领取", "已获取")


def mission_point_claims(frame: Frame, period: str) -> list[Word]:
    if (frame.is_loading() or frame.paid_dialog()
            or not frame.all("每日任务", "每周任务", "每月任务")
            or not frame.has("积分点数", region=(20, 610, 450, 680), exact=True)):
        raise Halt("不是已确认的任务积分奖励页面，拒绝领取。")
    expected = MISSION_POINT_TIERS[period]
    points = frame.number_fraction((120, 600, 330, 680))
    if points is None:
        # Maa can split the gold total and white denominator into adjacent boxes.
        parts = sorted((word for word in frame.words if word.confidence >= .85
                        and 120 <= word.center[0] <= 330 and 600 <= word.center[1] <= 680
                        and re.fullmatch(r"[0-9/]+", normalized(word.text))), key=lambda word: word.center[0])
        if (2 <= len(parts) <= 3 and max(word.center[1] for word in parts) - min(word.center[1] for word in parts) <= 12
                and all(right.box[0] - left.box[2] <= 24 for left, right in zip(parts, parts[1:]))):
            match = re.fullmatch(r"(\d+)/(\d+)", "".join(normalized(word.text) for word in parts))
            if match:
                points = int(match[1]), int(match[2])
    if points is None or points[1] != expected[-1]:
        raise Halt(f"{period}积分数量或页面未读清，停止领取。")
    tiers = []
    for word in frame.words:
        x, y = word.center
        if word.confidence < .85 or not (450 <= x <= 950 and 645 <= y <= 680):
            continue
        match = re.fullmatch(r"(\d+)积分", normalized(word.text))
        if match:
            tiers.append((int(match[1]), word))
    tiers.sort(key=lambda item: item[1].center[0])
    if tuple(value for value, _ in tiers) != expected:
        raise NeedsSetup(f"{period}底部积分档位未读清或发生变化，已保留截图。")
    claims = []
    for needed, tier in tiers:
        x = tier.center[0]
        region = (x - 55, 600, x + 55, 645)
        claim = frame.find("可领取", region=region, exact=True, confidence=.85)
        received = frame.find(*MISSION_POINT_RECEIVED, region=region, exact=True, confidence=.85)
        if claim and received:
            raise Halt(f"{period}{needed}积分奖励状态冲突，停止领取。")
        if claim and points[0] < needed:
            raise Halt(f"{period}积分不足却显示可领取，停止领取。")
        if points[0] >= needed and not (claim or received):
            raise Halt(f"{period}{needed}积分已达到，但奖励状态未读清，停止领取。")
        if claim:
            claims.append(claim)
    return claims


def collect_mission_points(ctx: Context, period: str, frame: Frame) -> Frame:
    claimed = 0
    for _ in range(len(MISSION_POINT_TIERS[period]) + 1):
        claims = mission_point_claims(frame, period)
        if not claims:
            ctx.result(f"{period}积分奖励", "completed" if claimed else "already_done",
                       f"已领取 {claimed} 档积分奖励，并确认已领取／已获取状态。" if claimed else "已核对全部积分档位，当前没有可领取的积分奖励。", frame)
            return frame
        button = claims[0]
        x = button.center[0]
        region = (x - 55, 600, x + 55, 645)
        ctx.save(f"{period}_points_before", frame)
        ctx.tap(frame, button.center, f"{period}：领取底部积分奖励")
        frame = ctx.wait(lambda f: f.reward() or (f.all("每日任务", "每周任务", "每月任务")
                         and f.has(*MISSION_POINT_RECEIVED, region=region, exact=True)), f"{period}积分领取结果")
        if frame.reward():
            frame = ctx.dismiss_reward(frame)
        frame = ctx.wait(lambda f: f.all("每日任务", "每周任务", "每月任务")
                         and f.has(*MISSION_POINT_RECEIVED, region=region, exact=True), f"{period}积分奖励状态更新")
        claimed += 1
    raise Halt(f"{period}积分奖励领取超过已确认档位数量，停止操作。")


def missions(ctx: Context) -> None:
    frame = ctx.home()
    ctx.text(frame, "任务", region=(1140, 250, 1279, 360), exact=True)
    frame = ctx.wait(lambda f: f.all("每日任务", "每周任务", "每月任务"), "任务页面")
    for period in ("每日任务", "每周任务", "每月任务"):
        ctx.text(frame, period, region=(20, 100, 310, 420), exact=True)
        frame = ctx.wait(lambda f: f.find(*CLAIM_ALL, exact=True) is not None, period)
        collect_rewards(ctx, period, frame)
        frame = ctx.wait(lambda f: f.all("每日任务", "每周任务", "每月任务"), "返回任务列表")
        frame = collect_mission_points(ctx, period, frame)


def battle_pass(ctx: Context) -> None:
    frame = ctx.home()
    ctx.text(frame, "通行证", region=(1140, 70, 1279, 185), exact=True)
    frame = ctx.wait(lambda f: f.has("通行证", region=(150, 0, 550, 100)), "通行证页面")
    ctx.text(frame, "任务", region=(780, 600, 1010, 710), exact=True, reason="查看通行证任务")
    frame = ctx.wait(lambda f: f.all("每日任务", "每周任务", "当期任务"), "通行证任务列表")
    for period in ("每日任务", "每周任务", "当期任务"):
        ctx.text(frame, period, region=(20, 130, 315, 440), exact=True)
        frame = ctx.wait(lambda f: f.find(*CLAIM_ALL, exact=True) is not None, f"通行证{period}")
        collect_rewards(ctx, f"通行证{period}", frame)
        frame = ctx.wait(lambda f: f.all("每日任务", "每周任务", "当期任务"), "通行证任务列表")
    ctx.text(frame, "奖励", region=(780, 600, 1010, 710), exact=True, reason="查看通行证等级奖励")
    frame = ctx.wait(lambda f: f.has("通行证", region=(150, 0, 550, 100)) and not f.has("当期任务"), "通行证奖励列表")
    collect_rewards(ctx, "通行证领奖", frame)


def free_gifts(ctx: Context) -> None:
    from .free_gifts import free_gifts as run
    run(ctx)


def elite_shop(ctx: Context) -> None:
    from .shops import elite_shop as run
    run(ctx)


def logistics_shop(ctx: Context) -> None:
    from .shops import logistics_shop as run
    run(ctx)


def union_shop(ctx: Context) -> None:
    from .shops import union_shop as run
    run(ctx)


def master_shop(ctx: Context) -> None:
    from .shops import master_shop as run
    run(ctx)


def armor(ctx: Context) -> None:
    from .armor import armor as run
    run(ctx)


TASKS = [("base", "指挥室收菜", base), ("expedition", "远征", expedition), ("mail", "邮件", mail), ("friends", "好友", friends), ("free_gifts", "首页推荐免费礼包", free_gifts), ("shop_elite", "精英商店", elite_shop), ("shop_logistics", "后勤商店", logistics_shop), ("shop_union", "联合商店", union_shop), ("shop_master", "大师商店", master_shop), ("armor", "装甲进阶", armor), ("event", "活动扫荡", event), ("endurance", "续航计划", endurance), ("arena", "竞技场", arena), ("missions", "任务领奖", missions), ("pass", "通行证", battle_pass)]
