from __future__ import annotations

from dataclasses import dataclass
import re

from .runtime import Context, Halt, NeedsSetup, ROOT
from .vision import Frame, Word, normalized, template_match
from .feature_config import CATALOG, SHOPS, shop_period, load_state, save_state, shop_selection

CRYSTALS = {item["id"]: item for item in CATALOG["elite"] if item["id"].startswith("crystal")}
CRYSTAL_TITLE = re.compile(r"[i1!|l]*型自?选水晶")


def crystal_identity(frame: Frame, region: tuple[int, int, int, int], *, modal: bool = False) -> str | None:
    variants = {(item["id"], item.get("visual", item["id"]))
                for shop in SHOPS for item in CATALOG[shop] if item["id"] in CRYSTALS}
    matches = set()
    sold = not modal and frame.has("已售罄", "售罄", region=region, exact=True)
    for key, visual in sorted(variants):
        paths = [ROOT / f"assets/item_{visual}_{'dialog' if modal else 'list'}.png"]
        if sold:
            paths.append(ROOT / f"assets/item_{visual}_sold_list.png")
        if any(path.is_file() and template_match(frame, path, region, .85) for path in paths):
            matches.add(key)
    return next(iter(matches)) if len(matches) == 1 else None


@dataclass(frozen=True)
class ShopCard:
    name: str
    price: int
    stock: int | None
    currency: str
    word: Word


def price_currency(frame: Frame, price: Word) -> str:
    _, y1, _, y2 = price.box
    center = price.center[0]
    region = (max(280, center - 125), max(0, y1 - 10), min(1280, center + 15), min(720, y2 + 10))
    modal = frame.has("是否购买", region=(350, 140, 850, 225))
    if modal:
        # Short digits such as 8 have a smaller OCR box than the currency icon.
        # The dialog's fixed icon slot must not be cropped to that digit height.
        region = (590, 415, 645, 466)
    matches = []
    for name in CATALOG["currency_labels"]:
        paths = [ROOT / f"assets/currency_{name}{'_dialog' if modal else ''}.png"]
        if not modal:
            paths.append(ROOT / f"assets/currency_{name}_lower.png")
            if frame.has("已售罄", "售罄", region=(center - 150, max(0, y1 - 180), center + 150, y1 - 30), exact=True):
                paths.extend(ROOT / f"assets/currency_{name}_{suffix}.png" for suffix in ("sold", "sold_lower"))
        if any(path.is_file() and template_match(frame, path, region, .88 if modal else .74) for path in paths):
            matches.append(name)
    if len(matches) != 1:
        raise Halt("兑换货币图标不明确，停止兑换。")
    return matches[0]


