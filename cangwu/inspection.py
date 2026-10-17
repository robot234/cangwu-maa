"""Bounded navigation for inspecting new game features through the Maa context."""
from .runtime import Context, Halt
from .feature_config import SHOPS


NAVIGATION = {"商城", "商城系统", "首页推荐", "通常礼包", "每日礼包", "每周礼包", "每月礼包", "兑换商店", "兌換商店", "精英商店", "后勤商店", "联合商店", "大师商店", "作战", "资源", "装甲进阶", "超限共鸣"}


def inspect_ui(ctx: Context, params: dict) -> None:
    text = params.get("text", "")
    if text and text not in NAVIGATION:
        raise Halt("页面检查仅允许已列出的导航入口，禁止提交购买或作战。")
    if params.get("back"):
        ctx.back()
    frame = ctx.home() if params.get("home", False) else ctx.wait(lambda f: True, "页面加载完成")
    route = params.get("route")
    if route:
        if route in SHOPS:
            from .shops import open_shop
            frame = open_shop(ctx, route)
        elif route in ("combat", "recommend"):
            frame = ctx.home()
            if route == "combat":
                ctx.text(frame, "作战", region=(1020, 620, 1279, 719), exact=True)
                frame = ctx.wait(lambda f: f.has("战斗副本", region=(180, 0, 520, 90)), "作战页面")
            else:
                ctx.text(frame, "商城", region=(700, 630, 850, 719), exact=True)
                frame = ctx.wait(lambda f: f.has("商城系统"), "商城系统")
                ctx.text(frame, "首页推荐", region=(100, 70, 285, 190), exact=True)
                frame = ctx.wait(lambda f: f.all("商城系统", "首页推荐"), "首页推荐")
        else:
            raise Halt("未知页面检查路线。")
    menu_down = params.get("menu_down", 0)
    if type(menu_down) is not int or not 0 <= menu_down <= 3:
        raise Halt("商店菜单最多向下检查3次。")
    for _ in range(menu_down):
        if not frame.has("商城系统") or not frame.has("后勤商店", "联合商店", "大师商店", "深渊商店", "武道会商店", region=(0, 180, 235, 719), exact=True):
            raise Halt("只有确认的兑换商店才能检查侧栏。")
        ctx.swipe(frame, (125, 620), (125, 270), "查看下方商店入口", duration=1000)
        frame = ctx.wait(lambda f: f.has("商城系统"), "商店菜单")
    if text:
        required = params.get("require", [])
        if not required or not frame.all(*required):
            raise Halt("当前页面与检查所需页面不一致，停止导航。")
        region = params.get("region")
        if region is not None:
            if len(region) != 4 or not (0 <= region[0] < region[2] <= 1280 and 0 <= region[1] < region[3] <= 720):
                raise Halt("页面检查范围无效。")
        ctx.text(frame, text, region=region, exact=True, reason=f"查看页面：{text}")
        ctx.pause(1)
        frame = ctx.wait(lambda f: True, "页面切换完成")
    pages = params.get("page_right", 0) or params.get("page_left", 0)
    if not isinstance(pages, int) or not 0 <= pages <= 6:
        raise Halt("页面检查最多滑动 6 次。")
    ctx.save("inspection_start", frame)
    for index in range(pages):
        if not frame.has("商城系统") or not frame.has("精英商店", "后勤商店", "联合商店", "大师商店", region=(0, 180, 235, 719), exact=True):
            raise Halt("只有确认的兑换商店列表才能检查下一页。")
        start, end = ((1050, 430), (530, 430)) if params.get("page_right") else ((530, 430), (1050, 430))
        ctx.swipe(frame, start, end, "查看其他商店商品", duration=1200)
        frame = ctx.wait(lambda f: f.has("商城系统") and f.has("兑换商店", "兌換商店", region=(650, 70, 850, 190)), "商店商品列表")
        ctx.save(f"inspection_page_{index + 1}", frame)
    item = params.get("item", "")
    if item:
        if ":" in item:
            from .feature_config import CATALOG
            from .shops import find_card
            shop, item_id = item.split(":", 1)
            if shop not in SHOPS:
                raise Halt("不是允许检查的兑换商店。")
            selected = next((entry for entry in CATALOG[shop] if entry["id"] == item_id), None)
            if selected is None or not frame.exchange_list():
                raise Halt("不是已知商品或兑换商店列表。")
            frame, card = find_card(ctx, frame, selected)
            if card is None or card.stock == 0:
                raise Halt("检查商品未识别或已售罄，未打开兑换。")
            ctx.tap(frame, card.word.center, "查看已核对的目录商品：" + selected["name"])
            frame = ctx.wait(lambda f: f.has("是否购买", region=(350, 140, 850, 225)), "商品详情")
            ctx.result("页面检查", "completed", "已核对商品图／名称、价格和币种，仅打开详情，没有提交兑换。", frame)
            return
        free_gift = item == "免费礼包" and frame.all("商城系统", "首页推荐", "通常礼包")
        if free_gift:
            from .shops import field_integer
            if field_integer(frame, (380, 395, 450, 440)) != 0:
                raise Halt("免费礼包列表价格不是明确的0，拒绝查看。")
        if (not free_gift and not (frame.has("商城系统") and frame.has("精英商店", "后勤商店", "联合商店", "大师商店", region=(0, 180, 235, 719), exact=True))) or any(word in item for word in ("购买", "兑换", "支付", "确认", "充值", "开始")):
            raise Halt("商品检查只允许查看已识别商品详情。")
        ctx.text(frame, item, region=(280, 180, 1250, 700), exact=True, reason=f"查看商品详情：{item}")
        frame = ctx.wait(lambda f: True, "商品详情")
    if params.get("armor_preview"):
        if not frame.has("本周可领取奖励次数") or not frame.has("超限共鸣"):
            raise Halt("只有确认的装甲进阶页面才能查看扫荡次数。")
        ctx.text(frame, "扫荡", region=(790, 610, 980, 710), exact=True, reason="查看装甲进阶扫荡设置，不提交")
        frame = ctx.wait(lambda f: True, "扫荡设置窗口")
    if params.get("shop_rules"):
        if not frame.exchange_list():
            raise Halt("只有兑换商店列表才能查看商店说明。")
        ctx.tap(frame, (423, 43), "查看商店刷新规则")
        frame = ctx.wait(lambda f: True, "商店说明")
    if params.get("armor_rules"):
        if not frame.has("本周可领取奖励次数") or not frame.has("超限共鸣"):
            raise Halt("只有确认的装甲进阶页面才能查看规则。")
        ctx.tap(frame, (435, 43), "查看装甲进阶说明")
        frame = ctx.wait(lambda f: not f.has("扫荡", region=(790, 610, 980, 710), exact=True), "规则说明")
    ctx.result("页面检查", "completed", "只进行已确认的页面导航和截图，没有提交付费购买或作战。", frame)
