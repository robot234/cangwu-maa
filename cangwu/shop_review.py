"""Inspect a blocked shop receipt, or explicitly reconcile it after human review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cangwu.feature_config import CATALOG, SHOPS
from cangwu.runtime import Halt, NeedsSetup, RunLock
from cangwu.shop_receipts import review_receipt, resolve_receipt


def show_receipt(state: dict, shop: str, digest: str) -> None:
    pending = state["pending"]
    item = next(item for item in CATALOG[shop] if item["id"] == pending["item"])
    print(f"\n{SHOPS[shop]['label']}：{item['name']} ×{pending['quantity']}")
    print(f"原记录周期：{state['day']}；总价：{pending['total']} {CATALOG['currency_labels'][pending['currency']]}")
    print("记录指纹：" + digest)
    print(json.dumps(pending, ensure_ascii=False, indent=2))
    print("\n正常商店任务会先自动重新识别核对；本工具仅用于仍无法判断时的人工处理。")
    print("请比较同一次兑换前后的余额、商品剩余数量及奖励截图。")
    print("只看现在买不起、换日后库存刷新或没有奖励弹窗，都不能证明此前没有成交。")
    print("旧版记录可能缺少前后数字，请结合原运行目录的预览、超时截图及游戏记录核对；无法确认就保持锁定。")


def main(argv=None, *, root=None, lock=None) -> int:
    root = ROOT if root is None else root
    lock = RunLock if lock is None else lock
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shop", choices=SHOPS)
    parser.add_argument("--interactive", action="store_true", help="逐步显示并确认；默认可取消")
    parser.add_argument("--outcome", choices=("completed", "not_exchanged"))
    parser.add_argument("--note", help="人工核对依据")
    parser.add_argument("--evidence", type=Path, help="实际核对的 PNG/JPEG 游戏截图")
    parser.add_argument("--expected-record", help="查看时显示的完整记录指纹")
    parser.add_argument("--apply", action="store_true", help="明确提交人工核对结果；不带此参数只查看")
    args = parser.parse_args(argv)
    if args.interactive and any((args.outcome, args.note, args.evidence, args.expected_record, args.apply)):
        parser.error("交互核对不能与提交参数混用。")
    if not args.interactive and not args.shop:
        parser.error("请指定 --shop，或使用 --interactive。")
    if args.apply and not all((args.outcome, args.note, args.evidence, args.expected_record)):
        parser.error("提交必须同时提供 --outcome、--note、--evidence 和 --expected-record。")
    try:
        # The same device lock as normal tasks prevents editing their receipt
        # while they run. This utility never connects to the game or submits it.
        with lock():
            shop = args.shop
            if shop is None:
                print("请先停止日常任务。核对不会点击游戏，也不会自动重新兑换。")
                choices = list(SHOPS)
                for index, name in enumerate(choices, 1):
                    print(f"{index}. {SHOPS[name]['label']}")
                choice = input("选择商店（回车取消）：").strip()
                if not choice:
                    return 0
                if not choice.isascii() or not choice.isdecimal() or not 1 <= int(choice) <= len(choices):
                    raise NeedsSetup("商店选项无效，未更改记录。")
                shop = choices[int(choice) - 1]
            path = root / f"runtime/state/shop_{shop}.json"
            state, digest = review_receipt(path, shop)
            show_receipt(state, shop, digest)
            if args.interactive:
                choice = input("\n1=已确认全部成交，2=已确认完全未成交，0或回车=不确定、保留锁定：").strip()
                if choice in ("", "0"):
                    print("已保留原记录，未解除、未重试。")
                    return 0
                if choice not in ("1", "2"):
                    raise NeedsSetup("结果选项无效，未更改记录。")
                outcome = "completed" if choice == "1" else "not_exchanged"
                note = input("填写核对依据（例如前后余额及库存数字）：").strip()
                evidence = Path(input("输入核对过的游戏截图完整路径：").strip().strip('"'))
                label = "全部成交，将计入原记录周期的已兑换数量" if outcome == "completed" else "完全未成交，已兑换数量保持不变"
                print(f"\n将记录为：{label}。\n核对依据：{note}\n核对截图：{evidence}")
                if input("输入「确认核对」保存备份并解除，回车取消：").strip() != "确认核对":
                    print("已取消，未更改记录。")
                    return 0
                expected = digest
            elif not args.apply:
                print("\n本次仅查看，未更改记录。")
                return 0
            else:
                outcome, note, evidence, expected = args.outcome, args.note, args.evidence, args.expected_record
            audit = resolve_receipt(path, shop, outcome=outcome, note=note,
                                    evidence=evidence, expected_digest=expected)
            print("\n已按人工核对结果解除；原记录、核对截图及说明已保存：" + str(audit))
            print("本工具没有重新兑换。确认任务选项后，可再启动正常日常任务。")
            return 0
    except (Halt, NeedsSetup, OSError, EOFError) as exc:
        print("\n未完成核对：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
