"""当前部署只编制指定章节；范围按标题识别，不依赖章节的数组位置。"""
from app.core.chapter_title import normalize


def select_roots(nodes: list[dict], title: str, member_titles=()) -> list[dict]:
    """保留指定根章及其完整子树，不修改原树、不删除已有正文。"""
    if not title:
        return nodes
    exact = [n for n in nodes if normalize(n.get("title")) == normalize(title)]
    if exact:
        return exact
    # 旧项目可能把本册内部各章平铺为顶层；只接纳有本项目招标要求佐证的章。
    members = {normalize(t) for t in member_titles if t}
    return [n for n in nodes if normalize(n.get("title")) in members]


def node_ids(nodes: list[dict]) -> set[int]:
    ids = set()
    for n in nodes:
        if n.get("id") is not None:
            ids.add(int(n["id"]))
        ids.update(node_ids(n.get("children") or []))
    return ids
