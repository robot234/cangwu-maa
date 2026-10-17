"""Build a self-contained Windows x64 ZIP; never copy local settings or state."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

from setup_runtime import digest, download

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = {
    "README.md", "THIRD_PARTY_NOTICES.md", "DEPENDENCIES.json", "launcher.py",
    "config.example.json", "requirements.txt", "requirements-windows.lock",
    "启动苍雾助手.bat", "停止助手.bat", "仅检查连接.bat",
}
EXTRA_FILES = {
    "app/interface.json", "app/icon.ico", "app/icon.png", "app/welcome.md", "app/description.md",
    "tools/run_maa.py", "tools/setup_runtime.py", "tools/validate_resources.py",
}
SOURCE_DIRS = ("agent/", "cangwu/", "assets/", "app/resource/", "schemas/", "licenses/", "docs/")


def run(command: list[str], cwd: Path, timeout: int = 180, env=None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=timeout)


def copy_sources(target: Path) -> None:
    # Git is only used to enumerate public source files. Untracked local data
    # can never enter the archive, even if a developer has modified .gitignore.
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=15)
    names = result.stdout.decode("utf-8").split("\0")
    selected = {name for name in names if name in TOP_LEVEL or name in EXTRA_FILES or name.startswith(SOURCE_DIRS)}
    missing = (TOP_LEVEL | EXTRA_FILES) - selected
    if missing:
        raise RuntimeError("Stage new public files before building: " + ", ".join(sorted(missing)))
    for name in sorted(selected):
        source = ROOT / name
        if source.is_symlink() or not source.is_file():
            raise RuntimeError("Expected a regular source file: " + name)
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def verify_layout(target: Path) -> None:
    forbidden = {"config.local.json", "pyvenv.cfg", "direct_url.json", "launcher-error.txt", ".env"}
    for path in target.rglob("*"):
        relative = path.relative_to(target)
        if path.is_symlink() or path.name in forbidden or path.suffix.lower() in {".pyc", ".pdb", ".lnk", ".dmp", ".part"}:
            raise RuntimeError("Local or intermediate file in package: " + str(relative))
        if relative.parts[0] == "runtime" or relative.parts[:2] in {("app", "config"), ("app", "logs"), ("app", "debug"), ("app", "cache")}:
            raise RuntimeError("User state in package: " + str(relative))
        if path.is_dir() and path.name in {"tests", "__pycache__"}:
            raise RuntimeError("Development directory in package: " + str(relative))
    for name in ("python.exe", "pythonw.exe", "python313.dll", "python313.zip", "python313._pth", "LICENSE.txt"):
        if not (target / ".venv/Scripts" / name).is_file():
            raise RuntimeError("Incomplete embedded Python: " + name)


def trim_dependency_tests(packages: Path) -> None:
    # Remove upstream test suites, retaining public helpers such as numpy.testing
    # and each dependency's license, metadata, and runtime data files.
    for path in sorted(packages.rglob("tests"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            if path.is_symlink() or not path.resolve().is_relative_to(packages.resolve()):
                raise RuntimeError("Unexpected dependency test directory")
            shutil.rmtree(path)
    # Remove RECORD entries for the omitted test files and console wrappers.
    # Every retained row still refers to the original wheel file and hash.
    for record in packages.glob("*.dist-info/RECORD"):
        with record.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream))
        retained = []
        for row in rows:
            path = (packages / row[0]).resolve()
            if path.is_relative_to(packages.resolve()) and path.is_file():
                retained.append(row)
        with record.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, lineterminator="\n").writerows(retained)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runtime/dist")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "runtime/downloads")
    parser.add_argument("--wheel-dir", type=Path, help="Use an offline directory of hash-locked wheels")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32 or sys.version_info[:2] != (3, 13):
        raise RuntimeError("Build with Windows x64 CPython 3.13")
    interface = json.loads((ROOT / "app/interface.json").read_text(encoding="utf-8"))
    version = interface["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("Expected a numeric release version")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    zip_path = output / "CangwuMaa-Windows-x64.zip"
    if zip_path.exists() or (output / "SHA256SUMS.txt").exists():
        raise RuntimeError("Output already exists; choose a fresh output directory")
    target = output / f"CangwuMaa-v{version}"
    target.mkdir()  # Never reset or reuse an existing installation.
    copy_sources(target)

    metadata = json.loads((ROOT / "DEPENDENCIES.json").read_text(encoding="utf-8"))
    python_asset = next(a for a in metadata["downloaded_assets"] if a["name"] == "Python embeddable")
    scripts = target / ".venv/Scripts"
    scripts.mkdir(parents=True)
    with zipfile.ZipFile(download(python_asset, args.cache_dir.resolve())) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if Path(info.filename).name != info.filename:
                raise RuntimeError("Unexpected path in official embedded Python archive")
            (scripts / info.filename).write_bytes(archive.read(info))
    # Paths are relative to this file, independent of cwd, PYTHONHOME, or the
    # developer's Python installation. Do not import global/user site packages.
    (scripts / "python313._pth").write_text(
        "python313.zip\n.\n..\\Lib\\site-packages\n..\\..\\\n", encoding="utf-8"
    )
    packages = target / ".venv/Lib/site-packages"
    command = [sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check", "install", "--no-compile",
               "--no-warn-script-location", "--retries", "1", "--timeout", "30", "--target", str(packages),
               "-r", str(ROOT / "requirements-windows.lock")]
    if args.wheel_dir:
        command.extend(["--no-index", "--find-links", str(args.wheel_dir.resolve())])
    run(command, ROOT)
    # pip can create command-line wrappers with absolute build-machine paths.
    # The application only needs importable runtime modules.
    for name in ("bin", "Scripts"):
        path = packages / name
        if path.is_dir():
            if path.is_symlink() or not path.resolve().is_relative_to(target.resolve()):
                raise RuntimeError("Unexpected generated scripts directory")
            shutil.rmtree(path)
    for path in sorted(packages.rglob("__pycache__"), reverse=True):
        if path.is_dir():
            if path.is_symlink() or not path.resolve().is_relative_to(target.resolve()):
                raise RuntimeError("Unexpected cache directory")
            shutil.rmtree(path)
    for path in packages.rglob("*.pyc"):
        path.unlink()
    trim_dependency_tests(packages)
    python = str(scripts / "python.exe")
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    environment.pop("MAAFW_BINARY_PATH", None)
    run([python, "-B", "tools/setup_runtime.py", "--cache-dir", str(args.cache_dir.resolve())], target, env=environment)
    run([python, "-B", "tools/setup_runtime.py", "--check"], target, env=environment)
    run([python, "-B", "tools/validate_resources.py"], target, env=environment)
    run([python, "-B", "launcher.py", "--help"], target, env=environment)
    verify_layout(target)
    manifest = {"version": version, "platform": "windows-x64", "python": python_asset["version"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "files": {p.relative_to(target).as_posix(): digest(p) for p in sorted(target.rglob("*")) if p.is_file()}}
    (target / "PACKAGE.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(target.name) / path.relative_to(target)).as_posix())
    checksum = digest(zip_path)
    (output / "SHA256SUMS.txt").write_text(f"{checksum}  {zip_path.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(zip_path), "bytes": zip_path.stat().st_size, "sha256": checksum}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
