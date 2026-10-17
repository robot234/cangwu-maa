from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

import cv2
import numpy as np


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).lower()


@dataclass(frozen=True)
class Word:
    text: str
    confidence: float
    box: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.box
        return ((x1 + x2) // 2, (y1 + y2) // 2)


@dataclass
class Frame:
    image: np.ndarray
    original_size: tuple[int, int]
    words: list[Word]
    png: bytes
    vision: object | None = None

    def find(self, *texts: str, region: tuple[int, int, int, int] | None = None,
             exact: bool = False, confidence: float = 0.73) -> Word | None:
        wanted = [normalized(text) for text in texts]
        for word in self.words:
            if word.confidence < confidence:
                continue
            x, y = word.center
            if region and not (region[0] <= x <= region[2] and region[1] <= y <= region[3]):
                continue
            observed = normalized(word.text)
            if any(observed == text if exact else text in observed for text in wanted):
                return word
        return None

    def has(self, *texts: str, **kwargs) -> bool:
        return self.find(*texts, **kwargs) is not None

    def all(self, *texts: str) -> bool:
        return all(self.has(text) for text in texts)

    def matching(self, text: str, region: tuple[int, int, int, int] | None = None) -> list[Word]:
        return [w for w in self.words if Frame(self.image, self.original_size, [w], self.png).has(text, region=region)]

    def is_home(self) -> bool:
        return self.all("指挥室", "驾驶员", "作战", "演习") and self.has("任务", region=(1130, 230, 1279, 390))

    def is_loading(self) -> bool:
        if self.has("LOADING", region=(400, 600, 850, 719)):
            return True
        # Some navigation transitions are black with only a corner spinner.
        # Wait for a readable page instead of interpreting that as unknown UI.
        if not any(word.confidence >= .73 for word in self.words):
            return float(np.mean(np.all(self.image[80:650, 70:1220] < 12, axis=2))) > .995
        return False

    def reward(self) -> bool:
        return self.has("获得资源", "获得道具", "获得奖励", "奖励获得", region=(390, 160, 900, 350))

    def settlement_confirm(self) -> Word | None:
        if self.is_loading() or not self.has("扫荡结算", region=(420, 35, 850, 130)):
            return None
        return self.find("确认", "确定", "刷新", region=(420, 560, 850, 670), exact=True)

    def exchange_list(self) -> bool:
        return (self.has("商城系统", region=(180, 0, 450, 90))
                and self.has("兑换商店", "兌換商店", region=(650, 70, 850, 190))
                and self.has("精英商店", "调度商店", "后勤商店", "联合商店", "大师商店", "深渊商店", "武道会商店",
                             region=(0, 180, 235, 719), exact=True)
                and not self.has("是否购买", region=(350, 140, 850, 225))
                and not self.reward())

    def paid_dialog(self) -> bool:
        region = (200, 140, 1130, 620)
        if self.has("购买体力", "体力购买", "购买演习", "充值", "支付", "是否消耗", region=region):
            return True
        # Item cards can show clipped reset timers containing "购买次数".
        # Only exempt the identified shop list, never a confirmation dialog.
        shop_list = self.exchange_list()
        gift_list = self.all("商城系统", "首页推荐", "通常礼包")
        if (shop_list or gift_list) and not self.has("是否购买", region=(350, 140, 850, 225)):
            return False
        return self.has("购买次数", region=region)

    def colored(self, word: Word, color: str = "orange") -> bool:
        x1, y1, x2, y2 = word.box
        crop = self.image[max(0, y1 - 10):min(720, y2 + 10), max(0, x1 - 18):min(1280, x2 + 18)]
        if not crop.size:
            return False
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        if color == "orange":
            mask = cv2.inRange(hsv, (5, 105, 130), (40, 255, 255))
        elif color == "blue":
            mask = cv2.inRange(hsv, (85, 70, 110), (135, 255, 255))
        else:
            raise ValueError(color)
        return float(np.mean(mask > 0)) > 0.15

    def number_fraction(self, region: tuple[int, int, int, int]) -> tuple[int, int] | None:
        for word in self.words:
            x, y = word.center
            if not (region[0] <= x <= region[2] and region[1] <= y <= region[3]) or word.confidence < .8:
                continue
            match = re.search(r"(\d+)\s*/\s*(\d+)", normalized(word.text))
            if match:
                return int(match[1]), int(match[2])
        return None


class Vision:
    def __init__(self, context):
        self.context = context

    def ocr(self, image: np.ndarray) -> list[Word]:
        detail = self.context.run_recognition("CangwuOCR", image)
        if detail is None:
            raise RuntimeError("Maa OCR 没有返回识别结果。")
        result = detail.raw_detail
        if isinstance(result, str):
            import json
            result = json.loads(result)
        words = []
        for item in result.get("all", []):
            x, y, width, height = item["box"]
            words.append(Word(str(item["text"]), float(item["score"]), (x, y, x + width, y + height)))
        return words

    def template(self, frame: Frame, path: Path, region: tuple[int, int, int, int], threshold: float):
        x1, y1, x2, y2 = region
        override = {"CangwuTemplate": {"recognition": {"type": "TemplateMatch", "param": {
            "template": path.name, "roi": [x1, y1, x2 - x1, y2 - y1], "threshold": threshold,
        }}}}
        detail = self.context.run_recognition("CangwuTemplate", frame.image, override)
        if detail is None or not detail.hit:
            return None
        x, y, width, height = detail.box
        return (x + width // 2, y + height // 2)

    def region_words(self, frame: Frame, region: tuple[int, int, int, int], *, scale: int = 3) -> list[Word]:
        x1, y1, x2, y2 = region
        crop = cv2.resize(frame.image[y1:y2, x1:x2], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        return [Word(w.text, w.confidence, (x1 + w.box[0] // scale, y1 + w.box[1] // scale,
                                          x1 + w.box[2] // scale, y1 + w.box[3] // scale))
                for w in self.ocr(crop)]

    def read(self, png: bytes) -> Frame:
        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("截图解码失败。")
        height, width = image.shape[:2]
        if abs(width / height - 16 / 9) > 0.06:
            raise RuntimeError(f"当前画面是 {width}×{height}，请将 MuMu 设为横屏 16:9 后再运行。")
        scaled = cv2.resize(image, (1280, 720), interpolation=cv2.INTER_AREA)
        frame = Frame(scaled, (width, height), self.ocr(scaled), png, self)
        modal = frame.has("开始代理", "开始扫荡", region=(430, 490, 830, 590))
        if modal or frame.has("作战演习", "限时活动", region=(180, 0, 460, 90)):
            # Dimmed resource icons can merge with counters. Re-read just the
            # digits at higher contrast instead of weakening confidence gates.
            regions = [(1123, 17, 1216, 64)]
            if modal:
                regions.append((510, 245, 655, 325))
            for region in regions:
                x1, y1, x2, y2 = region
                gray = cv2.cvtColor(scaled[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
                enhanced = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
                crop = cv2.resize(cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                for word in self.ocr(crop):
                    if not re.fullmatch(r"\d+(?:/\d+)?", normalized(word.text)) or word.confidence < .9:
                        continue
                    a, b, c, d = word.box
                    frame.words.insert(0, Word(word.text, word.confidence, (x1 + a // 3, y1 + b // 3, x1 + c // 3, y1 + d // 3)))
        return frame


def template_match(frame: Frame, path: Path, region: tuple[int, int, int, int], threshold: float = .88) -> tuple[int, int] | None:
    if frame.vision is not None:
        return frame.vision.template(frame, path, region, threshold)
    if not path.is_file():
        return None
    template = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
    x1, y1, x2, y2 = region
    gray = cv2.cvtColor(frame.image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    if template is None or gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1] or template.std() < 4:
        return None
    scores = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(scores)
    if score < threshold:
        return None
    return (x1 + location[0] + template.shape[1] // 2, y1 + location[1] + template.shape[0] // 2)
