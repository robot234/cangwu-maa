"""Verify Maa OCR, templates and budgets against real screenshots without input."""
from pathlib import Path
import json
import sys
import numpy as np
import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maa.library import Library
Library.open(ROOT / "app/maafw")
from maa.custom_action import CustomAction
from maa.controller import CustomController
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit
from cangwu.device import Device
from cangwu.vision import Vision, template_match
from cangwu.tasks import sweep_budget, arena_budget, expedition_conditions, expedition_repeat_plan, mission_point_claims


class Verify(CustomAction):
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
        def read(relative):
            return vision.read((folder / relative).read_bytes())
        results = {}
        home = read("home.png")
        assert home.is_home(), "Maa 大厅识别失败"
        assert template_match(home, ROOT / "assets/event_entry.png", (0, 60, 300, 390), .88), "Maa 活动模板失败"
        results["home_and_event_template"] = True
        sweep = read("sweep_multi.png")
        results["sweep"] = sweep_budget(sweep, 42, 0)
        assert results["sweep"] == (6, 254, 2)
        split = read("sweep_single_split.png")
        results["split_single_sweep"] = sweep_budget(split, 42, 0)
        assert results["split_single_sweep"] == (1, 44, 2)
        arena = read("arena_budget.png")
        results["arena"] = arena_budget(arena, 15)
        assert results["arena"] == (9, 9)
        empty_arena = read("arena_empty.png")
        results["arena_empty_tickets"] = empty_arena.number_fraction((1080, 0, 1279, 85))
        assert results["arena_empty_tickets"] == (0, 15), "竞技场零票数识别失败"
        expedition = read("expedition.png")
        assert expedition_conditions(expedition), "Maa 远征页面或队伍条件识别失败"
        results["expedition"] = True
        claimable = read("expedition_claimable.png")
        assert claimable.find("点击领取", region=(1000, 540, 1225, 640), exact=True), "远征点击领取按钮识别失败"
        assert claimable.number_fraction((65, 70, 380, 140)) == (2, 2), "完成远征仍会占用两支队伍"
        assert expedition_conditions(claimable), "完成远征的已保存队伍条件识别失败"
        results["expedition_claimable_with_full_teams"] = True
        repeat = read("expedition_repeat.png")
        missions = ["新人驾驶员培养协助", "新人驾驶员培养协助-攻击"]
        assert expedition_repeat_plan(repeat, {"hours": 12, "missions": missions}) == missions, "远征两队再次派遣列表识别失败"
        results["expedition_repeat_two_12h_missions"] = True
        daily = read("mission_daily_points_available.png")
        assert len(mission_point_claims(daily, "每日任务")) == 1, "每日任务一键领取禁用时，仍应找到底部积分奖励"
        results["daily_points_independent_of_bulk_claim"] = True
        weekly = read("mission_weekly_points_claimed.png")
        assert mission_point_claims(weekly, "每周任务") == [], "每周已领取积分档位不应重领"
        monthly = read("mission_monthly_points_claimed.png")
        assert mission_point_claims(monthly, "每月任务") == [], "每月已领取积分档位不应重领"
        results["weekly_and_monthly_points_already_claimed"] = True
        confirmed = read("arena_settlement_confirm.png")
        button = confirmed.settlement_confirm()
        assert button is not None and button.text == "确认", "竞技场结算确认按钮识别失败"
        results["arena_settlement_confirm"] = True
        loading = read("arena_settlement_loading.png")
        assert loading.is_loading() and loading.has("扫荡结算") and loading.has("刷新"), "竞技场加载截图识别失败"
        results["arena_settlement_loading"] = True
        transition = read("black_transition.png")
        assert transition.is_loading(), "无文字的切页黑屏应等待，不应点击或立即报未知页面"
        results["black_transition_loading"] = True
        (ROOT / "runtime/replay-verification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False), flush=True)
        return True


class OfflineImages(CustomController):
    def connect(self):
        return True

    def request_uuid(self):
        return "Cangwu-offline-images"

    def get_features(self):
        return 0

    def screencap(self):
        return cv2.imdecode(np.frombuffer((ROOT / "tests/fixtures/home.png").read_bytes(), np.uint8), cv2.IMREAD_COLOR)

    def click(self, *args):
        raise AssertionError("离线验收禁止输入")


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    Toolkit.init_option(ROOT / "runtime/replay", {"stdout_level": 2})
    resource = Resource()
    resource.register_custom_action("Verify", Verify())
    Device.completed(resource.post_bundle(ROOT / "app/resource"), "加载资源")
    controller = OfflineImages()
    Device.completed(controller.post_connection(), "连接离线截图")
    tasker = Tasker()
    tasker.bind(resource, controller)
    job = tasker.post_task("VerifyReplays", {"VerifyReplays": {"recognition": "DirectHit", "action": {"type": "Custom", "param": {"custom_action": "Verify"}}}}).wait()
    return 0 if job.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
