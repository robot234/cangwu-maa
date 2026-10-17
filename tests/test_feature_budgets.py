import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cangwu.armor import armor, armor_ledger, remaining_rewards
from cangwu.feature_config import CATALOG, BOSSES, SHOPS, armor_selection, game_day, item_node, save_state, shop_selection, saved_feature_overrides
from cangwu.runtime import CST, Halt, NeedsSetup
from cangwu.shops import exchange, exchange_preview, shop_ledger
from test_guards import frame


def options(values):
    context = Mock()
    def node(name):
        return {"action": {"type": "Custom", "param": {"custom_action_param": values.get(name, {"enabled": False, "quantity": 0})}}}
    context.get_node_data.side_effect = node
    return context


class FeatureBudgets(unittest.TestCase):
    def test_multiple_shop_choices_keep_individual_quantities(self):
        context = options({item_node("elite", "echo_gold"): {"enabled": True, "quantity": 2},
                           item_node("elite", "elite_ticket"): {"enabled": True, "quantity": 3}})
        self.assertEqual([(item["id"], count) for item, count in shop_selection(context, "elite")], [("echo_gold", 2), ("elite_ticket", 3)])

    def test_unselected_items_ignore_old_quantity(self):
        context = options({item_node("elite", "echo_gold"): {"enabled": False, "quantity": 999}})
        self.assertEqual(shop_selection(context, "elite"), [])

    def test_armor_total_over_five_rejected_before_navigation(self):
        context = options({item_node("armor", key): {"enabled": True, "quantity": count}
                           for key, count in (("niai", 3), ("sobek", 3))})
        ctx = Mock(maa=context)
        with self.assertRaises(Halt):
            armor(ctx)
        ctx.home.assert_not_called()
        ctx.text.assert_not_called()

    def test_armor_total_five_and_boolean_quantity(self):
        context = options({item_node("armor", "niai"): {"enabled": True, "quantity": 2},
                           item_node("armor", "sobek"): {"enabled": True, "quantity": 3}})
        self.assertEqual(armor_selection(context), {"niai": 2, "sobek": 3})
        context = options({item_node("armor", "niai"): {"enabled": True, "quantity": True}})
        with self.assertRaises(Halt):
            armor_selection(context)

    def test_empty_selection_has_no_game_input(self):
        for task in (lambda ctx: exchange(ctx, "elite"), armor):
            ctx = Mock(maa=options({}))
            task(ctx)
            ctx.text.assert_not_called()
            ctx.home.assert_not_called()

    def test_pending_exchange_survives_new_day(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(path, {"version": 1, "day": "2026-10-16", "done": {}, "pending": {"item": "echo_gold"}})
            with self.assertRaises(NeedsSetup):
                shop_ledger(path, "2026-10-17")

    def test_daily_target_preserved_and_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(path, {"version": 1, "day": "2026-10-17", "done": {"echo_gold": 2}, "pending": None})
            self.assertEqual(shop_ledger(path, "2026-10-17")["done"], {"echo_gold": 2})
            self.assertEqual(shop_ledger(path, "2026-10-18")["done"], {})

    def test_armor_external_spending_does_not_reset_boss_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(path, {"version": 1, "week": "2026-10-12", "remaining": 3, "done": {"niai": 2}, "pending": None})
            self.assertEqual(armor_ledger(path, 1, "2026-10-12")["done"], {"niai": 2})
            self.assertEqual(armor_ledger(path, 5, "2026-10-19")["done"], {})
            with self.assertRaises(NeedsSetup):
                armor_ledger(path, 2, "2026-10-19")

    def test_zero_weekly_counter_skips_all_three_bosses(self):
        screen = frame([("超限共鸣", 65, 165), ("本周可领取奖励次数", 1000, 585), ("(0/5)", 1130, 585)])
        self.assertEqual(remaining_rewards(screen), 0)
        with tempfile.TemporaryDirectory() as directory, patch("cangwu.armor.ROOT", Path(directory)), patch("cangwu.armor.open_armor", return_value=screen):
            ctx = Mock(maa=options({item_node("armor", "niai"): {"enabled": True, "quantity": 5}}))
            armor(ctx)
            ctx.text.assert_not_called()
            self.assertIn("0/5", ctx.result.call_args.args[2])

    def test_counter_direction_and_wrong_denominator(self):
        screen = frame([("超限共鸣", 65, 165), ("本周可领取奖励次数", 1000, 585), ("(3/5)", 1130, 585)])
        self.assertEqual(remaining_rewards(screen), 3)
        screen.words[-1] = screen.words[-1].__class__("3/6", .99, screen.words[-1].box)
        with self.assertRaises(Halt):
            remaining_rewards(screen)

    def test_shop_modal_requires_selected_name_currency_and_total(self):
        item = next(item for item in CATALOG["elite"] if item["id"] == "arena_token")
        screen = frame([("是否购买精英演习券?", 600, 183), ("剩余数量:150", 900, 190),
                        ("2", 640, 394), ("200", 670, 442), ("取消", 475, 505), ("确认", 800, 505)])
        with patch("cangwu.shops.price_currency", return_value="token"):
            self.assertEqual(exchange_preview(screen, item, expected=2).total, 200)
            with self.assertRaises(Halt):
                exchange_preview(screen, item, expected=3)
            with self.assertRaises(Halt):
                exchange_preview(screen, {**item, "price": 101})
            with self.assertRaises(Halt):
                exchange_preview(screen, {**item, "name": "精英调度券"})
        with patch("cangwu.shops.price_currency", return_value="shield"):
            with self.assertRaises(Halt):
                exchange_preview(screen, item)

    def test_new_tasks_default_disabled_and_parameters_reset_each_run(self):
        root = Path(__file__).resolve().parents[1]
        interface = json.loads((root / "app/interface.json").read_text(encoding="utf-8"))
        for key, task_name in ([(key, f"shop_{key}") for key in SHOPS] + [("armor", "armor")]):
            task = next(task for task in interface["task"] if task["name"] == task_name)
            self.assertFalse(task["default_check"])
            self.assertEqual(len(task["option"]), len(BOSSES) if key == "armor" else len(CATALOG[key]))
            for choice_key in task["option"]:
                option = interface["option"][choice_key]
                self.assertEqual(option["type"], "switch")
                self.assertEqual(option["default_case"], "No")
                enabled = next(case for case in option["cases"] if case["name"] == "Yes")
                self.assertTrue(enabled["option"])
                self.assertTrue(set(enabled["pipeline_override"]).issubset(task["pipeline_override"]))
        order = [task["name"] for task in interface["task"]]
        self.assertLess(order.index("armor"), order.index("event"))

    def test_cn_day_boundary(self):
        self.assertEqual(game_day(datetime(2026, 10, 17, 4, 59, tzinfo=CST)), "2026-10-16")
        self.assertEqual(game_day(datetime(2026, 10, 17, 5, 0, tzinfo=CST)), "2026-10-17")

    def test_developer_runner_resets_old_selection_and_reads_saved_quantities(self):
        root = Path(__file__).resolve().parents[1]
        pi = json.loads((root / "app/interface.json").read_text(encoding="utf-8"))
        task = next(task for task in pi["task"] if task["name"] == "shop_elite")
        data = saved_feature_overrides(task, {"choice_elite_echo_gold": {"type": "switch", "value": True},
                                             "quantity_elite_echo_gold": {"type": "input", "values": {"amount": "2"}},
                                             "choice_elite_elite_ticket": {"type": "switch", "value": True},
                                             "quantity_elite_elite_ticket": {"type": "input", "values": {"amount": "3"}}})
        ctx = Mock()
        ctx.get_node_data.side_effect = data.get
        self.assertEqual([(item["id"], count) for item, count in shop_selection(ctx, "elite")], [("echo_gold", 2), ("elite_ticket", 3)])
        ctx.get_node_data.side_effect = saved_feature_overrides(task, {}).get
        self.assertEqual(shop_selection(ctx, "elite"), [])

    def test_sold_out_receipt_is_checked_on_current_page_before_scrolling(self):
        from cangwu.shops import find_card, ShopCard
        from cangwu.vision import Word
        item = next(item for item in CATALOG["elite"] if item["id"] == "arena_token")
        screen = frame([("商城系统", 300, 40), ("精英商店", 100, 230), ("调度商店", 100, 330)])
        sold = ShopCard(item["name"], 100, 0, "unknown", Word(item["name"], .99, (300, 200, 400, 225)))
        ctx = Mock()
        with patch("cangwu.shops.shop_cards", return_value=[sold]):
            self.assertIs(find_card(ctx, screen, item, allow_sold=True)[1], sold)
        ctx.swipe.assert_not_called()

    def test_crystal_picture_must_agree_even_when_title_and_price_match(self):
        item = next(item for item in CATALOG["elite"] if item["id"] == "crystal3")
        screen = frame([("是否购买III型自选水晶?", 600, 183), ("剩余数量:50", 900, 190),
                        ("1", 640, 394), ("125", 670, 442), ("取消", 475, 505), ("确认", 800, 505)])
        with patch("cangwu.shops.crystal_identity", return_value="crystal2"):
            with self.assertRaises(Halt):
                exchange_preview(screen, item, expected=1)


class SubmissionRecords(unittest.TestCase):
    def shop_case(self, folder, *, fail=False, after_stock=8):
        from cangwu.shops import ShopCard, ExchangePreview
        from cangwu.vision import Word
        item = next(item for item in CATALOG["elite"] if item["id"] == "arena_token")
        screen = frame([("商城系统", 300, 40), ("精英商店", 100, 230), ("调度商店", 100, 330)])
        before = ShopCard(item["name"], 100, 10, "token", Word(item["name"], .99, (300, 200, 400, 225)))
        after = ShopCard(item["name"], 100, after_stock, "token", before.word)
        reward = frame([("获得资源", 640, 220)])
        ctx = Mock(maa=options({}))
        ctx.config = {"limits": {"max_actions": 200}}
        ctx.actions = 0
        ctx.save.return_value = "preview.png"
        ctx.wait.side_effect = [screen, screen, Halt("server outcome unknown")] if fail else [screen, screen, reward, screen]
        ctx.dismiss_reward.return_value = screen
        path = Path(folder) / "runtime/state/shop_elite.json"
        def on_text(screen, text, **kwargs):
            if text == "确认":
                pending = json.loads(path.read_text(encoding="utf-8"))["pending"]
                self.assertEqual(pending["quantity"], 2)
                self.assertEqual(pending["total"], 200)
        ctx.text.side_effect = on_text
        patches = [patch("cangwu.shops.ROOT", Path(folder)),
                   patch("cangwu.shops.shop_selection", return_value=[(item, 2)]),
                   patch("cangwu.shops.open_shop", return_value=screen),
                   patch("cangwu.shops.shop_cards", return_value=[before]),
                   patch("cangwu.shops.find_card", side_effect=[(screen, before), (screen, after)]),
                   patch("cangwu.shops.prepare_exchange", return_value=(screen, 2)),
                   patch("cangwu.shops.exchange_preview", return_value=ExchangePreview(2, 200, 10))]
        for scoped in patches:
            self.enterContext(scoped)
        return ctx, path

    def test_confirm_writes_pending_first_and_completed_target_is_not_bought_again(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx, path = self.shop_case(folder)
            exchange(ctx, "elite")
            state = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(state["pending"])
            self.assertEqual(state["done"], {"arena_token": 2})
            ctx.text.assert_called_once()
            ctx.text.reset_mock()
            exchange(ctx, "elite")
            ctx.text.assert_not_called()

    def test_unknown_server_outcome_keeps_pending_and_blocks_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx, path = self.shop_case(folder, fail=True)
            with self.assertRaises(Halt):
                exchange(ctx, "elite")
            self.assertIsNotNone(json.loads(path.read_text(encoding="utf-8"))["pending"])
            ctx.text.reset_mock()
            with self.assertRaises(NeedsSetup):
                exchange(ctx, "elite")
            ctx.text.assert_not_called()

    def test_reward_without_matching_stock_change_does_not_erase_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx, path = self.shop_case(folder, after_stock=10)
            with self.assertRaises(Halt):
                exchange(ctx, "elite")
            state = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNotNone(state["pending"])
            self.assertEqual(state["done"], {})

    def test_armor_success_updates_boss_target_and_shared_remaining(self):
        from test_guards import sweep
        initial = frame([("超限共鸣", 65, 165), ("本周可领取奖励次数", 1000, 585), ("5/5", 1130, 585), ("100/264", 1160, 40)])
        after = frame([("超限共鸣", 65, 165), ("本周可领取奖励次数", 1000, 585), ("3/5", 1130, 585), ("20/264", 1160, 40)])
        settlement = frame([("扫荡结算", 640, 90), ("确认", 640, 610)])
        modal = sweep(count="2", budget="100>>20", stamina="100/264", extra=[("5", 720, 475)])
        with tempfile.TemporaryDirectory() as folder:
            ctx = Mock(maa=options({item_node("armor", "niai"): {"enabled": True, "quantity": 2}}))
            ctx.wait.side_effect = [settlement, after]
            ctx.save.return_value = "preview.png"
            path = Path(folder) / "runtime/state/armor.json"
            def check_pending(screen, text, **kwargs):
                if text == "开始扫荡":
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pending"]["quantity"], 2)
            ctx.text.side_effect = check_pending
            with patch("cangwu.armor.ROOT", Path(folder)), patch("cangwu.armor.open_armor", return_value=initial) as open_page, patch("cangwu.armor.select_stage", return_value=initial) as select, patch("cangwu.armor.prepare_armor", return_value=modal):
                armor(ctx)
                state = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(state["done"], {"niai": 2})
                self.assertEqual(state["remaining"], 3)
                self.assertIsNone(state["pending"])
                open_page.return_value = after
                select.reset_mock()
                armor(ctx)
                select.assert_not_called()


if __name__ == "__main__":
    unittest.main()
