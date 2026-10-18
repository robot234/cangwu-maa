"""Verified, transactional project updates. Never write user settings or ledgers."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "robot234/cangwu-maa"
ASSET = "CangwuMaa-Update.zip"
MAX_ARCHIVE = 300 * 1024 * 1024
MAX_EXPANDED = 600 * 1024 * 1024
TOP_FILES = {"README.md", "THIRD_PARTY_NOTICES.md", "DEPENDENCIES.json", "PACKAGE.json", "launcher.py",
             "config.example.json", "requirements.txt", "requirements-windows.lock",
             "启动苍雾助手.bat", "停止助手.bat", "仅检查连接.bat",
             "app/mxu.exe", "app/interface.json", "app/icon.ico", "app/icon.png", "app/welcome.md", "app/description.md",
             "tools/run_maa.py", "tools/setup_runtime.py", "tools/validate_resources.py"}
PREFIXES = ("agent/", "cangwu/", "assets/", "app/resource/", "schemas/", "licenses/", "docs/")
RESOURCE_PREFIXES = ("assets/", "app/resource/")


class UpdateError(RuntimeError):
    pass


class Cancelled(UpdateError):
    pass


class Busy(UpdateError):
    pass


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise UpdateError("更新记录无法读取，请重新检查更新。") from exc


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value) if isinstance(value, str) else None
    if not match:
        raise UpdateError("更新版本号无效。")
    return tuple(map(int, match.groups()))


def managed_name(name: str) -> bool:
    if not isinstance(name, str) or "\\" in name or any(ord(c) < 32 for c in name):
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or any(p in (".", "..") for p in path.parts):
        return False
    for part in path.parts:
        if ":" in part or part.endswith((".", " ")) or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            return False
    return (name in TOP_FILES or name.startswith(PREFIXES)) and not any(
        p.lower() in {"__pycache__", ".git", "tests"} for p in path.parts
    ) and path.suffix.lower() not in {".pyc", ".pdb", ".dmp", ".log"}


def runtime_id(root: Path) -> str:
    return hashlib.sha256(b"CangwuMaa/python313/windows-x64\n" + (root / "requirements-windows.lock").read_bytes()).hexdigest()


@contextmanager
def exclusive(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise Busy("已有更新正在处理，请稍后再试。") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise Busy("已有更新正在处理，请稍后再试。") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class Updater:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        interface = read_json(self.root / "app/interface.json")
        if not isinstance(interface, dict) or interface.get("name") != "CangwuMaa":
            raise UpdateError("当前目录不是苍雾世界助手。")
        self.current = interface["version"]
        version(self.current)
        self.directory = self.root / "runtime/updates"
        self.state_path = self.directory / "status.json"
        self.started = time.time()
        self.automatic = False

    def preferences(self) -> dict:
        value = read_json(self.directory / "preferences.json", {"auto_update": True})
        if not isinstance(value, dict) or type(value.get("auto_update")) is not bool:
            raise UpdateError("自动更新设置无效。")
        return value

    def configure(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise UpdateError("自动更新开关无效。")
        previous = self.preferences()["auto_update"]
        write_json(self.directory / "preferences.json", {"auto_update": enabled})
        if enabled and not previous:
            write_json(self.directory / "schedule.json", {"last_attempt": 0})
        return self.status()

    def status(self) -> dict:
        result = read_json(self.state_path, {"phase": "idle", "message": "可检查更新或同步当前版本的资源。"})
        if not isinstance(result, dict):
            raise UpdateError("更新状态无效，请重新检查更新。")
        result = {**result, "current_version": self.current, **self.preferences()}
        if result.get("phase") in {"checking", "downloading", "waiting_exit", "installing"}:
            pid = result.get("pid")
            if type(pid) is int and not process_alive(pid):
                result.update(phase="error", message="上次更新已中断，原设置和运行记录保留。请重新检查更新。")
        return result

    def set_status(self, phase: str, message: str, **values) -> dict:
        state = {"phase": phase, "message": message, "pid": os.getpid(), "updated_at": time.time(), **values}
        write_json(self.state_path, state)
        return state

    def check_cancel(self) -> None:
        if self.automatic and not self.preferences()["auto_update"]:
            raise Cancelled("已关闭自动更新。")
        cancelled = read_json(self.directory / "cancel.json", {"at": 0})
        if cancelled.get("at", 0) >= self.started:
            raise Cancelled("已取消更新。")

    def release(self, tag: str | None = None) -> dict:
        path = "tags/" + tag if tag else "latest"
        url = f"https://api.github.com/repos/{REPOSITORY}/releases/{path}"
        try:
            with urlopen(Request(url, headers={"User-Agent": "CangwuMaa-Updater", "Accept": "application/vnd.github+json"}), timeout=18) as response:
                data = json.load(response)
        except (HTTPError, URLError, TimeoutError):
            # Public GitHub API quotas are shared by IP. The same repository's
            # release page and published checksum asset also work without login.
            page = f"https://github.com/{REPOSITORY}/releases/" + ("tag/" + tag if tag else "latest")
            with urlopen(Request(page, headers={"User-Agent": "CangwuMaa-Updater"}, method="HEAD"), timeout=20) as response:
                final = urlsplit(response.geturl())
            match = re.fullmatch(r"/" + re.escape(REPOSITORY) + r"/releases/tag/(v\d+\.\d+\.\d+)", unquote(final.path))
            if final.scheme != "https" or final.hostname != "github.com" or not match or (tag and tag != match[1]):
                raise UpdateError("无法确认公开发布版本，请稍后再试。")
            return {"tag_name": match[1], "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{match[1]}",
                    "assets": [], "checksum_fallback": True}
        if data.get("draft") or data.get("prerelease"):
            raise UpdateError("更新版本尚未正式发布。")
        version(data["tag_name"])
        return data

    def asset(self, release: dict) -> dict:
        if release.get("checksum_fallback"):
            base = f"https://github.com/{REPOSITORY}/releases/download/{release['tag_name']}/"
            try:
                with urlopen(Request(base + "SHA256SUMS.txt", headers={"User-Agent": "CangwuMaa-Updater"}), timeout=20) as response:
                    content = response.read(64 * 1024 + 1)
                if len(content) > 64 * 1024:
                    raise UpdateError("更新校验清单大小异常。")
                matches = re.findall(r"(?m)^([0-9a-f]{64})  " + re.escape(ASSET) + r"\r?$", content.decode("ascii"))
                if len(matches) != 1:
                    raise UpdateError("该版本尚未提供自动更新包，请下载 Windows 完整版。")
                with urlopen(Request(base + ASSET, headers={"User-Agent": "CangwuMaa-Updater"}, method="HEAD"), timeout=20) as response:
                    size = int(response.headers.get("Content-Length", "0"))
                release = {**release, "assets": [{"name": ASSET, "state": "uploaded", "size": size,
                           "digest": "sha256:" + matches[0], "browser_download_url": base + ASSET}]}
            except HTTPError as exc:
                raise UpdateError("该版本的更新资源暂时无法下载，请稍后重试或下载 Windows 完整版。") from exc
        candidates = [a for a in release.get("assets", []) if a.get("name") == ASSET and a.get("state") == "uploaded"]
        if len(candidates) != 1:
            raise UpdateError("该版本尚未提供自动更新包，请下载 Windows 完整版。")
        asset = candidates[0]
        prefix = f"https://github.com/{REPOSITORY}/releases/download/{release['tag_name']}/"
        if not asset.get("browser_download_url", "").startswith(prefix):
            raise UpdateError("更新包来源与项目不一致。")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest", "")):
            raise UpdateError("更新包缺少完整校验信息，请稍后重新检查。")
        if type(asset.get("size")) is not int or not 1 <= asset["size"] <= MAX_ARCHIVE:
            raise UpdateError("更新包大小异常。")
        return asset

    def check(self) -> dict:
        self.check_cancel()
        self.set_status("checking", "正在检查更新……")
        release = self.release()
        self.check_cancel()
        newer = version(release["tag_name"]) > version(self.current)
        if newer:
            self.asset(release)
        return self.set_status("available" if newer else "current", "发现新版本，可更新脚本和识别资源。" if newer else "当前已是最新版本。",
                               latest_version=release["tag_name"].removeprefix("v"), checked_at=time.time(), automatic=self.automatic,
                               release_url=release.get("html_url", ""))

    def download(self, asset: dict, target: Path) -> None:
        deadline = time.monotonic() + 600
        partial = target.with_suffix(".part")
        digest = hashlib.sha256()
        received, reported = 0, 0.0
        try:
            with urlopen(Request(asset["browser_download_url"], headers={"User-Agent": "CangwuMaa-Updater"}), timeout=25) as response, partial.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    self.check_cancel()
                    if time.monotonic() >= deadline:
                        raise UpdateError("下载超时，请检查网络后重试。")
                    received += len(chunk)
                    if received > asset["size"]:
                        raise UpdateError("下载大小与发布记录不一致。")
                    stream.write(chunk)
                    digest.update(chunk)
                    if time.monotonic() - reported > .5:
                        reported = time.monotonic()
                        self.set_status("downloading", "正在下载并校验更新……", downloaded=received, total=asset["size"], automatic=self.automatic)
            if received != asset["size"] or "sha256:" + digest.hexdigest() != asset["digest"]:
                raise UpdateError("更新包校验失败，原程序未改动。")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)

    def unpack(self, archive: Path, destination: Path, expected_version: str) -> dict:
        with zipfile.ZipFile(archive) as package:
            entries = [entry for entry in package.infolist() if not entry.is_dir()]
            if len(entries) > 20000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
                raise UpdateError("更新包内容大小异常。")
            names = [entry.filename for entry in entries]
            if len({name.casefold() for name in names}) != len(names):
                raise UpdateError("更新包存在重复路径。")
            if names.count("UPDATE.json") != 1 or package.getinfo("UPDATE.json").file_size > 2 * 1024 * 1024:
                raise UpdateError("更新包缺少有效清单。")
            manifest = json.loads(package.read("UPDATE.json"))
            if (not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("project") != "CangwuMaa"
                    or manifest.get("platform") != "windows-x64" or version(manifest.get("version", "")) != version(expected_version)):
                raise UpdateError("更新包的项目、版本或平台不匹配。")
            if manifest.get("runtime_id") != runtime_id(self.root):
                raise UpdateError("这个版本需要升级运行环境，请下载 Windows 完整版更新。")
            files = manifest.get("files")
            if not isinstance(files, dict) or not files or set(names) != {*files, "UPDATE.json"}:
                raise UpdateError("更新包文件与清单不一致。")
            if any(not managed_name(name) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) for name, digest in files.items()):
                raise UpdateError("更新包包含不允许覆盖的路径。")
            required = {"app/interface.json", "app/mxu.exe", "launcher.py", "cangwu/updater.py", "agent/main.py", "PACKAGE.json"}
            if not required.issubset(files) or not any(name.startswith("app/resource/") for name in files):
                raise UpdateError("更新包缺少程序或识别资源。")
            for entry in entries:
                if stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                    raise UpdateError("更新包包含链接或加密文件。")
                if entry.filename == "UPDATE.json":
                    continue
                self.check_cancel()
                output = destination / entry.filename
                output.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, output.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                if sha(output) != files[entry.filename]:
                    raise UpdateError("更新文件校验失败，原程序未改动。")
            interface = read_json(destination / "app/interface.json")
            if (not isinstance(interface, dict) or interface.get("name") != "CangwuMaa"
                    or version(interface.get("version", "")) != version(expected_version)):
                raise UpdateError("更新包内的程序版本与发布版本不一致。")
            inventory = read_json(destination / "PACKAGE.json")
            if not isinstance(inventory, dict) or not isinstance(inventory.get("files"), dict):
                raise UpdateError("更新包缺少文件校验记录。")
            if any(inventory["files"].get(name) != digest for name, digest in files.items() if name != "PACKAGE.json"):
                raise UpdateError("更新包与完整版的文件校验记录不一致。")
            write_json(destination / "UPDATE.json", manifest)
            return manifest

    def destination(self, name: str) -> Path:
        if not managed_name(name):
            raise UpdateError("拒绝覆盖个人设置或运行记录。")
        target = self.root / name
        if not target.resolve().is_relative_to(self.root) or any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(self.root)):
            raise UpdateError("更新目标路径包含链接，已停止覆盖。")
        return target

    def verify_targets(self, manifest: dict, mode: str) -> dict:
        files = {name: digest for name, digest in manifest["files"].items()
                 if mode != "resources" or name.startswith(RESOURCE_PREFIXES)}
        if not files:
            raise UpdateError("更新包没有可同步的资源。")
        baseline = read_json(self.root / "PACKAGE.json", {}).get("files", {})
        for name, digest in files.items():
            target = self.destination(name)
            if target.exists() and not target.is_file():
                raise UpdateError("更新目标不是普通文件。")
            if mode != "resources" and target.is_file() and baseline and name != "PACKAGE.json":
                observed = sha(target)
                if observed != digest and observed != baseline.get(name):
                    raise UpdateError("检测到本地文件已修改，已暂停更新。请先保留自己的改动，再手动更新。")
        if mode != "resources" and not baseline and (self.root / ".git").exists():
            result = subprocess.run(["git", "status", "--porcelain", "--", *files], cwd=self.root,
                                    capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode or result.stdout.strip():
                raise UpdateError("检测到本地源码修改，已暂停自动覆盖。")
        return files

    def prepare(self, mode: str = "program") -> dict:
        self.check_cancel()
        if self.automatic:
            schedule = read_json(self.directory / "schedule.json", {"last_attempt": 0})
            if time.time() - schedule.get("last_attempt", 0) < 6 * 3600:
                return self.status()
            if self.status().get("phase") == "ready":
                return self.status()
            write_json(self.directory / "schedule.json", {"last_attempt": time.time()})
        (self.directory / "prepared.json").unlink(missing_ok=True)
        self.set_status("checking", "正在准备资源同步……" if mode == "resources" else "正在检查更新……", automatic=self.automatic)
        release = self.release("v" + self.current if mode == "resources" else None)
        target_version = release["tag_name"].removeprefix("v")
        if mode != "resources" and version(target_version) <= version(self.current):
            return self.set_status("current", "当前已是最新版本。", checked_at=time.time())
        if mode == "resources" and version(target_version) != version(self.current):
            raise UpdateError("资源版本与当前脚本不一致。")
        asset = self.asset(release)
        work = self.directory / ("work-" + uuid.uuid4().hex)
        work.mkdir(parents=True)
        self.download(asset, work / ASSET)
        manifest = self.unpack(work / ASSET, work / "payload", target_version)
        self.verify_targets(manifest, mode)
        self.check_cancel()
        write_json(self.directory / "prepared.json", {"work": work.name, "version": target_version, "mode": mode,
                   "digest": asset["digest"], "automatic": self.automatic, "requested_at": self.started})
        return self.set_status("ready", "资源已校验，空闲时可同步并重启助手。" if mode == "resources" else "更新已校验，空闲时可安装并重启助手。",
                               target_version=target_version, mode=mode, automatic=self.automatic,
                               release_url=release.get("html_url", ""))

    def replace_file(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".cangwu-" + uuid.uuid4().hex + ".tmp")
        try:
            shutil.copyfile(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def apply(self, staging: Path, manifest: dict, mode: str) -> None:
        files = self.verify_targets(manifest, mode)
        for name, digest in files.items():
            source = staging / name
            if source.is_symlink() or not source.resolve().is_relative_to(staging.resolve()) or not source.is_file() or sha(source) != digest:
                raise UpdateError("待安装文件已变化，请重新下载更新。")
        backup = self.directory / ("backup-" + uuid.uuid4().hex)
        backup.mkdir(parents=True)
        applied = []
        # Commit the version and package inventory last.
        ordered = sorted(files, key=lambda name: (name in {"app/interface.json", "PACKAGE.json"}, name))
        try:
            for name in ordered:
                self.check_cancel()
                target = self.destination(name)
                existed = target.is_file()
                if existed:
                    saved = backup / name
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, saved)
                applied.append((name, existed))
                self.replace_file(staging / name, target)
                if sha(target) != files[name]:
                    raise UpdateError("安装后的文件校验失败。")
        except Exception as exc:
            failures = []
            for name, existed in reversed(applied):
                try:
                    target = self.destination(name)
                    if existed:
                        self.replace_file(backup / name, target)
                    else:
                        target.unlink(missing_ok=True)
                except Exception:
                    failures.append(name)
            write_json(backup / "rollback.json", {"restored": not failures, "failed": failures})
            if failures:
                raise UpdateError("更新未完成，部分文件无法恢复。原文件已保存在 runtime/updates 的备份目录，请关闭助手后恢复。") from exc
            raise UpdateError("更新未完成，已恢复原文件；设置和运行记录保留。") from exc
        write_json(backup / "result.json", {"version": manifest["version"], "mode": mode, "files": list(files), "completed": True})

    def install(self, parent_pid: int | None = None) -> dict:
        prepared = read_json(self.directory / "prepared.json")
        if not isinstance(prepared, dict) or not re.fullmatch(r"work-[0-9a-f]{32}", prepared.get("work", "")):
            raise UpdateError("没有已校验的更新包，请先检查更新。")
        mode = prepared.get("mode")
        if mode not in {"program", "resources"}:
            raise UpdateError("更新类型无效。")
        self.automatic = bool(prepared.get("automatic"))
        self.started = prepared.get("requested_at", self.started)
        work = self.directory / prepared["work"]
        if work.is_symlink() or not work.resolve().is_relative_to(self.directory.resolve()):
            raise UpdateError("更新缓存路径无效。")
        if "sha256:" + sha(work / ASSET) != prepared.get("digest"):
            raise UpdateError("更新缓存已变化，请重新下载。")
        # Re-extract the authenticated archive instead of trusting mutable cache
        # metadata, including UPDATE.json, left by the download process.
        staging = work / ("install-" + uuid.uuid4().hex)
        manifest = self.unpack(work / ASSET, staging, prepared["version"])
        if manifest.get("runtime_id") != runtime_id(self.root) or version(manifest["version"]) != version(prepared["version"]):
            raise UpdateError("待安装更新与当前运行环境不匹配。")
        if ((mode == "program" and version(manifest["version"]) <= version(self.current))
                or (mode == "resources" and version(manifest["version"]) != version(self.current))):
            raise UpdateError("待安装的更新版本已不适用，请重新检查。")
        self.check_cancel()
        if parent_pid:
            self.set_status("waiting_exit", "更新已准备好，正在重启助手……", mode=mode)
            deadline = time.monotonic() + 45
            while process_alive(parent_pid) and time.monotonic() < deadline:
                self.check_cancel()
                time.sleep(.2)
            if process_alive(parent_pid):
                raise UpdateError("助手尚未退出，本次未覆盖任何文件。")
        self.set_status("installing", "正在同步资源……" if mode == "resources" else "正在安装更新……", mode=mode)
        # Same lock used by the game Agent. Acquire only after the UI and its
        # Agents have exited; never stop a game task in order to update.
        from .runtime import RunLock
        with RunLock():
            self.apply(staging, manifest, mode)
        (self.directory / "prepared.json").unlink(missing_ok=True)
        return self.set_status("completed", "识别资源已同步。" if mode == "resources" else "脚本和识别资源已更新。",
                               installed_version=manifest["version"], mode=mode, checked_at=time.time())


def process_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


def restart(root: Path) -> None:
    subprocess.Popen([str(root / ".venv/Scripts/pythonw.exe"), "-B", str(root / "launcher.py"), "--host"], cwd=root,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True, choices=("status", "configure", "check", "prepare", "sync", "install", "cancel"))
    parser.add_argument("--enabled", choices=("on", "off"))
    parser.add_argument("--automatic", action="store_true")
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    updater = Updater()
    updater.automatic = args.automatic
    should_restart = False
    try:
        if args.action == "status":
            result = updater.status()
        elif args.action == "configure":
            if args.enabled is None:
                raise UpdateError("缺少自动更新设置。")
            result = updater.configure(args.enabled == "on")
        elif args.action == "cancel":
            write_json(updater.directory / "cancel.json", {"at": time.time()})
            result = updater.status()
            if result.get("phase") == "ready":
                (updater.directory / "prepared.json").unlink(missing_ok=True)
                result = updater.set_status("cancelled", "已取消更新。")
        else:
            with exclusive(updater.directory / "worker.lock"):
                if args.action == "install":
                    # Restart only if this updater observed the UI actually exit.
                    should_restart = bool(args.parent_pid)
                    result = updater.install(args.parent_pid)
                elif args.action == "check":
                    result = updater.check()
                else:
                    result = updater.prepare("resources" if args.action == "sync" else "program")
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except Busy as exc:
        if sys.stdout is not None:
            print(json.dumps({"error": str(exc), "busy": True}, ensure_ascii=False))
        return 2
    except Exception as exc:
        message = str(exc) if isinstance(exc, UpdateError) else "更新未完成，请检查网络和目录权限后重试。"
        if args.action not in {"status", "configure"}:
            updater.set_status("cancelled" if isinstance(exc, Cancelled) else "error", message, checked_at=time.time())
        if sys.stdout is not None:
            print(json.dumps({"error": message}, ensure_ascii=False))
        return 1
    finally:
        if should_restart and args.parent_pid and not process_alive(args.parent_pid):
            restart(updater.root)


if __name__ == "__main__":
    raise SystemExit(main())
