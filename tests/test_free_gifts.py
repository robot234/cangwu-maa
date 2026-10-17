import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from cangwu.free_gifts import free_confirmation, free_gifts, gift_stock
from cangwu.runtime import Halt, NeedsSetup
from test_guards import frame


def gift_list(stock=1, price="0"):
    return frame([("商城系统", 300, 40), ("首页推荐", 195, 145), ("通常礼包", 110, 525),
                  ("免费礼包", 380, 225), (f"剩余{stock}", 455, 257), (price, 410, 417)])


def confirmation(price="0", title="是否购买免费礼包?"):
    return frame([("商城系统", 300, 40), ("首页推荐", 195, 145), ("通常礼包", 110, 525),
                  (title, 600, 183), ("礼包包含", 640, 225), ("剩余数量:1", 900, 190),
                  (price, 655, 442), ("取消", 475, 505), ("确认", 800, 505)])


def claimed_list():
    return frame([("商城系统", 300, 40), ("首页推荐", 195, 145), ("通常礼包", 110, 525),
                  ("艾吉斯·调度补给包1", 385, 225), ("艾吉斯·调度补给包2", 385, 478),
                  ("艾吉斯·调度补给包3", 660, 225)])


class FreeGiftGuards(unittest.TestCase):
    def test_claimed_pack_disappears_and_known_reflow_is_recognized(self):
        self.assertEqual(gift_stock(claimed_list()), 0)

    def test_missing_pack_without_full_known_layout_is_not_success(self):
        for screen in (frame([("商城系统", 300, 40), ("首页推荐", 195, 145), ("通常礼包", 110, 525)]),
                       claimed_list()):
            if len(screen.words) > 3:
                screen.words = screen.words[:-1]
            with self.assertRaises(NeedsSetup):
                gift_stock(screen)
        screen = claimed_list()
        screen.words += frame([("免费礼包", 950, 225)]).words
        with self.assertRaises(NeedsSetup):
            gift_stock(screen)

    def test_reward_overlay_or_payment_warning_is_not_a_claimed_list(self):
        for text in ("获得资源", "充值"):
            screen = claimed_list()
            screen.words += frame([(text, 640, 265)]).words
            with self.assertRaises(Halt):
                gift_stock(screen)
    def test_only_verified_zero_cost_free_pack_is_accepted(self):
        with patch("cangwu.free_gifts.template_match", return_value=(617, 440)):
            free_confirmation(confirmation())
            with self.assertRaises(Halt):
                free_confirmation(confirmation(price="6"))
            with self.assertRaises(Halt):
                free_confirmation(confirmation(title="是否购买艾吉斯调度补给包?"))
            with self.assertRaises(Halt):
                free_confirmation(confirmation(price="?"))

    def test_list_requires_zero_price_and_known_currency_icon(self):
        with patch("cangwu.free_gifts.template_match", return_value=(357, 417)):
            self.assertEqual(gift_stock(gift_list()), 1)
            with self.assertRaises(Halt):
                gift_stock(gift_list(price="1"))
        with patch("cangwu.free_gifts.template_match", return_value=None):
            with self.assertRaises(Halt):
                gift_stock(gift_list())

    def test_claimed_pack_does_not_open_or_confirm_any_purchase(self):
        for after in (gift_list(0), claimed_list()):
            with self.subTest(after=after.words):
                ctx = Mock()
                ctx.wait.side_effect = [after] * 3
                free_gifts(ctx)
                self.assertEqual(ctx.result.call_args.args[1], "already_done")
                self.assertFalse(any(call.args[1] in ("免费礼包", "确认") for call in ctx.text.call_args_list))
                ctx.swipe.assert_not_called()

    def test_claim_checks_reward_and_stock_zero_or_disappearance_then_finishes(self):
        available, modal, reward = gift_list(), confirmation(), frame([("获得资源", 640, 220)])
        for after in (gift_list(0), claimed_list()):
            with self.subTest(after=after.words):
                ctx = Mock()
                ctx.wait.side_effect = [available, available, available, modal, reward, after]
                ctx.dismiss_reward.return_value = after
                with patch("cangwu.free_gifts.template_match", return_value=(617, 440)):
                    free_gifts(ctx)
                self.assertEqual(sum(call.args[1] == "确认" for call in ctx.text.call_args_list), 1)
                self.assertEqual(ctx.result.call_args.args[1], "completed")

    def test_uncertain_result_is_not_confirmed_twice(self):
        available, modal = gift_list(), confirmation()
        ctx = Mock()
        ctx.wait.side_effect = [available, available, available, modal, Halt("network timeout")]
        with patch("cangwu.free_gifts.template_match", return_value=(617, 440)):
            with self.assertRaises(Halt):
                free_gifts(ctx)
        self.assertEqual(sum(call.args[1] == "确认" for call in ctx.text.call_args_list), 1)
        ctx.result.assert_not_called()

    def test_reward_without_stock_zero_does_not_report_success_or_repeat(self):
        available, modal, reward = gift_list(), confirmation(), frame([("获得资源", 640, 220)])
        ctx = Mock()
        ctx.wait.side_effect = [available, available, available, modal, reward, available]
        ctx.dismiss_reward.return_value = available
        with patch("cangwu.free_gifts.template_match", return_value=(617, 440)):
            with self.assertRaises(Halt):
                free_gifts(ctx)
        self.assertEqual(sum(call.args[1] == "确认" for call in ctx.text.call_args_list), 1)
        ctx.result.assert_not_called()

    def test_actual_purchase_warning_is_not_exempted_by_shop_header(self):
        screen = gift_list()
        screen.words += frame([("重置购买次数", 880, 380)]).words
        self.assertFalse(screen.paid_dialog())
        screen.words += frame([("充值", 640, 350)]).words
        self.assertTrue(screen.paid_dialog())

    def test_task_is_default_checked_and_has_correct_entry(self):
        root = Path(__file__).resolve().parents[1]
        pi = json.loads((root / "app/interface.json").read_text(encoding="utf-8"))
        task = next(task for task in pi["task"] if task["name"] == "free_gifts")
        self.assertTrue(task["default_check"])
        self.assertEqual(task["entry"], "CangwuFreeGifts")


if __name__ == "__main__":
    unittest.main()
