"""Public teaching taxonomy. Subjects are independent of renderer families."""

SUBJECTS = {
    "mathematics": ("数学", "Mathematics"),
    "physics": ("物理", "Physics"),
    "chemistry": ("化学", "Chemistry"),
    "statistics": ("统计", "Statistics"),
    "biology": ("生物", "Biology"),
    "geography": ("地理", "Geography"),
    "systems": ("通用与信息技术", "General & computing"),
    "language": ("语言与写作", "Language & writing"),
    "history": ("历史与社会", "History & society"),
    "economics": ("经济与管理", "Economics & management"),
    "engineering": ("工程技术", "Engineering"),
    "astronomy": ("天文与空间", "Astronomy & space"),
    "music": ("音乐", "Music"),
    "visual_art": ("美术与设计", "Art & design"),
    "sports": ("体育与运动", "Sport & movement"),
    "agriculture": ("农业与劳动", "Agriculture & practical studies"),
    "environment": ("环境科学", "Environmental science"),
}
LEVELS = {
    "elementary": ("小学", "Primary"),
    "junior": ("初中", "Lower secondary"),
    "senior": ("高中", "Upper secondary"),
    "undergraduate": ("大学基础", "Undergraduate"),
}
KINDS = {
    "symbol": ("符号", "Symbol"),
    "object": ("物体与仪器", "Object & apparatus"),
    "chart": ("数据图", "Data chart"),
    "scene": ("完整构图", "Composition"),
    "sequence": ("步骤与过程", "Sequence"),
}
FAMILIES = {
    "geometry": ("平面与立体几何", "Geometry"),
    "function": ("函数与坐标", "Functions"),
    "chart": ("统计图表", "Statistical charts"),
    "vessel": ("容器与玻璃仪器", "Vessels"),
    "apparatus": ("实验配件", "Laboratory accessories"),
    "mechanics": ("力学物体", "Mechanics"),
    "circuit": ("电路元件", "Circuit components"),
    "optics": ("光学", "Optics"),
    "measurement": ("测量仪器", "Measurements"),
    "waves": ("波动与流体", "Waves & fluids"),
    "biology": ("生物结构与实验", "Biology"),
    "earth": ("地理与地球", "Earth science"),
    "chemistry": ("化学结构", "Chemical structures"),
    "graph": ("关系与流程", "Relations"),
    "logic": ("逻辑电路", "Logic"),
    "objects": ("生活与工程物体", "Everyday objects"),
    "template": ("完整构图", "Complete compositions"),
}
for _key, _label in SUBJECTS.items():
    FAMILIES[f"{_key}_extended"] = (_label[0] + "专题", _label[1] + " studies")


def public_taxonomy():
    result = {
        key: [
            {"id": name, "zh": labels[0], "en": labels[1]}
            for name, labels in rows.items()
        ]
        for key, rows in [
            ("subjects", SUBJECTS),
            ("education_levels", LEVELS),
            ("asset_kinds", KINDS),
            ("families", FAMILIES),
        ]
    }
    result["subject_groups"] = [
        {
            "id": "science",
            "zh": "数学与自然科学",
            "en": "Mathematics & science",
            "subjects": [
                "mathematics",
                "statistics",
                "physics",
                "chemistry",
                "biology",
                "geography",
                "astronomy",
            ],
        },
        {
            "id": "humanities",
            "zh": "语言与社会科学",
            "en": "Language & social science",
            "subjects": ["language", "history", "economics"],
        },
        {
            "id": "technology",
            "zh": "技术与实践",
            "en": "Technology & practical studies",
            "subjects": ["systems", "engineering", "agriculture", "environment"],
        },
        {
            "id": "arts",
            "zh": "艺术与体育",
            "en": "Arts & sport",
            "subjects": ["music", "visual_art", "sports"],
        },
    ]
    return result


def resolve_level(grade: str) -> str:
    text = str(grade or "").lower()
    for key, words in {
        "elementary": ["小学", "primary", "elementary"],
        "junior": ["初中", "junior", "lower secondary"],
        "senior": ["高中", "senior", "upper secondary"],
        "undergraduate": ["大学", "本科", "undergraduate", "college"],
    }.items():
        if any(word in text for word in words):
            return key
    return ""
