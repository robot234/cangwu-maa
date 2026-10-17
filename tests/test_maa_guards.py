from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from cangwu.device import Device
from cangwu.runtime import Context, Halt
from cangwu.maa_action import CangwuTask, task_config
from cangwu.tasks import arena_settle
from test_guards import frame


class MaaGuards(unittest.TestCase):
    def test_native_stop_is_observed_inside_custom_task(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = object.__new__(Context)
            ctx.stop = threading.Event()
            ctx.stop_file = Path(directory) / "STOP"
            ctx.maa = Mock()
            ctx.maa.tasker.stopping = True
            ctx.deadline = time.monotonic() + 60
            ctx.actions = 0
            ctx.config = {"limits": {"max_actions": 200}}
            with self.assertRaises(Halt):
                ctx.check()

    def test_arena_settlement_waits_until_loading_finishes(self):
        start = frame([("作战演习", 280, 40), ("代理完成", 1000, 660)])
        loading = frame([("扫荡结算", 640, 90), ("刷新", 640, 610), ("LOADING...", 640, 675)])
        ready = frame([("扫荡结算", 640, 90), ("刷新", 640, 610)])
        arena_page = frame([("作战演习", 280, 40), ("代理作战", 890, 660)])
        ctx = Mock()
        ctx.deadline = time.monotonic() + 10
        ctx.frame.side_effect = [loading, ready]
        ctx.wait.return_value = arena_page
        def safe_click(screen, *args, **kwargs):
            self.assertFalse(screen.is_loading(), "结算还在加载时不应点击")
        ctx.text.side_effect = safe_click
        self.assertIs(arena_settle(ctx, start), arena_page)
        self.assertEqual(ctx.text.call_count, 2)

    def test_stop_after_foreground_check_still_prevents_native_click(self):
        controller = Mock()
        device = Device({}, controller=controller, check=Mock(side_effect=Halt("stop")))
        device.require_game = Mock()
        with self.assertRaises(Halt):
            device.tap(640, 540)
        controller.post_click.assert_not_called()

    def test_ui_cannot_exceed_arena_ticket_limit(self):
        for value in (-1, 16, 999):
            with self.assertRaises(ValueError):
                task_config({"max_tickets": value})

    def test_ui_cannot_use_invalid_expedition_hours(self):
        with self.assertRaises(ValueError):
            task_config({"hours": 6})

    def test_ui_cannot_use_negative_stamina_reserve(self):
        with self.assertRaises(ValueError):
            task_config({"reserve_stamina": -1})

    def test_failed_action_requests_stop_for_the_whole_queue(self):
        context = Mock()
        argv = Mock(custom_action_param='{"task":"unknown"}')
        import contextlib
        import io
        from unittest.mock import patch
        action = CangwuTask()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(action.run(context, argv))
            with patch("cangwu.maa_action.Context") as constructor:
                self.assertFalse(action.run(context, Mock(custom_action_param='{"task":"check"}')))
                constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
