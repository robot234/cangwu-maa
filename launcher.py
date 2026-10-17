from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import ctypes
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from cangwu.device import DeviceError, discover_adb, validate_port


def settings() -> dict:
    path = ROOT / "config.local.json"
    if not path.is_file():
        path = ROOT / "config.example.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def environment() -> dict[str, str]:
    port = validate_port(settings()["device"].get("adb_server_port", 15037))
    env = dict(os.environ)
    # ADB only treats an empty host or "localhost" as a local server.
    # A numeric host prevents automatic daemon startup on a cold launch.
    env.update(ANDROID_ADB_SERVER_PORT=str(port), ADB_SERVER_SOCKET=f"tcp:{port}", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    env.pop("MAAFW_BINARY_PATH", None)
    return env


def prepare_adb(env: dict[str, str]) -> str:
    config = settings()["device"]
    port = validate_port(config.get("adb_server_port", 15037))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as sock:
            sock.settimeout(2)
            sock.sendall(b"000chost:version")
            if sock.recv(4) != b"OKAY":
                raise DeviceError(f"端口 {port} 被其他程序占用，请更换助手连接端口。")
    except ConnectionRefusedError:
        pass
    except (TimeoutError, socket.timeout) as exc:
        raise DeviceError(f"端口 {port} 没有返回 ADB 协议，已停止连接。") from exc
    adb = config.get("adb_path") or str(discover_adb())
    if not Path(adb).is_file():
        raise DeviceError("配置的 adb.exe 不存在。")
    for args in (["start-server"], ["connect", config["serial"]]):
        result = subprocess.run([adb, "-P", str(port), *args], capture_output=True, timeout=15, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise DeviceError((result.stderr or result.stdout).decode("utf-8", "replace")[:500])
    return adb


def ui_running() -> bool:
    from cangwu.mxu_api import discover
    try:
        discover(ROOT)
        return True
    except (OSError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="苍雾世界 Maa 助手")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--host", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--restart-idle", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    (ROOT / "runtime").mkdir(exist_ok=True)
    if args.stop:
        (ROOT / "runtime/STOP").write_text("stop", encoding="ascii")
        print("已发送停止指令。游戏内已开始的代理仍由游戏继续结算。")
        return 0
    if not args.check and not args.restart_idle and ui_running():
        return 0
    if not args.check and not ctypes.windll.shell32.IsUserAnAdmin():
        # MXU elevates itself and loses the caller's environment. Pass
        # the dedicated ADB settings from an elevated host; the UI connects.
        arguments = [str(ROOT / "launcher.py"), "--host"]
        if args.restart_idle:
            arguments.append("--restart-idle")
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(ROOT / ".venv/Scripts/pythonw.exe"), subprocess.list2cmdline(arguments), str(ROOT), 0)
        if result <= 32:
            raise RuntimeError("启动窗口未打开，请允许苍雾助手的启动请求。")
        return 0
    if args.restart_idle:
        close_idle_ui()
    env = environment()
    if args.check:
        prepare_adb(env)
        return subprocess.call([str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "tools/run_maa.py"), "--tasks", "check"], env=env, cwd=ROOT)
    exe = ROOT / "app/mxu.exe"
    if not exe.is_file():
        raise FileNotFoundError("缺少 MXU 客户端，请查看 README.md 的环境准备说明。")
    process = subprocess.Popen([str(exe)], cwd=exe.parent, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    (ROOT / "runtime/ui.pid").write_text(str(process.pid), encoding="ascii")
    return 0


def close_idle_ui() -> None:
    from urllib.request import urlopen
    from cangwu.mxu_api import discover
    api = discover(ROOT)
    with urlopen(api + "/interface", timeout=3) as response:
        pi = json.load(response)
    if pi["interface"]["name"] != "CangwuMaa" or Path(pi["basePath"]).resolve() != (ROOT / "app").resolve():
        raise RuntimeError("当前窗口不是这个项目，拒绝重启。")
    with urlopen(api + "/maa/state", timeout=3) as response:
        state = json.load(response)
    if any(item["is_running"] for item in state["instances"].values()):
        raise RuntimeError("助手还有任务在运行，请先停止任务。")
    pid = int((ROOT / "runtime/ui.pid").read_text(encoding="ascii"))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000 | 0x1, False, pid)
    if not handle:
        raise RuntimeError("无法打开旧的空闲助手进程。")
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.c_ulong(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            raise RuntimeError("无法核对旧助手路径。")
        if Path(buffer.value).resolve() != (ROOT / "app/mxu.exe").resolve():
            raise RuntimeError("进程路径不一致，拒绝重启。")
        if not kernel.TerminateProcess(handle, 0):
            raise RuntimeError("旧助手未能退出。")
    finally:
        kernel.CloseHandle(handle)
    time.sleep(1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = f"启动失败：{exc}"
        (ROOT / "runtime").mkdir(exist_ok=True)
        (ROOT / "runtime/launcher-error.txt").write_text(message, encoding="utf-8")
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        else:
            ctypes.windll.user32.MessageBoxW(None, message, "苍雾世界助手", 0x10)
        raise SystemExit(1)
