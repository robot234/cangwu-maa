"""Keep uncertain event sweeps blocked across task and application restarts."""
from pathlib import Path

from .feature_config import load_state
from .runtime import NeedsSetup


def event_ledger(path: Path) -> dict:
    state = load_state(path, {"version": 1, "pending": None, "last": None})
    if type(state.get("version")) is not int or state["version"] != 1 or "pending" not in state:
        raise NeedsSetup("活动扫荡记录格式无效，请先核对上次执行结果。")
    # A new day or a different event cannot resolve an earlier submission.
    if state["pending"] is not None:
        raise NeedsSetup(f"上次活动扫荡已提交但结果待核对，不会再次消耗体力。请核对游戏及记录：{path}")
    return state
