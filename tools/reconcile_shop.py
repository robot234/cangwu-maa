"""Compatibility script for the shared shop receipt review command."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cangwu.runtime import RunLock
from cangwu.shop_review import main as review_main, show_receipt


def main(argv=None) -> int:
    return review_main(argv, root=ROOT, lock=RunLock)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
