import unittest
from unittest.mock import Mock

from cangwu.runtime import Halt, NeedsSetup
from cangwu.tasks import collect_mission_points, mission_point_claims, missions
from test_guards import frame


def points_page(period="每日任务", statuses=None, points=None):
    counter, tiers = {
        "每日任务": ("170/100", [(100, 898)]),
        "每周任务": ("190/150", [(90, 663), (150, 898)]),
        "每月任务": ("450/450", [(150, 580), (300, 740), (450, 898)]),
    }[period]
    statuses = statuses or ["已领取"] * len(tiers)
    return frame([
        ("每日任务", 170, 165), ("每周任务", 170, 265), ("每月任务", 170, 365),
        ("积分点数", 75, 644), (points or counter, 220, 644), ("一键领取", 1100, 655),
        *[(str(needed) + "积分", x, 658) for needed, x in tiers],
        *[(status, x, 630) for status, (_, x) in zip(statuses, tiers)],
    ])


def checked_wait(pages):
    pages = iter(pages)

    def wait(predicate, description, **kwargs):
        page = next(pages)
        if not predicate(page):
            raise AssertionError(description)
        return page

    return wait


class MissionPointRewards(unittest.TestCase):
    def test_bottom_claim_is_independent_of_disabled_bulk_claim(self):
        page = points_page(statuses=["可领取"])
        self.assertFalse(page.colored(page.find("一键领取"), "orange"))
        self.assertEqual([word.center for word in mission_point_claims(page, "每日任务")], [(898, 630)])

    def test_received_and_unreached_rewards_are_not_clicked(self):
        for period, count in (("每日任务", 1), ("每周任务", 2), ("每月任务", 3)):
            self.assertEqual(mission_point_claims(points_page(period), period), [])
            self.assertEqual(mission_point_claims(points_page(period, ["已获取"] * count), period), [])
        self.assertEqual(mission_point_claims(points_page(statuses=["不可领取"], points="90/100"), "每日任务"), [])

    def test_split_counter_is_joined_only_for_adjacent_aligned_digits(self):
        page = points_page("每周任务")
        page.words = [word for word in page.words if word.text != "190/150"]
        page.words += frame([("190", 190, 643), ("/150", 240, 645)]).words
        self.assertEqual(mission_point_claims(page, "每周任务"), [])

    def test_counter_parts_from_different_rows_are_rejected(self):
        page = points_page("每周任务")
        page.words = [word for word in page.words if word.text != "190/150"]
        page.words += frame([("190", 190, 620), ("/150", 240, 660)]).words
        with self.assertRaises(Halt):
            mission_point_claims(page, "每周任务")

    def test_claim_text_in_the_task_list_is_not_a_bottom_reward(self):
        page = points_page()
        page.words += frame([("可领取", 1100, 320)]).words
        self.assertEqual(mission_point_claims(page, "每日任务"), [])

    def test_unknown_status_after_reaching_threshold_stops_instead_of_reporting_done(self):
        with self.assertRaises(Halt):
            mission_point_claims(points_page(statuses=["状态未知"]), "每日任务")

    def test_inconsistent_points_or_different_tab_are_rejected(self):
        with self.assertRaises(Halt):
            mission_point_claims(points_page(statuses=["可领取"], points="90/100"), "每日任务")
        with self.assertRaises(Halt):
            mission_point_claims(points_page("每周任务"), "每日任务")
        partial = points_page("每月任务")
        partial.words = [word for word in partial.words if word.text != "300积分"]
        with self.assertRaises(NeedsSetup):
            mission_point_claims(partial, "每月任务")

    def test_reward_popup_is_closed_and_received_status_is_confirmed(self):
        before = points_page(statuses=["可领取"])
        reward = frame([("获得资源", 640, 240)])
        after = points_page(statuses=["已获取"])
        ctx = Mock()
        ctx.wait.side_effect = checked_wait([reward, after])
        ctx.dismiss_reward.return_value = after

        self.assertIs(collect_mission_points(ctx, "每日任务", before), after)

        ctx.tap.assert_called_once()
        self.assertEqual(ctx.tap.call_args.args[1], (898, 630))
        ctx.dismiss_reward.assert_called_once_with(reward)
        self.assertEqual(ctx.result.call_args.args[1], "completed")
        self.assertIn("已领取 1 档", ctx.result.call_args.args[2])

    def test_multiple_weekly_tiers_are_claimed_once_each(self):
        before = points_page("每周任务", ["可领取", "可领取"])
        first = points_page("每周任务", ["已领取", "可领取"])
        after = points_page("每周任务")
        ctx = Mock()
        ctx.wait.side_effect = checked_wait([first, first, after, after])

        collect_mission_points(ctx, "每周任务", before)

        self.assertEqual([call.args[1] for call in ctx.tap.call_args_list], [(663, 630), (898, 630)])
        self.assertIn("已领取 2 档", ctx.result.call_args.args[2])

    def test_missing_receipt_does_not_report_success_or_repeat_the_click(self):
        ctx = Mock()
        ctx.wait.side_effect = Halt("timeout")
        with self.assertRaises(Halt):
            collect_mission_points(ctx, "每日任务", points_page(statuses=["可领取"]))
        ctx.tap.assert_called_once()
        ctx.result.assert_not_called()

    def test_missions_collects_points_even_when_the_task_list_has_no_claims(self):
        before = points_page(statuses=["可领取"])
        reward = frame([("获得资源", 640, 240)])
        daily = points_page()
        weekly, monthly = points_page("每周任务"), points_page("每月任务")
        ctx = Mock()
        ctx.home.return_value = frame([("任务", 1200, 300)])
        ctx.wait.side_effect = checked_wait([before, before, before, reward, daily, weekly, weekly, monthly, monthly])
        ctx.dismiss_reward.return_value = daily

        missions(ctx)

        ctx.tap.assert_called_once()
        reports = {call.args[0]: call.args[1] for call in ctx.result.call_args_list}
        self.assertEqual(reports["每日任务"], "already_done")
        self.assertEqual(reports["每日任务积分奖励"], "completed")
        self.assertEqual(reports["每周任务积分奖励"], "already_done")
        self.assertEqual(reports["每月任务积分奖励"], "already_done")


if __name__ == "__main__":
    unittest.main()