def shop_cards(frame: Frame) -> list[ShopCard]:
    cached = getattr(frame, "_shop_cards", None)
    if cached is not None:
        return cached
    if not frame.exchange_list() or frame.has("是否购买", region=(350, 140, 850, 225)):
        raise NeedsSetup("不是已确认的兑换商店商品列表。")
    cards = []
    for title in frame.words:
        x, y = title.center
        row = 0 if 195 <= y <= 230 else 1 if 450 <= y <= 485 else None
        if row is None or not 310 <= x <= 1170 or title.box[0] < 295:
            continue
        crystal = None
        if CRYSTAL_TITLE.fullmatch(normalized(title.text)) and title.confidence >= .73:
            crystal = crystal_identity(frame, (max(280, x - 135), 240 + row * 253, min(1270, x + 135), 365 + row * 253))
        elif title.confidence < .85:
            continue
        prices = [word for word in frame.words if word.confidence >= .9
                  and x - 115 <= word.center[0] <= x + 135
                  and 390 + row * 253 <= word.center[1] <= 435 + row * 253
                  and re.fullmatch(r"\d+", normalized(word.text))]
        stock = [match[1] for word in frame.words if word.confidence >= .85
                 and x - 90 <= word.center[0] <= x + 145
                 and 230 + row * 253 <= word.center[1] <= 270 + row * 253
                 and (match := re.fullmatch(r"剩余(\d+|∞)", normalized(word.text)))]
        if len(stock) != 1 and frame.vision is not None:
            region = (max(280, x - 100), 230 + row * 253, min(1270, x + 155), 275 + row * 253)
            stock = [match[1] for word in frame.vision.region_words(frame, region) if word.confidence >= .85
                     and (match := re.fullmatch(r"剩余(\d+|∞)", normalized(word.text)))]
        if len(stock) != 1:
            region = (max(280, x - 100), 230 + row * 253, min(1270, x + 155), 275 + row * 253)
            if template_match(frame, ROOT / "assets/stock_unlimited.png", region, .88):
                stock = ["∞"]
        sold = frame.has("已售罄", "售罄", region=(x - 115, 270 + row * 253, x + 135, 365 + row * 253), exact=True)
        if not 1 <= len(prices) <= 2 or (len(stock) != 1 and not sold):
            continue
        # Discounts render the active price before a struck-through old price.
        prices.sort(key=lambda word: word.center[0])
        try:
            currency = price_currency(frame, prices[0])
        except Halt:
            currency = "unknown"
        price = int(normalized(prices[0].text))
        if CRYSTAL_TITLE.fullmatch(normalized(title.text)) and crystal is None:
            if not sold:
                continue
            # Sold-out overlays cover the artwork. This path can only skip a
            # sold item; only a unique price/currency may replace the artwork.
            # Roman numerals can lose a stroke under this overlay (III -> II,
            # II -> I), so same-price crystals still require a visual match.
            candidates = [item for shop in SHOPS for item in CATALOG[shop]
                          if item["id"] in CRYSTALS and item["price"] == price and item["currency"] == currency]
            identities = {item["id"] for item in candidates}
            if len(identities) != 1:
                continue
            crystal = identities.pop()
        cards.append(ShopCard(CRYSTALS[crystal]["name"] if crystal else title.text, price, 0 if sold else None if stock[0] == "∞" else int(stock[0]), currency, title))
    frame._shop_cards = cards
    return cards


def names(item: dict) -> set[str]:
    return {normalized(name) for name in [item["name"], *item.get("aliases", [])]}


def matching_card(frame: Frame, item: dict) -> ShopCard | None:
    candidates = [card for card in shop_cards(frame) if normalized(card.name) in names(item)
                  and card.price == item["price"] and card.currency == item["currency"]]
    if len(candidates) > 1:
        raise Halt("同一商品识别到多个位置，停止兑换。")
    return candidates[0] if candidates else None


