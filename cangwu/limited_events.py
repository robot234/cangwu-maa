"""Navigation shared by the current limited-time activity tabs."""
from .runtime import Context
from .vision import Frame


TABS = (0, 150, 215, 650)


def open_activity(ctx: Context, title: str, ready) -> Frame | None:
    frame = ctx.home()
    ctx.text(frame, "活动", region=(1150, 170, 1279, 260), exact=True, reason="打开活动合集")
    frame = ctx.wait(lambda f: f.activity_hub(), "活动合集页面")
    tab = frame.find(title, region=TABS, exact=True, confidence=.9)
    for start, end in (((105, 300), (105, 600)), ((105, 600), (105, 250))):
        if tab is not None:
            break
        ctx.swipe(frame, start, end, "查找活动：" + title, duration=600)
        frame = ctx.wait(lambda f: f.activity_hub(), "活动列表")
        tab = frame.find(title, region=TABS, exact=True, confidence=.9)
    if tab is None:
        ctx.result(title, "already_done", "活动入口未出现，跳过。", frame)
        return None
    ctx.tap(frame, tab.center, "切换活动：" + title)
    return ctx.wait(ready, title + "页面")
