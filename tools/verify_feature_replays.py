"""Real Maa recognition on the captured shops/armor; controller forbids input."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from verify_replays import OfflineImages, Library, Resource, Tasker, Toolkit, Device, CustomAction
from cangwu.feature_config import CATALOG, SHOPS, armor_selection, item_node, shop_selection
from cangwu.shops import matching_card, shop_cards, exchange_preview
from cangwu.armor import remaining_rewards
from cangwu.runtime import Halt
from cangwu.free_gifts import gift_stock, free_confirmation
from cangwu.vision import Vision


def verify_free_gifts(vision, folder):
    assert gift_stock(vision.read((folder / "free_gift_available.png").read_bytes())) == 1
    free_confirmation(vision.read((folder / "free_gift_confirmation.png").read_bytes()))
    assert vision.read((folder / "free_gift_reward.png").read_bytes()).reward()
    assert gift_stock(vision.read((folder / "free_gift_claimed.png").read_bytes())) == 0
    return {"free_gift_zero_cost_and_stock": True, "free_gift_reward_and_disappearance": True}


class VerifyFeatures(CustomAction):
    def __init__(self, free_gifts_only=False, shop_update_only=False):
        super().__init__()
        self.free_gifts_only = free_gifts_only
        self.shop_update_only = shop_update_only

    def run(self, context, argv):
        try:
            return self.verify(context)
        except Exception:
            import traceback
            traceback.print_exc()
            return False

    def verify(self, context):
        vision = Vision(context)
        folder = ROOT / "tests/fixtures"
        if self.free_gifts_only:
            results = verify_free_gifts(vision, folder)
            (ROOT / "runtime/free-gift-replay-verification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print("PASS free gift list, zero-cost confirmation, reward and claimed layout", flush=True)
            return True
        results = {"shops": {}, "armor": {}}
        replay_path = ROOT / ("runtime/shop-update-replay-verification.json" if self.shop_update_only else "runtime/feature-replay-verification.json")
        for shop in SHOPS:
            if self.shop_update_only and shop == "elite":
                continue
            frames = [vision.read(path.read_bytes()) for path in sorted(folder.glob(f"shop_{shop}_*.png"))]
            assert frames, shop
            found = set()
            details = []
            for frame in frames:
                assert not frame.paid_dialog(), "商店重置购买次数误触保护"
                cards = shop_cards(frame)
                details.append([{**card.__dict__, "word": card.word.text} for card in cards])
                for item in CATALOG[shop]:
                    if matching_card(frame, item) is not None:
                        found.add(item["id"])
            missing = [item["id"] for item in CATALOG[shop] if item["id"] not in found]
            results["shops"][shop] = {"found": sorted(found), "missing": missing, "pages": details}
            print(f"{shop}: {len(found)}/{len(CATALOG[shop])}; missing={missing}", flush=True)
            replay_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        for filename in ("shop_elite_shifted.png", "shop_elite_variant_glyphs.png"):
            frame = vision.read((folder / filename).read_bytes())
            for item in CATALOG["elite"]:
                if item["id"].startswith("crystal"):
                    assert matching_card(frame, item) is not None, (filename, item["id"])
        results["exchange_preview"] = {}
        for filename, shop, item_id in (("exchange_arena_token.png", "elite", "arena_token"),
                                         ("exchange_arena_shield.png", "elite", "arena_shield"),
                                         ("exchange_logistics_one.png", "logistics", "core_block"),
                                         ("exchange_union_one.png", "union", "core_crystal10"),
                                         ("exchange_master_one.png", "master", "target_ticket"),
                                         ("exchange_master_crystal1_visual.png", "master", "crystal1"),
                                         ("exchange_master_crystal2_visual.png", "master", "crystal2"),
                                         ("exchange_master_crystal3_visual.png", "master", "crystal3"),
                                         ("exchange_unlimited_shield.png", "elite", "shield"),
                                         ("exchange_crystal_one.png", "elite", "crystal1"),
                                         ("exchange_crystal1_visual.png", "elite", "crystal1"),
                                         ("exchange_crystal2_visual.png", "elite", "crystal2"),
                                         ("exchange_crystal3_visual.png", "elite", "crystal3")):
            if self.shop_update_only and shop == "elite":
                continue
            print("Verify preview: " + filename, flush=True)
            modal = vision.read((folder / filename).read_bytes())
            item = next(item for item in CATALOG[shop] if item["id"] == item_id)
            results["exchange_preview"][filename] = exchange_preview(modal, item, expected=2 if filename.endswith("_two.png") else 1).__dict__
            if filename.endswith("_visual.png"):
                wrong_id = "crystal2" if item_id != "crystal2" else "crystal3"
                try:
                    exchange_preview(modal, {**item, "id": wrong_id}, expected=1)
                except Halt:
                    pass
                else:
                    raise AssertionError("只改预期水晶图，保留价格也必须拒绝")
        results["crystal_visual_separation"] = True
        if self.shop_update_only:
            for shop in ("logistics", "union", "master"):
                first, second = CATALOG[shop][:2]
                context.override_pipeline({item_node(shop, item["id"]): {"custom_action_param": {"enabled": True, "quantity": quantity}}
                                           for item, quantity in ((first, 2), (second, 3))})
                assert [(i["id"], n) for i, n in shop_selection(context, shop)] == [(first["id"], 2), (second["id"], 3)]
                context.override_pipeline({item_node(shop, item["id"]): {"custom_action_param": {"enabled": False, "quantity": 0}} for item in CATALOG[shop]})
                assert shop_selection(context, shop) == []
            results["new_store_independent_parameters_and_reset"] = True
            replay_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            assert all(not s["missing"] for s in results["shops"].values())
            print("PASS three new shops, dialogs, crystal pictures and native parameter reset", flush=True)
            return True
        for boss in ("niai", "sobek", "leviathan"):
            frame = vision.read((folder / f"armor_{boss}_empty.png").read_bytes())
            assert remaining_rewards(frame) == 0, "0/5必须识别为剩余零次"
            results["armor"][boss] = 0
        busy = vision.read((folder / "armor_sobek_busy_background.png").read_bytes())
        assert remaining_rewards(busy) == 0
        results["armor"]["busy_background_counter_reread"] = 0
        results.update(verify_free_gifts(vision, folder))
        selected = shop_selection(context, "elite")
        assert [(item["id"], quantity) for item, quantity in selected] == [("echo_gold", 2), ("elite_ticket", 3)], selected
        assert armor_selection(context) == {"niai": 2, "sobek": 3}
        results["independent_parameter_nodes"] = True
        context.override_pipeline({item_node("elite", item["id"]): {"custom_action_param": {"enabled": False, "quantity": 0}} for item in CATALOG["elite"]})
        assert shop_selection(context, "elite") == [], "取消勾选后不得沿用缓存选择"
        results["unselect_resets_cached_parameters"] = True
        replay_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        assert all(not shop["missing"] for shop in results["shops"].values()), "有商品未被实际Maa识别"
        print("PASS feature replays and native parameter overrides", flush=True)
        return True


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--free-gifts-only", action="store_true")
    parser.add_argument("--shop-update-only", action="store_true")
    args = parser.parse_args()
    Toolkit.init_option(ROOT / "runtime/feature-replay", {"stdout_level": 2})
    resource = Resource()
    resource.register_custom_action("VerifyFeatures", VerifyFeatures(args.free_gifts_only, args.shop_update_only))
    Device.completed(resource.post_bundle(ROOT / "app/resource"), "加载新功能资源")
    controller = OfflineImages()
    Device.completed(controller.post_connection(), "连接离线截图")
    tasker = Tasker()
    tasker.bind(resource, controller)
    definitions = json.loads((ROOT / "app/interface.json").read_text(encoding="utf-8"))
    overrides = [next(task for task in definitions["task"] if task["name"] == key)["pipeline_override"]
                 for key in ("shop_elite", "armor")]
    for key, item, quantity in (("elite", "echo_gold", 2), ("elite", "elite_ticket", 3), ("armor", "niai", 2), ("armor", "sobek", 3)):
        overrides.append({item_node(key, item): {"action": {"type": "Custom", "param": {
            "custom_action": "CangwuParameter", "custom_action_param": {"enabled": True, "quantity": quantity}}}}})
    if args.free_gifts_only:
        overrides = []
    overrides.append({"VerifyFeatures": {"recognition": "DirectHit", "action": {"type": "Custom", "param": {"custom_action": "VerifyFeatures"}}}})
    return 0 if tasker.post_task("VerifyFeatures", overrides).wait().succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
