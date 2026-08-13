from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

import yaml


SERVER_ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIRECTORY = SERVER_ROOT / "content" / "museum"
OUTPUT_PACKAGE = CONTENT_DIRECTORY / "china-national-silk-museum-stage3-catalog.json"
OUTPUT_EVALUATION = (
    SERVER_ROOT / "tests" / "fixtures" / "museum_stage3_100_eval.json"
)
BASELINE_EVALUATION = (
    SERVER_ROOT / "tests" / "fixtures" / "museum_conversation_eval.json"
)
ACCESSED_AT = "2026-08-12"


CATALOG_ITEMS = (
    ("31590", "晚清民国雪青缎地彩绣喜鹊登梅纹暖耳"),
    ("31589", "晚清民国蓝缎地彩绣松鼠葡萄纹暖耳"),
    ("31588", "晚清民国蓝缎地彩绣鹤鹿同春纹暖耳"),
    ("31587", "晚清民国蓝缎地彩绣童子莲鱼纹暖耳"),
    ("31586", "晚清黑缎地平绣唐诗暖耳"),
    ("30674", "民国苎麻女衫裙"),
    ("30672", "竹制扇套"),
    ("30671", "羊皮女袄"),
    ("30568", "紫缎钉几何纹花边眉勒"),
    ("30567", "白底花卉纹花边"),
    ("30566", "几何蝴蝶纹花边"),
    ("30560", "“双兔”牌手帕商标"),
    ("30559", "上海寰球手帕厂广告"),
    ("3316", "民国纸制月份广告画"),
    ("3319", "石刀"),
    ("3313", "民国纸制月份广告画"),
    ("3321", "绣线"),
    ("3320", "铜烫斗"),
    ("3317", "帐钩"),
    ("3314", "铜烫斗"),
    ("3322", "梭子"),
    ("3312", "天山牌二十一支棉纱包装纸"),
    ("3311", "民国大和生丝厂包装纸"),
    ("3318", "民国纸制月份广告画"),
    ("3307", "民国恒丰纺织股份有限公司股票（第008907号）"),
    ("3308", "民国烟囱熨斗"),
    ("3310", "“浙杭瑞新织绸公司”织款"),
    ("3304", "“浙杭振新织绸公司”织款"),
    ("3303", "陶纺轮"),
    ("3315", "座垫"),
    ("3305", "辑里缫丝车"),
    ("3299", "石元宝"),
    ("3309", "旧碗"),
    ("3306", "榻柜"),
    ("3296", "旧烛台"),
    ("3297", '"绸联处"牌'),
    ("3301", "丁桥织机"),
    ("3295", "土丝"),
    ("3294", "丝线"),
    ("3293", "碗"),
    ("3292", "碗"),
    ("3289", "碗"),
    ("3288", "碗"),
    ("3287", "碗"),
    ("3291", "碗"),
    ("3290", "碗"),
    ("3298", "丝线"),
    ("3300", "丝绵胎"),
    ("3302", '"开元通宝"铜钱'),
    ("3286", '"五铢"铜钱'),
    ("3285", "当代海宁蚕花戏“马鸣王菩萨”牛皮道具"),
    ("3282", "盘金彩绣狮子纹荷包"),
    ("3281", "黄绸地打籽绣多子多福荷包"),
    ("3280", "蓝缎圈金铺绒绣石榴飞雁腰包"),
    ("3279", "白绸绣桃子围棋腰包"),
    ("3284", "红绿缎绣花卉小饰件"),
    ("3283", "品蓝缎彩绣花卉名片袋"),
    ("3274", "白缎铺绒绣桃子螃蟹褡裢"),
    ("3275", "彩绸绣石榴花虫荷包"),
    ("3272", "彩绢圈金绣梅花钱袋"),
    ("3273", "白绸地彩绣公鸡花卉钱袋"),
    ("3270", "蓝缎圈金铺绒绣葫芦桃子褡裢"),
    ("3269", "绿缎彩绣花卉钱袋"),
    ("3271", "粉缎圈金绣螃蟹荷包"),
    ("3266", "红缎地彩绣花蝶荷包"),
    ("3278", "月白绸彩绣花蝶碗形钱袋"),
    ("3265", "红绸彩绣花蝶钱袋"),
    ("3264", "红缎地彩绣多子多福荷包"),
    ("3267", "五彩缎彩绣花蝶钱袋"),
    ("3276", "紫色缎地彩绣花蝶钱袋"),
    ("3268", "白缎彩绣花卉钱袋"),
    ("3262", "蓝缎彩绣花卉荷包"),
    ("3259", "红布彩绣莲藕飞蝶钱袋"),
    ("3258", "白色布地彩绣桃花钱袋"),
    ("3257", "堆绒绣花卉碗形钱袋"),
    ("3277", "红缎彩绣花蝶荷包"),
    ("3260", "白绢彩绣花蝶钱袋"),
    ("3253", "白缎彩绣小花钱袋"),
    ("3255", "湖绿绢彩绣花卉钱袋"),
    ("3254", "宝蓝缎铺绒绣花卉荷包"),
    ("3250", "堆绫绣花卉钱袋"),
    ("3252", "白缎彩绣桃花碗形钱袋"),
    ("3249", "红缎地彩绣葫芦莲花钱袋"),
)


