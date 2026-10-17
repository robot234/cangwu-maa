import time
import unittest
from unittest.mock import Mock

from cangwu.runtime import NeedsSetup
from cangwu.tasks import arena_settle, expedition, expedition_repeat_plan
from test_guards import frame


MISSION = "新人驾驶员培养协助"


def expedition_page(button, teams="2/2"):
    screen = frame([
        ("远征任务", 140, 145), ("队伍需求", 440, 395),
        (MISSION, 200, 200), (teams, 330, 108),
        (button, 1030, 590), ("12小时", 760, 590),
    ])
    for x, y in ((631, 432), (898, 432), (1163, 432)):
        screen.image[y:y + 5, x:x + 5] = (190, 200, 0)
    return screen


def expedition_context(pages):
    ctx = Mock()
    ctx.config = {"expedition": {"hours": 12, "missions": [MISSION]}}
    ctx.home.return_value = frame([("指挥室", 120, 670)])
    screens = iter([ctx.home.return_value, *pages])

    def wait(predicate, description, **kwargs):
        screen = next(screens)
        if not predicate(screen):
            raise AssertionError(description)
        return screen

    ctx.wait.side_effect = wait
    return ctx


def repeat_page(missions=None, hours="12.0"):
    missions = missions or [MISSION, MISSION + "-攻击"]
    return frame([
        ("远征探索", 640, 105), ("再次派遣", 770, 590), ("取消", 495, 590),
        *[(name + hours + "小时", 425, 185 + index * 115) for index, name in enumerate(missions)],
    ])


class ExpeditionRecovery(unittest.TestCase):
    def test_bulk_claim_repeats_both_completed_missions_and_verifies_running_state(self):
        claimable = expedition_page("点击领取")
        running = expedition_page("取消")
        running.words += frame([("剩余时间", 900, 550), (MISSION + "-攻击", 200, 375)]).words
        base = frame([("战斗训练", 400, 200), ("体能锻炼", 600, 200), ("远征探索", 700, 200)])
        ctx = expedition_context([base, claimable, claimable, repeat_page(), running, running, running, running])
        ctx.config["expedition"]["missions"].append(MISSION + "-攻击")

        expedition(ctx)

        repeat_calls = [call for call in ctx.text.call_args_list if call.args[1] == "再次派遣"]
        self.assertEqual(len(repeat_calls), 1)
        self.assertEqual(ctx.result.call_args.args[1], "completed")
        self.assertIn("收取 2 次，续派 2 次", ctx.result.call_args.args[2])
        ctx.dismiss_reward.assert_not_called()
        ctx.frame.assert_not_called()

    def test_pending_repeat_dialog_can_resume_without_leaving_the_page_first(self):
        running = expedition_page("取消")
        running.words += frame([("剩余时间", 900, 550)]).words
        ctx = expedition_context([running, running])
        ctx.config["expedition"]["missions"] = [MISSION]
        pages = iter([repeat_page([MISSION]), running, running])
        ctx.wait.side_effect = lambda predicate, *args, **kwargs: self.validated_page(predicate, next(pages))

        expedition(ctx)

        ctx.home.assert_not_called()
        self.assertIn("恢复上次领取后的续派", ctx.result.call_args.args[2])

    def validated_page(self, predicate, page):
        self.assertTrue(predicate(page))
        return page

    def test_repeat_rejects_unconfigured_missions_or_different_duration(self):
        config = {"hours": 12, "missions": [MISSION, MISSION + "-攻击"]}
        self.assertEqual(expedition_repeat_plan(repeat_page(), config), config["missions"])
        for screen in (repeat_page(["陌生远征"]), repeat_page(hours="8.0")):
            with self.subTest(screen=screen.words):
                with self.assertRaises(NeedsSetup):
                    expedition_repeat_plan(screen, config)

    def test_full_team_count_does_not_hide_claimable_reward_and_redispatch(self):
        claimable = expedition_page("点击领取")
        idle = expedition_page("派遣", "1/2")
        running = expedition_page("取消")
        reward = frame([("获得资源", 640, 240)])
        base = frame([("战斗训练", 400, 200), ("体能锻炼", 600, 200), ("远征探索", 700, 200)])
        ctx = expedition_context([base, claimable, claimable, reward, running])
        ctx.dismiss_reward.return_value = idle
        ctx.frame.return_value = idle

        expedition(ctx)

        self.assertEqual(ctx.tap.call_count, 2)
        self.assertEqual(ctx.tap.call_args.args[1], (1030, 590))
        self.assertIn("领取", ctx.tap.call_args.args[2])
        self.assertEqual(ctx.text.call_args.args[1:], ("派遣", "开始远征", "确认派遣"))
        self.assertEqual(ctx.result.call_args.args[1], "completed")
        self.assertIn("收取 1 次，续派 1 次", ctx.result.call_args.args[2])

    def test_running_expedition_is_never_cancelled(self):
        running = expedition_page("取消")
        running.words += frame([("剩余时间", 900, 550)]).words
        base = frame([("战斗训练", 400, 200), ("体能锻炼", 600, 200), ("远征探索", 700, 200)])
        ctx = expedition_context([base, running, running])

        expedition(ctx)

        self.assertEqual(ctx.tap.call_count, 1)  # Only select the mission row.
        ctx.frame.assert_not_called()
        self.assertEqual(ctx.result.call_args.args[1], "already_done")

    def test_full_team_count_with_unknown_status_is_not_reported_as_done(self):
        unknown = expedition_page("已领取")
        base = frame([("战斗训练", 400, 200), ("体能锻炼", 600, 200), ("远征探索", 700, 200)])
        ctx = expedition_context([base, unknown, unknown])

        with self.assertRaises(NeedsSetup):
            expedition(ctx)

        self.assertEqual(ctx.tap.call_count, 1)
        ctx.result.assert_not_called()


