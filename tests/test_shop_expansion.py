from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cangwu.feature_config import CATALOG, SHOPS, save_state, shop_period
from cangwu.runtime import CST, Halt, NeedsSetup
from cangwu.shops import exchange, open_shop, prepare_exchange, shop_ledger
from test_guards import frame


def modal(item, count, stock=50, *, total=None):
    return frame([(f"是否购买{item['name']}?", 600, 183), (f"剩余数量:{stock}", 900, 190),
                  (str(count), 640, 394), (str(item['price'] * count if total is None else total), 670, 442),
                  ("取消", 475, 505), ("确认", 800, 505)])


class ShopExpansion(unittest.TestCase):
    def prepare(self, *, wanted, maximum, stock=50, total=None):
        item = CATALOG['logistics'][0]
        initial, capped = modal(item, 1, stock), modal(item, maximum, stock, total=total)
        ctx = Mock()
        ctx.config = {'limits': {'max_actions': 200}}
        ctx.actions = 0
        ctx.wait.return_value = capped
        return ctx, item, initial, capped

    def test_requested_quantity_above_stock_uses_maximum(self):
        ctx, item, initial, capped = self.prepare(wanted=999, maximum=5, stock=5)
        with patch('cangwu.shops.price_currency', return_value=item['currency']):
            self.assertEqual(prepare_exchange(ctx, initial, item, 999), (capped, 5))
        self.assertEqual([call.args[1] for call in ctx.text.call_args_list], ['MAX'])
        ctx.tap.assert_not_called()

    def test_owned_currency_can_cap_quantity_below_stock(self):
        ctx, item, initial, capped = self.prepare(wanted=999, maximum=3)
        with patch('cangwu.shops.price_currency', return_value=item['currency']):
            self.assertEqual(prepare_exchange(ctx, initial, item, 999)[1], 3)
        ctx.log.assert_called_once()

    def test_request_below_maximum_keeps_requested_quantity(self):
        ctx, item, initial, capped = self.prepare(wanted=2, maximum=5)
        ctx.frame.side_effect = [modal(item, 1), modal(item, 2)]
        with patch('cangwu.shops.price_currency', return_value=item['currency']):
            self.assertEqual(prepare_exchange(ctx, initial, item, 2)[1], 2)
        self.assertNotIn('确认', [call.args[1] for call in ctx.text.call_args_list])

    def test_zero_affordable_maximum_is_skipped(self):
        ctx, item, initial, capped = self.prepare(wanted=999, maximum=0)
        with patch('cangwu.shops.price_currency', return_value=item['currency']):
            self.assertEqual(prepare_exchange(ctx, initial, item, 999)[1], 0)

    def test_maximum_with_wrong_price_or_changed_stock_is_rejected(self):
        for total, stock_after in ((1, 50), (None, 49)):
            ctx, item, initial, capped = self.prepare(wanted=999, maximum=5, total=total)
            if stock_after != 50:
                ctx.wait.return_value = modal(item, 5, stock_after)
            with patch('cangwu.shops.price_currency', return_value=item['currency']):
                with self.assertRaises(Halt):
                    prepare_exchange(ctx, initial, item, 999)
            self.assertNotIn('确认', [call.args[1] for call in ctx.text.call_args_list])

    def test_logistics_target_survives_daily_reset_and_resets_on_monday(self):
        saturday = datetime(2026, 10, 17, 15, tzinfo=CST)
        sunday = datetime(2026, 10, 18, 15, tzinfo=CST)
        monday_before = datetime(2026, 10, 19, 4, 59, tzinfo=CST)
        monday_after = datetime(2026, 10, 19, 5, tzinfo=CST)
        week = shop_period('logistics', saturday)
        self.assertEqual(week, shop_period('logistics', sunday))
        self.assertEqual(week, shop_period('logistics', monday_before))
        self.assertNotEqual(week, shop_period('logistics', monday_after))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'shop.json'
            state = {'version': 1, 'day': week, 'done': {'core_block': 4}, 'pending': None}
            save_state(path, state)
            self.assertEqual(shop_ledger(path, shop_period('logistics', sunday))['done'], {'core_block': 4})
            self.assertEqual(shop_ledger(path, shop_period('logistics', monday_after))['done'], {})
            state['pending'] = {'item': 'core_block', 'quantity': 1}
            save_state(path, state)
            with self.assertRaises(NeedsSetup):
                shop_ledger(path, shop_period('logistics', monday_after))

    def test_removed_dispatch_shop_cannot_navigate_or_exchange(self):
        ctx = Mock()
        with self.assertRaises(Halt):
            exchange(ctx, 'dispatch')
        with self.assertRaises(Halt):
            open_shop(ctx, 'dispatch')
        ctx.home.assert_not_called()
        ctx.text.assert_not_called()

    def test_scrolled_sidebar_can_find_upper_and_lower_shops(self):
        top = frame([('商城系统', 300, 40), ('兑换商店', 740, 150), ('精英商店', 100, 240), ('后勤商店', 100, 430)])
        bottom = frame([('商城系统', 300, 40), ('兑换商店', 740, 150), ('联合商店', 100, 280), ('大师商店', 100, 570)])
        for shop, pages in [('elite', [bottom, bottom, top, top]), ('master', [top, top, top, bottom, bottom])]:
            ctx = Mock()
            ctx.wait.side_effect = pages
            open_shop(ctx, shop)
            self.assertEqual(ctx.text.call_args.args[1], SHOPS[shop]['label'])
            self.assertGreaterEqual(ctx.swipe.call_count, 1)


if __name__ == '__main__':
    unittest.main()
