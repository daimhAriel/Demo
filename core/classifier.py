"""
轻量关键词分类器（方案A）— 无需 LLM
"""
import re

# (category, keyword, use_regex)
# use_regex=True 时用 re.search，否则 substring match
CATEGORY_RULES = {
    "股权与资本": [
        (3, "融资"), (3, "募资"), (3, "投资"), (3, "股权"), (3, "上市"),
        (3, "财报"), (3, "资本"), (3, "融资轮"), (3, "pre-a"), (3, "a轮"),
        (3, "b轮"), (3, "c轮"), (3, "funding"), (3, "investment"),
        (3, "investor"), (3, "capital"), (3, "financing"), (3, "series a"),
        (3, "series b"), (3, "series c"), (3, "ipo"), (3, "equity"),
        (3, "valuation"), (3, "raised"), (3, "revenue"), (3, "earnings"),
        (3, "million"), (3, "billion"), (3, "美元"), (3, "亿"), (3, "万"),
        (3, "secures"), (3, "secured"), (3, "raise"), (3, "raises"),
        (1, "quarter"), (3, "financial result"), (1, "allots"),
    ],
    "战略目标": [
        (3, "愿景"), (3, "战略"), (3, "目标"), (3, "规划"), (3, "路线图"),
        (3, "roadmap"), (3, "mission"), (3, "vision"), (3, "strategy"),
        (3, "goal"), (1, "plan"), (3, "milestone"), (3, "ambition"),
        (3, "expand"), (3, "进军"), (3, "布局"), (3, "big plan"),
        (3, "outlook"),
    ],
    "产品与工程": [
        (3, "原型机"), (3, "首飞"), (3, "试飞"), (3, "航程"), (3, "载重"),
        (3, "速度"), (3, "动力"), (3, "电池"), (3, "发动机"), (3, "电机"),
        (3, "复合材料"), (3, "prototype"), (3, "first flight"),
        (3, "test flight"), (3, "flight test"), (3, "range"), (3, "payload"),
        (3, "speed"), (3, "battery"), (3, "engine"), (3, "motor"),
        (3, "composite"), (3, "wing"), (3, "propeller"), (3, "rotor"),
        (3, "验证机"), (3, "技术验证"), (3, "设计"), (3, "性能"), (3, "参数"),
        (3, "development"), (3, "develop"), (3, "engineering"), (3, "engineer"),
        (3, "cargo drone"), (3, "unmanned"), (3, "autonomous"),
        (3, "飞越"), (3, "内饰"), (3, "座舱"), (3, "航空器"),
        (3, "testing"), (3, "trial"),
        (3, "海空一体"), (3, "零碳"),
        (1, "flight"), (1, "test"), (1, "cross.country"),
        (1, "cross-country"),
        # 正则模式
        (3, "完成.*飞行", True),
        (3, "完成.*首飞", True),
        (3, "刷新.*记录", True),
        (3, "全球首款", True),
        (1, "安全", True),
    ],
    "适航情况": [
        (3, "适航"), (3, "认证"), (3, "型号"), (3, "取证"), (3, "tc"), (3, "stc"),
        (3, "type certificate"), (3, "certification"),
        (3, "airworthiness"), (3, "regulatory"), (3, "approval"),
        (3, "approved"), (3, "certificate"), (3, "compliance"),
        (3, "民航局"), (3, "caac"), (3, "审定"), (3, "合格证"),
        (3, "regulator"), (3, "regulate"),
        (3, "符合性"), (1, "验证"), (3, "符合性验证"),
        (3, "局方"), (3, "faa"), (3, "easa"),
        (3, "特许飞行"), (3, "特许"),
        # 正则
        (3, "验证.*试验", True),
    ],
    "商业化情况": [
        (3, "订单"), (3, "采购"), (3, "签约"), (3, "合作"), (3, "交付"),
        (3, "运营"), (3, "商业化"), (3, "客户"), (3, "用户"),
        (3, "order"), (3, "purchase"), (3, "contract"), (3, "delivery"),
        (3, "commercial"), (3, "customer"), (3, "partner"), (3, "partnership"),
        (3, "航线"), (3, "空中出租车"), (3, "air taxi"),
        (3, "服务"), (3, "预定"), (3, "预订"), (3, "book"),
        (3, "operation"), (3, "operate"), (1, "fly"), (1, "flight"),
        (3, "pilot program"), (3, "demonstration"), (3, "demo"),
        (3, "亮相"), (3, "发布"), (3, "推出"), (3, "入选"), (3, "启用"),
        (3, "参与"), (3, "展示"), (3, "体验"), (3, "showcase"),
        (3, "进驻"), (3, "入驻"), (3, "落地"), (3, "开通"),
        (3, "lease"), (3, "rent"), (3, "airline"), (3, "airport"),
        (3, "launch"), (1, "begin"), (1, "start"),
        (3, "lands at"), (3, "debut"), (3, "unveil"), (3, "deal"),
        (3, "deliver"), (3, "aircraft"), (3, "passenger"),
        (3, "emergency response"), (3, "应急"),
        (3, "调研交流"), (3, "到访"), (3, "考察交流"), (3, "莅临"),
        # 正则
        (3, "展$", True),  # 结尾的"展"字
        (1, "takes?.+flight", True),
        (3, "bring.*to", True),
    ],
    "产能规划": [
        (3, "产能"), (3, "工厂"), (3, "量产"), (3, "产线"), (3, "供应链"),
        (3, "生产"), (3, "制造"), (3, "assembly"), (3, "production"),
        (3, "manufacturing"), (3, "facility"), (3, "plant"),
        (3, "supply chain"), (3, "mass production"), (3, "生产线"),
        (3, "hub"), (3, "base"),
        (3, "动工"), (3, "基地"), (3, "总装"), (3, "厂房"),
        (3, "expansion"), (3, "expand"), (3, "build"),
        (3, "charging"), (3, "charger"), (3, "充电"),
        (1, "network"),
    ],
}


def classify(title: str, body: str = "") -> str:
    """
    基于关键词匹配分类。
    策略：
    1. 标题/正文匹配关键词，累加权重分
    2. 取总分最高的类别
    3. 平局时按 PRIORITY_OVERRIDE 规则裁决
    4. 0命中 → "其他"
    """
    text_lower = (title + " " + body).lower()
    title_lower = title.lower()

    scores = {}
    for cat, rules in CATEGORY_RULES.items():
        score = 0
        for rule in rules:
            weight = rule[0]
            kw = rule[1]
            use_regex = rule[2] if len(rule) > 2 else False

            if use_regex:
                # 正则匹配（标题权重×3，全文×1）
                if re.search(kw, title_lower):
                    score += weight * 3
                elif re.search(kw, text_lower):
                    score += weight
            else:
                # 子串匹配
                kw_lower = kw.lower()
                if kw_lower in title_lower:
                    score += weight * 3
                elif kw_lower in text_lower:
                    score += weight

        if score > 0:
            scores[cat] = score

    if not scores:
        return "其他"

    max_score = max(scores.values())
    top_cats = [c for c, s in scores.items() if s == max_score]

    return top_cats[0] if top_cats else "其他"
