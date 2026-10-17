from __future__ import annotations

import os
from pathlib import Path
import re
import cv2

PACKAGE = "com.feimo.hazeworld.mumu"
ACTIVITY = "prj.iyinghun.platform.sdk.SplashScreenActivity"


class DeviceError(RuntimeError):
    pass


def discover_adb() -> Path:
    for name in ("nx_device/12.0/shell/adb.exe", "nx_main/adb.exe"):
        path = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Netease/MuMuPlayer-12.0" / name
        if path.is_file():
            return path
    raise DeviceError("未找到 MuMu 的 adb.exe，请在 config.local.json 中填写 device.adb_path。")


def validate_port(value: int) -> int:
    port = int(value)
    if port == 5037 or not 1024 <= port <= 65535:
        raise DeviceError("助手连接端口必须为 1024～65535，且不能使用 5037。")
    return port


class Device:
    """All game screenshots and input go through the bound Maa controller."""

    def __init__(self, config: dict, log=print, controller=None, check=None):
        self.config, self.log, self.controller = config, log, controller
        self.check = check or (lambda: None)
        self.package = config.get("package", PACKAGE)
        self.port = validate_port(config.get("adb_server_port", 15037))
        self.serial = config.get("serial", "127.0.0.1:16384")
        if not re.fullmatch(r"127\.0\.0\.1:\d{4,5}", self.serial):
            raise DeviceError("模拟器地址须为 127.0.0.1:端口。")

    @staticmethod
    def completed(job, description: str):
        import time
        deadline = time.monotonic() + 25
        while not job.done:
            if time.monotonic() >= deadline:
                raise DeviceError(f"Maa {description} 超时，已停止后续输入。")
            time.sleep(.05)
        if not job.succeeded:
            raise DeviceError(f"Maa {description} 失败。")
        return job

    def connect(self) -> None:
        if self.controller is None or not self.controller.connected:
            raise DeviceError("Maa 控制器未连接，请先在界面连接 MuMu。")
        packages = self.shell("pm", "list", "packages", self.package)
        if f"package:{self.package}" not in packages.splitlines():
            raise DeviceError("当前模拟器没有安装苍雾世界国服。")
        self.log("已接入 MaaFramework 控制器；截图、识别和点击由 Maa 执行。")

    def shell(self, *args: str, timeout: float = 8) -> str:
        import shlex
        if self.controller is None:
            raise DeviceError("Maa 控制器未初始化。")
        job = self.completed(self.controller.post_shell(shlex.join(args), timeout=int(timeout * 1000)), "设备检查")
        return str(job.get()).replace("\r", "")

    def foreground(self) -> str:
        output = self.shell("dumpsys", "activity", "activities")
        for prefix in ("topResumedActivity=", "ResumedActivity:", "mResumedActivity:"):
            for line in output.splitlines():
                if prefix in line:
                    match = re.search(r"\bu\d+\s+([\w.]+)/", line)
                    if match:
                        return match.group(1)
        raise DeviceError("无法确认前台应用，已停止点击。")

    def require_game(self) -> None:
        if self.foreground() != self.package:
            raise DeviceError("苍雾世界已离开前台，已停止点击；请切回游戏再运行。")

    def screenshot(self) -> bytes:
        self.require_game()
        self.completed(self.controller.post_screencap(), "截图")
        ok, png = cv2.imencode(".png", self.controller.cached_image)
        if not ok:
            raise DeviceError("Maa 截图编码失败。")
        return png.tobytes()

    def tap(self, x: int, y: int) -> None:
        self.require_game()
        self.check()
        self.completed(self.controller.post_click(x, y), "点击")

    def swipe(self, start: tuple[int, int], end: tuple[int, int], duration: int = 500) -> None:
        self.require_game()
        self.check()
        self.completed(self.controller.post_swipe(*start, *end, duration), "滑动")

    def back(self) -> None:
        self.require_game()
        self.check()
        self.completed(self.controller.post_click_key(4), "返回")

    def launch(self) -> None:
        self.completed(self.controller.post_start_app(f"{self.package}/{ACTIVITY}"), "启动游戏")