QUESTION_BY_FACT_TYPE = {
    "appearance": "{name}的外形是什么样？",
    "craft": "{name}采用了什么工艺？",
    "dimensions": "{name}的尺寸多大？",
    "era": "{name}是什么年代的？",
    "excavation": "{name}在哪里出土？",
    "history": "介绍一下{name}",
    "material": "{name}是什么材质？",
    "research_limit": "关于{name}有哪些资料边界？",
    "usage": "{name}有什么用途？",
}


def build_content_package() -> dict[str, Any]:
    name_counts = Counter(name for _item_id, name in CATALOG_ITEMS)
    sources = []
    exhibits = []
    for item_id, official_name in CATALOG_ITEMS:
        source_id = f"source-china-silk-catalog-{item_id}"
        exhibit_id = f"china-silk-catalog-{item_id}"
        fact_id = f"fact-china-silk-catalog-{item_id}-listing"
        duplicate_name = name_counts[official_name] > 1
        canonical_name = (
            f"{official_name}（馆方条目{item_id}）"
            if duplicate_name
            else official_name
        )
        aliases = []
        if duplicate_name:
            aliases.append(
                {
                    "text": official_name,
                    "kind": "common",
                    "binding": "ambiguous",
                    "sources": [source_id],
                }
            )
        sources.append(
            {
                "id": source_id,
                "title": f"中国丝绸博物馆中国历代藏品：{official_name}",
                "source_type": "official_museum_webpage",
                "locator": (
                    "https://www.chinasilkmuseum.com/zggd/"
                    f"info_21.aspx?itemid={item_id}"
                ),
                "rights_note": "仅保存馆方公开名称与来源定位，不保存页面图片。",
                "publisher": "中国丝绸博物馆",
                "accessed_at": ACCESSED_AT,
                "language": "zh-CN",
            }
        )
        exhibits.append(
            {
                "id": exhibit_id,
                "zone_id": "china-silk-official-collection",
                "name": canonical_name,
                "aliases": aliases,
                "status": "active",
                "revision": {
                    "id": f"{exhibit_id}-r1",
                    "number": 1,
                    "status": "draft",
                    "facts": [
                        {
                            "id": fact_id,
                            "type": "history",
                            "statement": (
                                "中国丝绸博物馆“中国历代”藏品库以"
                                f"“{official_name}”为该公开条目的展示名称。"
                            ),
                            "keywords": [
                                official_name,
                                "中国丝绸博物馆",
                                "中国历代",
                                "馆方收录",
                                f"itemid {item_id}",
                            ],
                            "confidence": "official_museum_webpage",
                            "certainty": "confirmed",
                            "sources": [source_id],
                        }
                    ],
                },
            }
        )
    return {
        "schema_version": 2,
        "museum": {
            "id": "china-national-silk-museum",
            "name": "中国丝绸博物馆",
            "status": "active",
        },
        "zones": [
            {
                "id": "china-silk-official-collection",
                "name": "馆藏精品",
                "sort_order": 1,
            }
        ],
        "sources": sources,
        "exhibits": exhibits,
    }