class ArenaRecovery(unittest.TestCase):
    def test_confirmation_variants_wait_for_loading_and_finish_without_new_proxy(self):
        for label in ("确认", "确定", "刷新"):
            with self.subTest(label=label):
                start = frame([("作战演习", 280, 40), ("代理完成", 1000, 660)])
                ready = frame([("扫荡结算", 640, 90), (label, 640, 610)])
                loading = frame([("扫荡结算", 640, 90), (label, 640, 610), ("LOADING...", 640, 675)])
                arena_page = frame([("作战演习", 280, 40), ("代理作战", 890, 660)])
                ctx = Mock()
                ctx.deadline = time.monotonic() + 10
                ctx.frame.side_effect = [loading, ready]
                ctx.wait.return_value = arena_page

                def safe_click(screen, *texts, **kwargs):
                    self.assertFalse(screen.is_loading())
                    self.assertTrue(screen.has(*texts, region=kwargs.get("region"), exact=kwargs.get("exact", False)))
                    self.assertNotIn("开始代理", texts)

                ctx.text.side_effect = safe_click
                self.assertIs(arena_settle(ctx, start), arena_page)
                self.assertEqual(ctx.text.call_count, 2)
                self.assertEqual(ctx.text.call_args.args[1], label)

    def test_existing_confirmation_dialog_is_finished_without_claiming_again(self):
        ready = frame([("扫荡结算", 640, 90), ("确认", 640, 610)])
        arena_page = frame([("作战演习", 280, 40), ("代理作战", 890, 660)])
        ctx = Mock()
        ctx.deadline = time.monotonic() + 10
        ctx.wait.return_value = arena_page

        self.assertIs(arena_settle(ctx, ready), arena_page)
        ctx.frame.assert_not_called()
        ctx.text.assert_called_once()
        self.assertEqual(ctx.text.call_args.args[1], "确认")

    def test_refresh_or_confirmation_outside_result_dialog_is_not_a_result(self):
        self.assertIsNone(frame([("刷新", 640, 610)]).settlement_confirm())
        self.assertIsNone(frame([("扫荡结算", 640, 90), ("刷新", 1100, 610)]).settlement_confirm())


if __name__ == "__main__":
    unittest.main()
