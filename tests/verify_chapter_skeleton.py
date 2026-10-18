"""校验「知识库骨架」：同类标书里这一章实际用过哪些子节，喂给子节生成（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_chapter_skeleton.py

背景：目录的「节」此前只有两个来源——招标文件，和模型自由发挥。本项目招标的
「附件2 响应文件格式」是**平铺的九个章名、一个子节都没规定**（docx 段落 127-136），
于是子节全由模型现编，实测九个章无一与 `toc_hint` 相符。而知识库里躺着 1139 页
以「四、技术方案」开头的参考，路径形如
    「四、技术方案 > 第一章 工程化交付方案 > 第一节 问题处理机制的响应时间」
评审办法要的四份方案（工程化交付 10 分 / 测试验收准备 10 分 / 项目实施方案 5 分 /
售后服务与保障能力 8 分）**逐条都在里面**——但目录生成阶段压根拿不到知识页。

检查四件事：
  1. `matching.chapter_skeleton` 本身：只取紧邻的下一层、两套编号归并、计数降序、
     边界（章不存在 / 章在叶子上 / 空章名 / limit）；
  2. **真实语料**：lowalt 库里「四、技术方案」的骨架，必须含评审办法那四份方案；
  3. 骨架真的进了 prompt（把 llm 换成录音桩，查 user 消息）；
  4. **有骨架时不再照抄招标子节**——本项目 `toc_hint` 里的子节是抽取模型推的，
     照抄等于把一次推演固化成"招标规定"；有骨架就该把两者都交给模型。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import orchestrate_agent as OA                          # noqa: E402
from app.services import matching as M                                  # noqa: E402

ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def page(pid: int, title: str) -> dict:
    return {"id": pid, "title": title}


# ---------------------------------------------------------------- 1. 纯函数
def section_1() -> None:
    print("\n1) chapter_skeleton 的聚合规则")
    sk = M.chapter_skeleton

    pages = [
        page(1, "四、技术方案 > 第一章 工程化交付方案 > 第一节 问题处理机制的响应时间"),
        page(2, "四、技术方案 > 第一章 工程化交付方案 > 第二节 问题处理机制的流程"),
        page(3, "四、技术方案 > 2、工程化交付方案 > 2.1 交付批次规划"),
        page(4, "四、技术方案 > 第四章 项目售后服务与保障能力 > 第五节 故障处理时限"),
        page(5, "四、技术方案 > 第四章 项目售后服务与保障能力 > 第六节 巡检频次"),
        page(6, "四、技术方案 > 第四章 项目售后服务与保障能力 > 第七节 应急保障预案"),
        page(7, "四、技术方案 > 6、售后服务与保障方案 > （一）售后服务团队配置"),
        page(8, "四、技术方案 > 第四章 项目售后服务与保障能力 > 第一节 人员数量"),
    ]
    got = sk("四、技术方案", pages)
    # 页面 4/5/6/8 归「项目售后服务与保障能力」＝4 页；页面 1/2/3 归「工程化交付方案」
    # （两套编号：第一章 + 2、）＝3 页；页面 7 是**另一个名字**——「售后服务与保障方案」
    # 和「项目售后服务与保障能力」互不包含，normalize 不归并，分开计数是对的。
    # （两队页数得拉开：并列时 sorted() 是稳定的，次序会变成出现次序，测出来不好读。）
    check("两套编号归并成一节，页数相加、降序", got, [
        {"name": "项目售后服务与保障能力", "pages": 4},
        {"name": "工程化交付方案", "pages": 3},
        {"name": "售后服务与保障方案", "pages": 1},
    ])

    # 只取紧邻的下一层：孙辈（第一节/2.1/第七节）一个都不该出现
    check("不把孙辈当子节", [g["name"] for g in got],
          ["项目售后服务与保障能力", "工程化交付方案", "售后服务与保障方案"])

    check("章在叶子上（后面没段了）→ 不计", sk("四、技术方案", [page(9, "四、技术方案")]), [])
    check("章不存在 → 空", sk("九、其他资料", pages), [])
    check("空章名 → 空", sk("", pages), [])
    check("空页表 → 空", sk("四、技术方案", []), [])

    # 同名段出现两次只取最近那一次（避免一个标题里重复出现章名时双重计数）
    dup = [page(10, "四、技术方案 > 第一章 工程化交付方案 > 四、技术方案 > 第一节 甲")]
    check("同名段重复只算最近一层", sk("四、技术方案", dup), [{"name": "工程化交付方案", "pages": 1}])

    check("limit 生效", len(sk("四、技术方案", pages, limit=1)), 1)

    # 序号前缀（第一章/2、/（一））都要能被剥掉后归并
    mixed = [page(11, "四、技术方案 > 第一章 工程化交付方案"),
             page(12, "四、技术方案 > 2、工程化交付方案"),
             page(13, "四、技术方案 > （三）工程化交付方案")]
    check("三种写法归并成一节", sk("四、技术方案", mixed), [{"name": "工程化交付方案", "pages": 3}])


# ---------------------------------------------------------------- 2. 真实语料
def section_2() -> bool:
    print("\n2) 真实语料：lowalt 库「四、技术方案」的骨架")
    try:
        pages = M.load_page_summaries("lowalt")
    except Exception as e:  # noqa: BLE001 没库就跳过
        print(f"  [--] 连不上库，跳过：{type(e).__name__}: {e}")
        return False
    if not pages:
        print("  [--] 库里没有知识页，跳过")
        return False

    sk = M.chapter_skeleton("四、技术方案", pages)
    names = [s["name"] for s in sk]
    print(f"     骨架 {len(sk)} 项（页数降序）：")
    for s in sk:
        print(f"       {s['pages']:>5} 页   {s['name']}")

    # 评审办法（docx 段落 257-278）要技术方案必须含这四份方案
    for want in ("工程化交付方案", "测试验收准备方案", "项目实施方案", "项目售后服务与保障能力"):
        check(f"骨架含「{want}」", want in names, True)
    check("页数降序", [s["pages"] for s in sk] == sorted((s["pages"] for s in sk), reverse=True), True)
    check("别的章没有骨架（不是万能的）", M.chapter_skeleton("五、公司简介", pages), [])
    return True


# ---------------------------------------------------------------- 3 & 4. 进不进 prompt
class _Recorder:
    """录音桩：记下 prompt，返回一份合法 JSON。"""
    def __init__(self):
        self.calls: list[list[dict]] = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return '{"children": [{"title": "第一节 甲"}]}'

    @property
    def user(self) -> str:
        return self.calls[-1][1]["content"]


TOC = {"chapters": [{"title": "四、技术方案",
                     "children": [{"title": "第一节 招标列的大项"}]}]}


def section_3_4() -> None:
    print("\n3) 骨架真的进了 prompt")
    rec = _Recorder()
    real = OA.llm
    OA.llm = rec
    try:
        OA.generate_toc_children(
            "四、技术方案", "项目信息", ["无人机"], "评分办法",
            tender_toc=TOC, skeleton=[{"name": "工程化交付方案", "pages": 240},
                                      {"name": "项目售后服务与保障能力", "pages": 408}])
        u = rec.user
        check("骨架名字进了 user", "工程化交付方案" in u and "项目售后服务与保障能力" in u, True)
        check("骨架页数进了 user", "408" in u and "240" in u, True)
        check("招标大项也摆进去了（两条线并列）", "第一节 招标列的大项" in u, True)
        check("有骨架时确实调了模型（没走照抄）", len(rec.calls), 1)

        print("\n4) 没骨架时仍然照抄招标子节（这条不能退化）")
        rec2 = _Recorder()
        OA.llm = rec2
        got = OA.generate_toc_children("四、技术方案", "项目信息", [], "",
                                       tender_toc=TOC, skeleton=[])
        check("照抄，没调模型", (got, len(rec2.calls)),
              ([{"title": "第一节 招标列的大项"}], 0))

        print("\n   没招标子节、有骨架 → 走模型")
        rec3 = _Recorder()
        OA.llm = rec3
        OA.generate_toc_children("四、技术方案", "项目信息", [], "",
                                 tender_toc=None, skeleton=[{"name": "工程化交付方案", "pages": 240}])
        check("调了模型且 prompt 含骨架", (len(rec3.calls), "工程化交付方案" in rec3.user), (1, True))
    finally:
        OA.llm = real


def main() -> int:
    section_1()
    section_2()
    section_3_4()
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
