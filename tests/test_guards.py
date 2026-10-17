from __future__ import annotations

import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

import numpy as np

from cangwu.device import Device, DeviceError
from cangwu.runtime import Context, Halt, RunLock
from cangwu.tasks import arena_budget, sweep_budget, expedition_conditions
from cangwu.vision import Frame, Word


def frame(items: list[tuple[str, int, int]]) -> Frame:
    return Frame(np.zeros((720, 1280, 3), dtype=np.uint8), (1280, 720), [Word(t, .99, (x - 25, y - 10, x + 25, y + 10)) for t, x, y in items], b"fixture")


def sweep(count="5", budget="250>>40", stamina="250/264", extra=None):
    return frame([("扫荡次数", 490, 200), ("剩余次数", 520, 475), ("开始扫荡", 640, 540), (count, 570, 285), (budget, 715, 423), (stamina, 1160, 40)] + (extra or []))


def arena(count="8", consumption="8/8", tickets="8/15"):
    return frame([("代理次数", 490, 200), ("代理消耗", 510, 475), ("开始代理", 640, 540), (count, 570, 285), (consumption, 770, 475), (tickets, 1160, 40)])


class ResourceGuards(unittest.TestCase):
    def test_sweep_accepts_matching_existing_stamina(self):
        self.assertEqual(sweep_budget(sweep(), 42, 0), (5, 250, 40))

    def test_sweep_rejects_wrong_cost(self):
        with self.assertRaises(Halt):
            sweep_budget(sweep(), 40, 0)

    def test_sweep_rejects_reserve_violation(self):
        with self.assertRaises(Halt):
            sweep_budget(sweep(), 42, 50)

    def test_sweep_rejects_stale_preview(self):
        with self.assertRaises(Halt):
            sweep_budget(sweep(stamina="251/264"), 42, 0)

    def test_sweep_rejects_unreadable_cost(self):
        with self.assertRaises(Halt):
            sweep_budget(sweep(budget="250??40"), 42, 0)

    def test_sweep_accepts_split_preview_only_when_budget_matches(self):
        screen = sweep(budget="250>>")
        screen.words.append(Word("40", .99, (770, 414, 790, 432)))
        self.assertEqual(sweep_budget(screen, 42, 0), (5, 250, 40))
        screen.words[-1] = Word("41", .99, (770, 414, 790, 432))
        with self.assertRaises(Halt):
            sweep_budget(screen, 42, 0)

    def test_sweep_never_accepts_purchase_dialog(self):
        with self.assertRaises(Halt):
            sweep_budget(sweep(extra=[("购买体力", 640, 360)]), 42, 0)

    def test_proxy_accepts_only_existing_tickets(self):
        self.assertEqual(arena_budget(arena(), 15), (8, 8))

    def test_proxy_rejects_inconsistent_stock(self):
        with self.assertRaises(Halt):
            arena_budget(arena(consumption="8/100"), 15)

    def test_proxy_obeys_per_run_limit(self):
        with self.assertRaises(Halt):
            arena_budget(arena(), 5)

    def test_proxy_rejects_more_than_owned(self):
        with self.assertRaises(Halt):
            arena_budget(arena(count="9", consumption="9/8"), 15)

    def test_existing_claim_is_not_a_claim_button(self):
        screen = frame([("已领取", 1100, 650)])
        self.assertIsNone(screen.find("领取", exact=True))

    def test_expedition_requires_all_three_condition_checks(self):
        screen = frame([("远征任务", 130, 140), ("队伍需求", 440, 395)])
        for x, y in ((631, 432), (898, 432), (1163, 432)):
            screen.image[y:y + 5, x:x + 5] = (190, 200, 0)
        self.assertTrue(expedition_conditions(screen))
        screen.image[432:437, 898:903] = (0, 0, 230)
        self.assertFalse(expedition_conditions(screen))

    def test_expedition_rejects_unidentified_or_empty_team_screen(self):
        self.assertFalse(expedition_conditions(frame([])))
        self.assertFalse(expedition_conditions(frame([("远征任务", 130, 140), ("队伍需求", 440, 395)])))


class DeviceGuards(unittest.TestCase):
    def test_other_app_prevents_tap(self):
        device = Device({"adb_server_port": 15037})
        device.foreground = Mock(return_value="com.bilibili.trickcalcn")
        device.shell = Mock()
        with self.assertRaises(DeviceError):
            device.tap(1, 1)
        device.shell.assert_not_called()

    def test_default_adb_port_is_forbidden(self):
        with self.assertRaises(DeviceError):
            Device({"adb_server_port": 5037})

    def test_stop_flag_prevents_pending_tap(self):
        ctx = object.__new__(Context)
        ctx.check = Mock(side_effect=Halt("stop"))
        ctx.device = Mock()
        with self.assertRaises(Halt):
            ctx.tap(sweep(), (640, 540), "test")
        ctx.device.tap.assert_not_called()

    def test_loading_frame_prevents_tap(self):
        ctx = object.__new__(Context)
        ctx.check = Mock()
        ctx.device = Mock()
        with self.assertRaises(Halt):
            ctx.tap(frame([("LOADING...", 640, 675)]), (640, 540), "test")
        ctx.device.tap.assert_not_called()

    def test_second_run_cannot_take_control(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.lock"
            with RunLock(path):
                with self.assertRaises(Halt):
                    with RunLock(path):
                        self.fail("second lock acquired")
            with RunLock(path):
                pass


if __name__ == "__main__":
    unittest.main()
