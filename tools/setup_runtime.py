"""Install pinned Windows runtimes without changing settings or controlling a game."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from urllib.parse import urlparse
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_write(path: Path, content: bytes, expected: str) -> None:
    if hashlib.sha256(content).hexdigest() != expected:
        raise RuntimeError(f"SHA256 mismatch: {path.name}; no file written")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".setup-part")
    temporary.write_bytes(content)
    temporary.replace(path)


def download(asset: dict, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / Path(urlparse(asset["source"]).path).name
    if target.is_file():
        if digest(target) != asset["sha256"]:
            raise RuntimeError(f"Cached archive checksum mismatch: {target}")
        return target
    temporary = target.with_name(target.name + ".part")
    print(f"Downloading {asset['name']} {asset['version']}...", flush=True)
    try:
        request = Request(asset["source"], headers={"User-Agent": "CangwuMaa-setup"})
        with urlopen(request, timeout=30) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if digest(temporary) != asset["sha256"]:
            raise RuntimeError("Downloaded archive checksum mismatch")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target


def unique_member(archive: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in archive.namelist()
               if name == suffix or name.endswith("/" + suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"Archive has no unique file: {suffix}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Only verify local files; no downloads or writes")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "runtime/downloads")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise RuntimeError("Windows x64 and 64-bit Python are required")
    metadata = json.loads((ROOT / "DEPENDENCIES.json").read_text(encoding="utf-8"))
    expected = metadata["installed_files"]
    missing = {name: sha for name, sha in expected.items()
               if not (ROOT / name).is_file() or digest(ROOT / name) != sha}
    if args.check:
        for name in missing:
            print("Missing or different: " + name)
        print(f"Runtime check: {len(expected) - len(missing)}/{len(expected)} files match")
        return 1 if missing else 0
    models = [name for name in missing if name.startswith("app/resource/")]
    if models:
        raise RuntimeError("Repository OCR resources are missing or modified: " + ", ".join(models))
    assets = {asset["name"]: asset for asset in metadata["downloaded_assets"]}
    if "app/mxu.exe" in missing:
        with zipfile.ZipFile(download(assets["MXU"], args.cache_dir)) as archive:
            verified_write(ROOT / "app/mxu.exe", archive.read(unique_member(archive, "mxu.exe")), expected["app/mxu.exe"])
    native = {name: sha for name, sha in missing.items() if name.startswith("app/maafw/")}
    if native:
        spec = importlib.util.find_spec("maa")
        if spec is None or spec.origin is None:
            raise RuntimeError("Install requirements.txt into .venv first")
        source = Path(spec.origin).parent / "bin"
        for name, sha in native.items():
            verified_write(ROOT / name, (source / Path(name).name).read_bytes(), sha)
    helper_prefix = "app/MaaAgentBinary/"
    if any(name.startswith(helper_prefix) for name in missing):
        with zipfile.ZipFile(download(assets["MaaFramework runtime"], args.cache_dir)) as archive:
            helpers = {name: sha for name, sha in expected.items() if name.startswith(helper_prefix)}
            for name, sha in helpers.items():
                suffix = "MaaAgentBinary/" + name.removeprefix(helper_prefix)
                verified_write(ROOT / name, archive.read(unique_member(archive, suffix)), sha)
            # Keep the official distribution's accompanying license and sources.
            for filename in ("LICENSE", "README.md"):
                content = archive.read(unique_member(archive, "MaaAgentBinary/" + filename))
                verified_write(ROOT / helper_prefix / filename, content, hashlib.sha256(content).hexdigest())
    remaining = [name for name, sha in expected.items()
                 if not (ROOT / name).is_file() or digest(ROOT / name) != sha]
    if remaining:
        raise RuntimeError("Runtime files still missing: " + ", ".join(remaining))
    print(f"Ready: {len(expected)} runtime/model files verified. Start the root .bat launcher.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
