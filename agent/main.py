from __future__ import annotations
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from maa.library import Library
Library.open(ROOT / "app/maafw", agent_server=True)
from maa.agent.agent_server import AgentServer
from cangwu.maa_action import CangwuTask
from cangwu.runtime import RunLock, load_config
from cangwu.agent_lifecycle import watch_mxu_owner
from launcher import environment


def main() -> None:
    os.environ.update(environment())
    with watch_mxu_owner(ROOT, lambda line: print(line, flush=True)), RunLock():
        (ROOT / "runtime/STOP").unlink(missing_ok=True)
        if not AgentServer.register_custom_action("CangwuTask", CangwuTask()):
            raise RuntimeError("注册 Maa 自定义任务失败。")
        if not AgentServer.start_up(sys.argv[-1]):
            raise RuntimeError("Maa Agent 服务启动失败。")
        print("苍雾 Maa Agent 已连接。", flush=True)
        try:
            AgentServer.join()
        finally:
            AgentServer.shut_down()


if __name__ == "__main__":
    main()
