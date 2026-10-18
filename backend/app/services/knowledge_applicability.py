"""知识页级适用性：行业墙之外，按章节实际用途区分服务；未知不冒充已确认。"""
import re

SERVICE_RULES = {
    "测试": r"测试|缺陷|用例|质量评估|实飞|飞行试验",
    "开发": r"开发|编码|程序设计|代码实现",
    "集成": r"集成|部署|联调|安装调试|迁移",
    "运维": r"运维|售后|故障处理|技术保障|维护",
    "咨询": r"咨询|可行性|评估咨询",
    "产品": r"产品交付|产品定制|产品选型",
}
PURPOSE_RULES = {
    "需求分析": r"需求分析|需求理解|需求调研",
    "设计开发": r"开发|编码|架构设计|详细设计",
    "测试验收": r"测试|验收|缺陷|用例|实飞|飞行试验",
    "集成部署": r"集成|部署|联调|迁移",
    "项目管理": r"进度|组织|人员|项目管理|资源配置|风险管理",
    "培训交付": r"培训|交付|移交",
    "运维保障": r"运维|售后|维护|故障处理|技术保障",
}


def _labels(text, rules):
    return [name for name, pattern in rules.items() if re.search(pattern, text or "")]


def classify(title: str, content: dict | None = None) -> dict:
    content = content or {}
    # 叶子优先，防“开发项目 > 测试验收”被整册体裁覆盖。
    leaf = (title or "").split(" > ")[-1]
    fallback = " ".join(str(content.get(k) or "") for k in ("category", "usage"))
    def choose(key, rules):
        explicit = content.get(key)
        if isinstance(explicit, list):
            known = list(dict.fromkeys(x for x in explicit if isinstance(x, str) and x in rules))
            if known:
                return known
        return _labels(leaf, rules) or _labels(fallback, rules)
    return {"service_types": choose("service_types", SERVICE_RULES),
            "chapter_purposes": choose("chapter_purposes", PURPOSE_RULES)}


def applicable(page: dict, target_title: str) -> bool:
    """只拒绝明确冲突的专门工作；公共章节、混合章节、未知资料仍交后续相关性判断。"""
    target = classify(target_title)
    source = classify(page.get("title", ""), page)
    wanted = set(target["service_types"])
    actual = set(source["service_types"])
    specialized = {"设计开发", "测试验收", "集成部署", "运维保障"}
    return not (wanted and actual and wanted.isdisjoint(actual)
                and specialized.intersection(target["chapter_purposes"])
                and specialized.intersection(source["chapter_purposes"]))
