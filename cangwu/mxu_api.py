"""Find only this project's MXU when its default HTTP port is occupied."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from urllib.request import Request, urlopen


def discover(root: Path) -> str:
    def probe(port):
        base = f"http://127.0.0.1:{port}/api"
        try:
            with urlopen(base + "/interface", timeout=.7) as response:
                info = json.load(response)
            if info.get("interface", {}).get("name") == "CangwuMaa" and Path(info.get("basePath", "")).resolve() == (root / "app").resolve():
                return base
        except (OSError, ValueError):
            pass
        return None
    with ThreadPoolExecutor(max_workers=10) as executor:
        candidates = list(executor.map(probe, range(12701, 12711)))
    found = [candidate for candidate in candidates if candidate]
    if not found:
        raise ConnectionError("没有找到本项目的苍雾助手窗口，请先启动助手。")
    return found[-1]


def request(api: str, path: str, body=None, *, timeout=30):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    with urlopen(Request(api + path, data=data, headers={"Content-Type": "application/json"}), timeout=timeout) as response:
        return json.load(response)