def build_evaluation_fixture(package_paths: tuple[Path, ...]) -> dict[str, Any]:
    cases = []
    content_packages = []
    for package_path in package_paths:
        payload = _load_package(package_path)
        content_packages.append(
            package_path.relative_to(SERVER_ROOT).as_posix()
        )
        for exhibit in payload["exhibits"]:
            first_fact = exhibit["revision"]["facts"][0]
            question_template = QUESTION_BY_FACT_TYPE[first_fact["type"]]
            exhibit_id = str(exhibit["id"])
            cases.append(
                {
                    "id": f"stage3-{exhibit_id}",
                    "category": "stage3_catalog_coverage",
                    "turns": [
                        {
                            "text": question_template.format(name=exhibit["name"]),
                            "metrics": [
                                "canonical_resolution",
                                "grounded_boundary",
                                "audit_reproducibility",
                            ],
                            "expected": {
                                "knowledge_status": "grounded",
                                "resolution_status": "explicit",
                                "context_exhibit_id": exhibit_id,
                                "required_fact_ids": [first_fact["id"]],
                            },
                        },
                        {
                            "text": "这件展品现在值多少钱？",
                            "metrics": [
                                "unsupported_no_hallucination",
                                "audit_reproducibility",
                            ],
                            "expected": {
                                "knowledge_status": "unsupported",
                                "context_exhibit_id": exhibit_id,
                                "fact_ids": [],
                            },
                        },
                    ],
                }
            )
    cases.append(
        {
            "id": "stage3-ambiguous-shared-name",
            "category": "ambiguous_alias",
            "turns": [
                {
                    "text": "介绍一下碗",
                    "metrics": ["ambiguous_no_binding", "audit_reproducibility"],
                    "expected": {
                        "knowledge_status": "missing_context",
                        "resolution_status": "ambiguous",
                        "context_exhibit_id": "",
                        "fact_ids": [],
                    },
                }
            ],
        }
    )
    cases.append(
        {
            "id": "stage3-unlisted-exhibit",
            "category": "unlisted_exhibit",
            "turns": [
                {
                    "text": "介绍一下不存在的测试展品",
                    "metrics": ["unlisted_no_inherit", "audit_reproducibility"],
                    "expected": {
                        "knowledge_status": "missing_context",
                        "resolution_status": "not_found",
                        "context_exhibit_id": "",
                        "fact_ids": [],
                    },
                }
            ],
        }
    )
    baseline = json.loads(BASELINE_EVALUATION.read_text(encoding="utf-8"))
    for metric in ("alias_resolution", "asr_alias_resolution"):
        source_case = next(
            case
            for case in baseline["cases"]
            if any(
                metric in turn.get("metrics", ())
                for turn in case["turns"]
            )
        )
        copied_case = deepcopy(source_case)
        copied_case["id"] = f"stage3-{copied_case['id']}"
        cases.append(copied_case)
    return {
        "version": 2,
        "name": "museum-stage3-100-exhibit-evaluation",
        "content_packages": content_packages,
        "thresholds": dict(baseline["thresholds"]),
        "cases": cases,
    }


def _load_package(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid content package: {path}")
    return payload


def main() -> int:
    if len(CATALOG_ITEMS) != 83:
        raise RuntimeError(f"expected 83 catalog items, got {len(CATALOG_ITEMS)}")
    package = build_content_package()
    OUTPUT_PACKAGE.write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    package_paths = tuple(
        sorted(
            path
            for path in CONTENT_DIRECTORY.iterdir()
            if path.suffix.lower() in {".yaml", ".yml", ".json"}
        )
    )
    fixture = build_evaluation_fixture(package_paths)
    OUTPUT_EVALUATION.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "content_package": str(OUTPUT_PACKAGE),
                "new_exhibits": len(package["exhibits"]),
                "new_facts": sum(
                    len(exhibit["revision"]["facts"])
                    for exhibit in package["exhibits"]
                ),
                "evaluation_fixture": str(OUTPUT_EVALUATION),
                "evaluation_exhibits": len(
                    {
                        str(turn.get("expected", {}).get("context_exhibit_id", ""))
                        for case in fixture["cases"]
                        for turn in case["turns"]
                        if turn.get("expected", {}).get("context_exhibit_id")
                    }
                ),
                "evaluation_cases": len(fixture["cases"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
