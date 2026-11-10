"""Archive the original receipt before automatic or explicit human reconciliation."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import uuid

import cv2
import numpy as np

from .feature_config import CATALOG, save_state
from .runtime import CST, NeedsSetup
from .shop_recovery import assess_receipt


def receipt_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def review_receipt(path: Path, shop: str) -> tuple[dict, str]:
    try:
        data = path.read_bytes()
        state = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise NeedsSetup("无法读取商店记录，请先保留文件并检查路径。") from exc
    if (not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1
            or not isinstance(state.get("day"), str) or not state["day"]
            or not isinstance(state.get("done"), dict)
            or any(not isinstance(key, str) or type(value) is not int or value < 0
                   for key, value in state["done"].items())):
        raise NeedsSetup("商店记录格式无效，不能解除。")
    pending = state.get("pending")
    if not isinstance(pending, dict):
        raise NeedsSetup("该商店没有可核对的待确认兑换；未更改记录。")
    item = next((item for item in CATALOG.get(shop, []) if item["id"] == pending.get("item")), None)
    quantity = pending.get("quantity")
    if (item is None or type(quantity) is not int or not 1 <= quantity <= 999
            or type(pending.get("total")) is not int or pending["total"] != item["price"] * quantity
            or pending.get("currency") != item["currency"]):
        raise NeedsSetup("待确认商品、数量、总价或币种无效，不能解除。")
    return state, receipt_digest(data)


def resolve_receipt(path: Path, shop: str, *, outcome: str, note: str,
                    evidence: Path, expected_digest: str) -> Path:
    """Caller holds the application's RunLock; every change keeps exact backup bytes."""
    if outcome not in ("completed", "not_exchanged") or not note.strip():
        raise NeedsSetup("必须明确选择已成交或未成交，并填写人工核对依据。")
    state, digest = review_receipt(path, shop)
    if not expected_digest or digest != expected_digest:
        raise NeedsSetup("商店记录已变化，请重新查看后核对；未更改记录。")
    return _commit_resolution(path, state, digest, outcome=outcome, note=note,
                              evidence=evidence, authority="manual_review")


def resolve_automatic_receipt(path: Path, shop: str, *, before: dict, observations: list[dict],
                              expected_digest: str) -> tuple[Path, str]:
    state, digest = review_receipt(path, shop)
    if not expected_digest or digest != expected_digest:
        raise NeedsSetup("商店记录在自动核对期间发生变化，保留待核对。")
    outcome, reason = assess_receipt(state, shop, before, observations)
    if outcome is None:
        raise NeedsSetup(reason + "；保留待核对，不重复提交。")
    evidence = observations[-1].get("evidence")
    if not isinstance(evidence, str) or not evidence:
        raise NeedsSetup("自动核对缺少新截图，保留待核对。")
    first_evidence = observations[0].get("evidence")
    if not isinstance(first_evidence, str) or not first_evidence:
        raise NeedsSetup("自动核对缺少第一次新截图，保留待核对。")
    if Path(first_evidence).resolve() == Path(evidence).resolve():
        raise NeedsSetup("两次核对引用了同一个截图文件，保留待核对。")
    extras = [Path(first_evidence)]
    old_evidence = before.get("evidence")
    if isinstance(old_evidence, str) and Path(old_evidence).is_file():
        extras.append(Path(old_evidence))
    audit = _commit_resolution(path, state, digest, outcome=outcome, note=reason,
                               evidence=Path(evidence), authority="automatic_screen_review",
                               details={"before": before, "observations": observations},
                               extra_evidence=extras)
    return audit, outcome


def _commit_resolution(path: Path, state: dict, digest: str, *, outcome: str, note: str,
                        evidence: Path, authority: str, details: dict | None = None,
                        extra_evidence: list[Path] | None = None) -> Path:
    evidence, image = _read_review_image(evidence)
    extras = [_read_review_image(path) for path in (extra_evidence or [])]
    original = path.read_bytes()
    if receipt_digest(original) != digest:
        raise NeedsSetup("商店记录已变化，请重新查看后核对；未更改记录。")
    stamp = datetime.now(CST)
    folder = path.parent / "reconciled"
    folder.mkdir(exist_ok=True)
    prefix = folder / f"{path.stem}_{stamp:%Y%m%d_%H%M%S_%f}_{uuid.uuid4().hex[:8]}"
    backup = prefix.with_suffix(".original.json")
    with backup.open("xb") as handle:
        handle.write(original)
    # Preserve the exact before/after evidence alongside the original receipt.
    image_path = prefix.with_suffix(".png" if image.startswith(b"\x89PNG") else ".jpg")
    with image_path.open("xb") as handle:
        handle.write(image)
    resolution = {"authority": authority, "outcome": outcome, "note": note.strip(),
                  "reviewed_at": stamp.isoformat(), "original_sha256": digest,
                  "original_record": str(backup), "evidence": str(image_path),
                  "source_evidence": str(evidence), "evidence_sha256": receipt_digest(image),
                  "period": state["day"], "pending": state["pending"]}
    if details is not None:
        resolution.update(details)
    if extras:
        resolution["additional_evidence"] = []
        for index, (source, content) in enumerate(extras, 1):
            target = prefix.with_suffix(f".evidence{index}" + (".png" if content.startswith(b"\x89PNG") else ".jpg"))
            with target.open("xb") as handle:
                handle.write(content)
            resolution["additional_evidence"].append({"source": str(source), "copy": str(target),
                                                       "sha256": receipt_digest(content)})
    audit = prefix.with_suffix(".resolution.json")
    with audit.open("x", encoding="utf-8") as handle:
        json.dump(resolution, handle, ensure_ascii=False, indent=2)
    if receipt_digest(path.read_bytes()) != digest:
        raise NeedsSetup("商店记录已变化；已保留核对备份，但未解除，请重新核对。")
    pending = state["pending"]
    if outcome == "completed":
        state["done"][pending["item"]] = state["done"].get(pending["item"], 0) + pending["quantity"]
    # Credit the original period only. A later normal run performs its usual
    # day/week reset; resolving an old receipt never credits today's purchases.
    state["pending"] = None
    state["last_resolution"] = str(audit)
    save_state(path, state)
    return audit


def _read_review_image(evidence: Path) -> tuple[Path, bytes]:
    try:
        evidence = evidence.resolve(strict=True)
        image = evidence.read_bytes()
    except OSError as exc:
        raise NeedsSetup("请提供实际核对过的游戏截图文件。") from exc
    if not (image.startswith(b"\x89PNG\r\n\x1a\n") or image.startswith(b"\xff\xd8\xff")):
        raise NeedsSetup("核对依据必须是 PNG 或 JPEG 游戏截图。")
    if cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR) is None:
        raise NeedsSetup("核对截图无法解码，请保留记录并选择完整的游戏截图。")
    return evidence, image
