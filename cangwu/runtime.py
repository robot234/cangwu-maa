from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Callable

from .device import Device
from .vision import Frame, Vision, Word, template_match

ROOT = Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))


class Halt(RuntimeError):
    pass


class NeedsSetup(RuntimeError):
    pass


def load_config() -> dict:
    path = ROOT / "config.local.json"
    if not path.is_file():
        path = ROOT / "config.example.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_config(config: dict) -> None:
    path = ROOT / "config.local.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class RunLock:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.environ.get("LOCALAPPDATA", str(ROOT / "runtime"))) / "CangwuMaa/device.lock"

    def __enter__(self):
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+b")
        self.handle.seek(0, 2)
        if not self.handle.tell():
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        if __import__("os").name == "nt":
            import msvcrt
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                self.handle.close()
                raise Halt("已有一个日常任务在运行，请先停止它。") from exc
        return self

    def __exit__(self, *_):
        if __import__("os").name == "nt":
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()


@dataclass
class TaskResult:
    task: str
    status: str
    detail: str
    evidence: str = ""


class Context:
    def __init__(self, config: dict, stop: threading.Event, emit: Callable[[str], None] = print, *, maa=None):
        self.config, self.stop, self.emit = config, stop, emit
        stamp = datetime.now(CST).strftime("%Y%m%d_%H%M%S_%f")
        self.directory = ROOT / "runtime/runs" / stamp
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log_path = self.directory / "run.log"
        self.started = time.monotonic()
        self.deadline = self.started + int(config["limits"]["run_minutes"]) * 60
        self.actions = 0
        self.maa = maa
        self.device = Device(config["device"], self.log, maa.tasker.controller if maa else None, self.check)
        self.vision: Vision | None = None
        self.last: Frame | None = None
        self.stop_file = ROOT / "runtime/STOP"
        self.results: list[TaskResult] = []

    def log(self, message: str) -> None:
        line = f"{datetime.now(CST):%H:%M:%S} {message}"
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self.emit(line)

    def check(self) -> None:
        if self.stop.is_set() or self.stop_file.exists() or (self.maa and self.maa.tasker.stopping):
            raise Halt("已收到停止指令。")
        if time.monotonic() >= self.deadline:
            raise Halt("已达到本次运行时限，停止操作。")
        if self.actions >= int(self.config["limits"]["max_actions"]):
            raise Halt("已达到本次操作次数上限，停止操作。")

    def pause(self, seconds: float) -> None:
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.check()
            self.stop.wait(min(.15, max(0, until - time.monotonic())))

    def frame(self) -> Frame:
        self.check()
        if self.vision is None:
            raise RuntimeError("识图引擎未初始化。")
        self.last = self.vision.read(self.device.screenshot())
        if self.last.paid_dialog():
            self.save("unexpected_purchase", self.last)
            raise Halt("出现购买、充值或付费确认界面，已停止操作。")
        return self.last

    def wait(self, predicate: Callable[[Frame], bool], description: str, timeout: float | None = None) -> Frame:
        until = min(self.deadline, time.monotonic() + (timeout or int(self.config["limits"]["page_timeout"])))
        while time.monotonic() < until:
            frame = self.frame()
            if not frame.is_loading() and predicate(frame):
                return frame
            self.pause(.6)
        self.save("timeout")
        raise Halt(f"等待「{description}」超时，已保留截图。")

    def tap(self, frame: Frame, point: tuple[int, int], reason: str, *, pause: float = 1.2) -> None:
        self.check()
        if frame.is_loading() or frame.paid_dialog():
            raise Halt("画面仍在加载或出现付费界面，拒绝点击。")
        x, y = point
        if not (0 <= x < 1280 and 0 <= y < 720):
            raise Halt("按钮坐标超出画面，拒绝点击。")
        width, height = frame.original_size
        self.log(reason)
        self.check()
        self.device.tap(round(x * width / 1280), round(y * height / 720))
        self.actions += 1
        self.pause(pause)

    def swipe(self, frame: Frame, start: tuple[int, int], end: tuple[int, int], reason: str, *, duration: int = 500) -> None:
        self.check()
        if frame.is_loading() or frame.paid_dialog():
            raise Halt("画面仍在加载或出现付费界面，拒绝滑动。")
        if not all(0 <= x < 1280 and 0 <= y < 720 for x, y in (start, end)) or not 100 <= duration <= 1500:
            raise Halt("滑动范围或时长无效。")
        width, height = frame.original_size
        scaled = [(round(x * width / 1280), round(y * height / 720)) for x, y in (start, end)]
        self.log(reason)
        self.check()
        self.device.swipe(*scaled, duration)
        self.actions += 1
        self.pause(1.2)

    def text(self, frame: Frame, *texts: str, region: tuple[int, int, int, int] | None = None,
             reason: str | None = None, exact: bool = False) -> None:
        word = frame.find(*texts, region=region, exact=exact)
        if word is None:
            raise Halt(f"没有识别到按钮「{' / '.join(texts)}」，停止操作。")
        self.tap(frame, word.center, reason or f"点击 {word.text}")

    def back(self) -> None:
        self.check()
        self.device.back()
        self.actions += 1
        self.pause(1)

    def save(self, label: str, frame: Frame | None = None) -> str:
        frame = frame or self.last
        if frame is None:
            return ""
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)
        path = self.directory / f"{self.actions:03d}_{safe}.png"
        path.write_bytes(frame.png)
        path.with_suffix(".ocr.json").write_text(json.dumps([asdict(w) for w in frame.words], ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def dismiss_reward(self, frame: Frame) -> Frame:
        if frame.reward():
            self.save("reward", frame)
            self.tap(frame, (640, 550), "关闭已领取的资源弹窗")
            return self.wait(lambda f: not f.reward(), "资源弹窗关闭")
        return frame

    def home(self) -> Frame:
        from .startup import enter_game, signin_page, start_button

        for _ in range(8):
            frame = self.wait(lambda f: True, "页面加载完成")
            if frame.is_home():
                return frame
            if frame.reward():
                self.dismiss_reward(frame)
                continue
            if frame.has("扫荡结算", region=(420, 35, 850, 130)):
                frame = self.wait(lambda f: f.settlement_confirm() is not None, "结算确认按钮出现")
                button = frame.settlement_confirm()
                self.text(frame, button.text, region=(420, 560, 850, 670), exact=True, reason="关闭上次未收起的结算")
                self.wait(lambda f: not f.has("扫荡结算", region=(420, 35, 850, 130)), "结算窗口关闭")
                continue
            if start_button(frame) is not None or signin_page(frame):
                return enter_game(self, frame)
            if frame.all("活动公告", "游戏公告"):
                self.tap(frame, (640, 692), "关闭登录公告")
                continue
            if frame.has("开始扫荡", "开始代理", region=(430, 490, 830, 590)):
                self.tap(frame, (280, 620), "关闭次数设置窗口")
                self.wait(lambda f: not f.has("开始扫荡", "开始代理", region=(430, 490, 830, 590)), "次数窗口关闭")
                continue
            if frame.has("远征任务"):
                self.tap(frame, (640, 690), "关闭远征详情")
                self.wait(lambda f: not f.has("远征任务"), "远征详情关闭")
                continue
            if frame.all("每日任务", "每周任务", "每月任务"):
                self.tap(frame, (25, 38), "离开任务页面")
                self.wait(lambda f: f.is_home(), "游戏大厅")
                continue
            if frame.all("每日任务", "每周任务", "当期任务") and frame.has("通行证", region=(180, 0, 550, 90)):
                self.text(frame, "奖励", region=(780, 600, 1010, 710), exact=True, reason="返回通行证奖励列表")
                self.wait(lambda f: f.has("通行证", region=(180, 0, 550, 90)) and not f.has("当期任务"), "通行证奖励列表")
                continue
            if frame.has("商城系统", region=(180, 0, 450, 90)) and frame.has("是否购买", region=(350, 140, 850, 225)):
                self.text(frame, "取消", region=(300, 460, 620, 550), exact=True, reason="关闭未提交的商品兑换窗口")
                self.wait(lambda f: not f.has("是否购买", region=(350, 140, 850, 225)), "商品兑换窗口关闭")
                continue
            home = template_match(frame, ROOT / "assets/home_icon.png", (125, 5, 220, 70), .9)
            if home:
                self.tap(frame, home, "返回游戏大厅")
                self.wait(lambda f: f.is_home(), "游戏大厅")
                continue
            # A top-left back control is used only on identified in-game pages.
            title = frame.find("指挥室", "作战演习", "邮件", "好友", "任务", "通行证", "限时活动", "商城系统", region=(180, 5, 450, 90))
            if title:
                self.tap(frame, (25, 38), "点击游戏内返回按钮")
                self.wait(lambda f: f.is_home() or not f.has(title.text, region=(180, 5, 450, 90)), "返回上一页")
                continue
            self.save("unknown_page", frame)
            raise Halt("当前页面未识别。请手动回到苍雾世界大厅，再点击开始。")
        raise Halt("连续返回后仍未到达大厅，已停止操作。")

    def result(self, name: str, status: str, detail: str, frame: Frame | None = None) -> None:
        evidence = self.save(name, frame)
        self.results.append(TaskResult(name, status, detail, evidence))
        self.log(f"{name}：{detail}")

    def finish(self, status: str) -> dict:
        report = {
            "engine": "MaaFramework 5.14.3",
            "status": status, "finished_at": datetime.now(CST).isoformat(),
            "actions": self.actions, "results": [asdict(r) for r in self.results],
            "elapsed_seconds": round(time.monotonic() - self.started, 1),
            "directory": str(self.directory),
        }
        output = self.directory / "report.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "runtime/latest_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"本次状态：{status}；报告：{output}")
        return report
