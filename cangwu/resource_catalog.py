"""CN resource dungeon names; stage identity is checked again on screen."""

RESOURCE_DUNGEONS = {
    "resource_beast": {
        "label": "异兽清剿", "aliases": ("异兽清剿", "異兽清剿", "异兽清缴"),
        "entry": "CangwuResourceBeast", "region": (165, 460, 375, 570),
        "family": ("异兽清剿", "異兽清剿", "盾币回收", "幻化星碎"), "max_stage": 10,
        "choices": {
            "experience": {"label": "经验获取", "title": "异兽清剿", "prefix": "异兽清剿"},
            "shield": {"label": "盾币获取", "title": "盾币回收", "prefix": "盾币回收"},
            "star": {"label": "幻化星碎", "title": "幻化星碎", "prefix": "星碎开采"},
        },
    },
    "resource_core": {
        "label": "核心制造", "aliases": ("核心制造",), "entry": "CangwuResourceCore",
        "region": (420, 460, 630, 570), "family": ("核心",), "max_stage": 5,
        "choices": {
            "life": {"label": "生命核心", "title": "生命", "prefix": "生命源能"},
            "attack": {"label": "攻击核心", "title": "攻击", "prefix": "攻击源能"},
            "armor": {"label": "装甲核心", "title": "装甲", "prefix": "装甲源能"},
            "critical": {"label": "暴击核心", "title": "暴击", "prefix": "暴击源能"},
            "burst": {"label": "爆发核心", "title": "爆发", "prefix": "爆发源能"},
            "mobility": {"label": "机动核心", "title": "机动", "prefix": "机动源能"},
        },
    },
    "resource_training": {
        "label": "队员特训", "aliases": ("队员特训",), "entry": "CangwuResourceTraining",
        "region": (670, 460, 880, 570), "family": ("觉醒",), "max_stage": 10,
        "choices": {
            "defense": {"label": "防卫觉醒", "title": "防卫", "prefix": "防卫测试"},
            "attack": {"label": "攻击觉醒", "title": "攻击", "prefix": "攻击测试"},
            "firepower": {"label": "火力觉醒", "title": "火力", "prefix": "火力测试"},
            "support": {"label": "支援觉醒", "title": "支援", "prefix": "支援测试"},
        },
    },
}