def open_shop(ctx: Context, shop: str) -> Frame:
    if shop not in SHOPS:
        raise Halt("不允许的兑换商店；调度商店已移除。")
    label = SHOPS[shop]["label"]
    frame = ctx.home()
    ctx.text(frame, "商城", region=(700, 630, 850, 719), exact=True)
    frame = ctx.wait(lambda f: f.has("商城系统", region=(180, 0, 450, 90)), "商城系统")
    ctx.text(frame, "兑换商店", "兌換商店", region=(650, 70, 850, 190), exact=True)
    frame = ctx.wait(lambda f: f.exchange_list(), "兑换商店")
    sidebar = (0, 200, 235, 685)
    for direction in ("up", "down"):
        previous = None
        for _ in range(4):
            if frame.has(label, region=sidebar, exact=True, confidence=.9):
                ctx.text(frame, label, region=sidebar, exact=True)
                return ctx.wait(lambda f: f.exchange_list(), label + "商品列表")
            signature = tuple((w.text, w.center[1] // 10) for w in frame.words if w.confidence >= .9 and 0 <= w.center[0] <= 235 and 180 <= w.center[1] <= 719)
            if signature == previous:
                break
            previous = signature
            start, end = ((125, 275), (125, 620)) if direction == "up" else ((125, 620), (125, 270))
            ctx.swipe(frame, start, end, "查找商店入口：" + label, duration=1000)
            frame = ctx.wait(lambda f: f.exchange_list(), "兑换商店侧栏")
    raise NeedsSetup("没有找到商店入口「" + label + "」，停止兑换。")


def find_card(ctx: Context, frame: Frame, item: dict, *, forward_only: bool = False, allow_sold: bool = False) -> tuple[Frame, ShopCard | None]:
    # Each search begins at the left edge. Stop scrolling when the visual list
    # stabilizes, rather than depending on last session's scroll position.
    for direction in (("right",) if forward_only else ("left", "right")):
        previous = None
        for _ in range(7):
            card = matching_card(frame, item)
            if card is None and allow_sold:
                sold = [candidate for candidate in shop_cards(frame)
                        if normalized(candidate.name) in names(item) and candidate.price == item["price"]
                        and candidate.stock == 0 and candidate.currency in (item["currency"], "unknown")]
                card = sold[0] if len(sold) == 1 else None
            if card is None and "自选水晶" in item["name"] and frame.has("水晶", region=(290, 190, 1250, 490)):
                # Text is evaluated again after the scroll settles. A low-
                # confidence numeral must not cause us to skip its whole page.
                ctx.pause(.4)
                frame = ctx.frame()
                card = matching_card(frame, item)
            if card is not None:
                return frame, card
            ctx.save(f"search_{item['id']}", frame)
            signature = tuple((c.name, c.price, c.stock, c.word.center[0] // 10) for c in shop_cards(frame))
            if previous == signature and signature:
                break
            previous = signature
            start, end = ((530, 430), (1050, 430)) if direction == "left" else ((1050, 430), (530, 430))
            ctx.swipe(frame, start, end, f"查找兑换商品：{item['name']}", duration=1200)
            frame = ctx.wait(lambda f: f.exchange_list()
                             and not f.has("是否购买", region=(350, 140, 850, 225)), "商店商品列表")
    return frame, None


def field_integer(frame: Frame, region: tuple[int, int, int, int], *, stock: bool = False) -> int | None:
    words = frame.words
    for pass_number in range(2):
        values = []
        for word in words:
            x, y = word.center
            if word.confidence < .9 or not (region[0] <= x <= region[2] and region[1] <= y <= region[3]):
                continue
            text = normalized(word.text)
            match = re.fullmatch(r"(?:剩余数量[:：]?)?(\d+)", text) if stock else re.fullmatch(r"(\d+)", text)
            if match:
                values.append(int(match[1]))
        if len(values) == 1:
            return values[0]
        if pass_number == 0 and frame.vision is not None:
            words = frame.vision.region_words(frame, region)
        else:
            break
    return None


@dataclass(frozen=True)
class ExchangePreview:
    quantity: int
    total: int
    stock: int | None


def exchange_preview(frame: Frame, item: dict, *, expected: int | None = None, allow_zero: bool = False) -> ExchangePreview:
    title = frame.find("是否购买", region=(350, 140, 850, 225), confidence=.9)
    if title is None or frame.is_loading() or frame.paid_dialog():
        raise Halt("不是已确认的商品兑换窗口。")
    observed = normalized(title.text).removeprefix("是否购买").rstrip("?？")
    if item["id"] in CRYSTALS:
        if not CRYSTAL_TITLE.fullmatch(observed) or crystal_identity(frame, (587, 260, 707, 345), modal=True) != item["id"]:
            raise Halt("水晶兑换窗口的商品图或类别与勾选项不同。")
    elif observed not in names(item):
        raise Halt("兑换窗口的商品名称与勾选项不同。")
    quantity = field_integer(frame, (590, 370, 690, 417))
    totals = [word for word in frame.words if word.confidence >= .9
              and 635 <= word.center[0] <= 840 and 423 <= word.center[1] <= 465
              and re.fullmatch(r"\d+", normalized(word.text))]
    if len(totals) != 1 and frame.vision is not None:
        totals = [word for word in frame.vision.region_words(frame, (645, 420, 855, 466))
                  if word.confidence >= .9 and re.fullmatch(r"\d+", normalized(word.text))]
    stock = field_integer(frame, (795, 165, 1045, 215), stock=True)
    unlimited = item.get("unlimited", False)
    if stock is None and not (unlimited and frame.has("∞", "无限制", region=(795, 165, 1045, 215), confidence=.9)):
        raise Halt("没有读清兑换窗口的剩余数量。")
    if quantity is None or len(totals) != 1:
        raise Halt("没有读清兑换数量或总价。")
    total = int(normalized(totals[0].text))
    currency = price_currency(frame, totals[0])
    if quantity < (0 if allow_zero else 1) or total != item["price"] * quantity or currency != item["currency"]:
        raise Halt("兑换数量、总价或货币与已核对商品不一致。")
    if expected is not None and quantity != expected:
        raise Halt("实际兑换数量与计划不同，拒绝确认。")
    if stock is not None and quantity > stock:
        raise Halt("兑换数量超过当前库存。")
    if frame.find("取消", region=(300, 460, 620, 550), exact=True) is None or frame.find("确认", region=(680, 460, 950, 550), exact=True) is None:
        raise Halt("兑换窗口缺少明确的取消或确认按钮。")
    return ExchangePreview(quantity, total, stock)


def cancel_exchange(ctx: Context, frame: Frame) -> Frame:
    ctx.text(frame, "取消", region=(300, 460, 620, 550), exact=True, reason="取消商品兑换预览")
    return ctx.wait(lambda f: f.exchange_list()
                    and not f.has("是否购买", region=(350, 140, 850, 225)), "回到商品列表")


def prepare_exchange(ctx: Context, frame: Frame, item: dict, target: int) -> tuple[Frame, int]:
    initial = exchange_preview(frame, item, allow_zero=True)
    if initial.quantity == 0:
        return frame, 0
    ctx.text(frame, "MAX", region=(765, 370, 880, 420), exact=True, reason="查看现有资产可兑换的最大数量")
    frame = ctx.wait(lambda f: f.has("是否购买", region=(350, 140, 850, 225)), "商品数量设置")
    # MAX is constrained by both the game's remaining stock and owned currency.
    current = exchange_preview(frame, item, allow_zero=True)
    if current.quantity == 0:
        return frame, 0
    if current.stock != initial.stock:
        raise Halt("查看兑换上限时商品剩余数量发生变化，停止兑换。")
    requested = target
    target = min(target, current.quantity, initial.stock if initial.stock is not None else target)
    if target < requested:
        ctx.log(f"{item['name']}目标剩余{requested}份，当前最多兑换{current.quantity}份，本次按上限兑换{target}份。")
    # Keep enough operations to verify, submit, return home and finish the queue.
    available = int(ctx.config["limits"]["max_actions"]) - ctx.actions - 18
    steps_down, steps_up = current.quantity - target, target - 1
    if min(steps_down, steps_up + 1) > available:
        if available < 1:
            return frame, 0
        target = min(target, available)
        steps_down, steps_up = current.quantity - target, target - 1
        ctx.log(f"本轮操作预算有限，本次先兑换 {target} 份，未完成的每日目标保留。")
    if steps_down <= steps_up + 1:
        point, steps = (548, 394), steps_down
    else:
        ctx.text(frame, "MIN", region=(400, 370, 515, 420), exact=True, reason="从最小兑换数量开始设置")
        frame = ctx.frame()
        exchange_preview(frame, item, expected=1)
        point, steps = (730, 394), steps_up
    for _ in range(steps):
        exchange_preview(frame, item)
        ctx.tap(frame, point, "调整已选商品兑换数量", pause=.2)
        frame = ctx.frame()
    exchange_preview(frame, item, expected=target)
    return frame, target


def shop_ledger(path, day: str) -> dict:
    state = load_state(path, {"version": 1, "day": day, "done": {}, "pending": None})
    if "pending" not in state or state["pending"] is not None:
        raise NeedsSetup(f"上次兑换结果待核对，禁止重复提交。请核对游戏及记录：{path}")
    if state.get("version") != 1 or not isinstance(state.get("done"), dict) or not isinstance(state.get("day"), str):
        raise NeedsSetup("商店兑换记录格式无效。")
    if any(not isinstance(key, str) or type(value) is not int or value < 0 for key, value in state["done"].items()):
        raise NeedsSetup("商店兑换累计数量记录无效。")
    if state["day"] != day:
        state = {"version": 1, "day": day, "done": {}, "pending": None}
    return state


def exchange(ctx: Context, shop: str, *, preview_only: bool = False) -> None:
    selected = shop_selection(ctx.maa, shop)
    label = SHOPS[shop]["label"]
    period_label = "本周" if SHOPS[shop]["period"] == "week" else "今日"
    if not selected:
        ctx.result(label, "already_done", "没有勾选商品，跳过兑换。")
        return
    path = ROOT / f"runtime/state/shop_{shop}.json"
    state = shop_ledger(path, shop_period(shop))
    plans = [(item, max(0, target - state["done"].get(item["id"], 0))) for item, target in selected]
    if not any(quantity for _, quantity in plans):
        ctx.result(label, "already_done", f"勾选商品{period_label}目标已完成，不重复兑换。")
        return
    frame = open_shop(ctx, shop)
    previous = None
    for _ in range(6):
        signature = tuple((c.name, c.price, c.word.center[0] // 10) for c in shop_cards(frame))
        if previous == signature and signature:
            break
        previous = signature
        ctx.swipe(frame, (530, 430), (1050, 430), "复位到商店商品列表起点", duration=1200)
        frame = ctx.wait(lambda f: f.exchange_list(), "商品列表起点")
    completed, skipped = [], []
    for item, quantity in plans:
        if not quantity:
            continue
        if int(ctx.config["limits"]["max_actions"]) - ctx.actions < 30:
            skipped.append("本轮操作预算不足，剩余目标下次继续")
            break
        frame, card = find_card(ctx, frame, item)
        if card is None:
            raise NeedsSetup(f"没有找到已核对的商品「{item['name']}」及对应币种／价格；可能已换商品，停止兑换。")
        if card.stock == 0:
            skipped.append(item["name"] + "已售罄")
            continue
        if card.stock is None and not item.get("unlimited"):
            raise Halt("商品库存状态发生变化，拒绝兑换。")
        ctx.tap(frame, card.word.center, f"查看已勾选商品：{item['name']}")
        frame = ctx.wait(lambda f: f.has("是否购买", region=(350, 140, 850, 225)), "商品兑换确认窗口")
        if exchange_preview(frame, item, allow_zero=True).stock != card.stock:
            raise Halt("兑换弹窗库存与刚才选中的商品不同，拒绝确认。")
        frame, actual = prepare_exchange(ctx, frame, item, quantity)
        if not actual:
            frame = cancel_exchange(ctx, frame)
            skipped.append(item["name"] + "现有资产或操作预算不足")
            continue
        preview = exchange_preview(frame, item, expected=actual)
        evidence = ctx.save(f"exchange_{shop}_{item['id']}_preview", frame)
        if preview_only:
            frame = cancel_exchange(ctx, frame)
            completed.append(f"{item['name']} {actual} 份预览已核对并取消")
            continue
        state["pending"] = {"item": item["id"], "quantity": actual, "total": preview.total,
                            "currency": item["currency"], "evidence": evidence}
        save_state(path, state)
        ctx.text(frame, "确认", region=(680, 460, 950, 550), exact=True,
                 reason=f"兑换已选商品 {item['name']} ×{actual}，消耗 {preview.total} {CATALOG['currency_labels'][item['currency']]}")
        frame = ctx.wait(lambda f: f.reward(), "兑换成功的资源奖励", timeout=25)
        ctx.save("exchange_reward", frame)
        frame = ctx.dismiss_reward(frame)
        frame = ctx.wait(lambda f: f.exchange_list()
                         and not f.has("是否购买", region=(350, 140, 850, 225)), "兑换后商品列表")
        if preview.stock is not None:
            frame, after = find_card(ctx, frame, item, allow_sold=preview.stock == actual)
            if after is None or after.stock != preview.stock - actual:
                raise Halt("兑换奖励已出现，但商品剩余数量未核对成功；记录保留待核对，不会自动再兑。")
        state["done"][item["id"]] = state["done"].get(item["id"], 0) + actual
        state["pending"] = None
        save_state(path, state)
        completed.append(f"{item['name']} ×{actual}")
    detail = "；".join(completed + skipped) or "当前无需兑换。"
    ctx.result(label, "completed" if completed else "already_done", detail, frame)


def elite_shop(ctx: Context) -> None:
    exchange(ctx, "elite")


def logistics_shop(ctx: Context) -> None:
    exchange(ctx, "logistics")


def union_shop(ctx: Context) -> None:
    exchange(ctx, "union")


def master_shop(ctx: Context) -> None:
    exchange(ctx, "master")
