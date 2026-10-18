"""校验「招标文件类别兜底判据」：文件名猜错时，正文抬头能不能把它捞回来（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_file_category.py

背景：文件类别原先只靠**文件名**猜（前端 `WorkspaceView.guessCategory`），猜成 other 就
招标抽取（标段 / 招标要求 / 本册目录）整条不跑，而且静默——进度文字里连「招标要求 N 条」
那一行都不出现；类别在界面上又是只读的，**已解析成功的文件连 ↻ 重新解析的按钮都没有**，
唯一的路子是本地改名重传或手工改库。现改为在 `fact_service.process_file` 里按**正文抬头**
兜底（详见该处 `TENDER_SELF_NAMES` 的注释）。

这个判据是启发式的，所以它需要看门人——而且**误判比漏判危险得多**：把一份投标文件判成
招标文件，会把它的目录（「一、响应函…九、其他资料」整本响应文件）写进 `toc_hint`，
正是"扔进去一份标书、冒出一整本标书"那个最要防的老问题。所以下面负样本比正样本多。

检查：
  1. 抬头自称判据的构造用例（纯文本，不依赖真实文件）；
  2. **真实语料**：项目 4 那份《…采购文件》判为是、同一项目的参考标书判为否。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.fact_service import _looks_like_tender                      # noqa: E402

ok = True

TENDER = ROOT / "uploads" / "projects" / "4" / "202610" / "7acc03af_实飞测试验证外协采购文件(1).docx"
BID = ROOT / "uploads" / "knowledge" / "202610" / "33ce5675_实飞测试验证外协项目投标文件(正本).docx"


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}" + ("" if good else f": {got!r} != 期望 {want!r}"))


# ---------------------------------------------------------------- 1. 构造用例
def section_1() -> None:
    print("\n1) 抬头自称判据（构造用例）")
    yes = [
        ("采购文件封面", "公开\n航天时代低空科技有限公司\n实飞测试验证外协采购文件\n2026年9月"),
        ("只传附件3", "附件3  评审办法\n本项目采用综合评分法。"),
        # PDF/MinerU 出来的封面会把字拆开，判据先合并空白
        ("拆字封面", "响 应 文 件\n实 飞 测 试 验 证 外 协 采 购 文 件"),
        ("磋商/询价这一类的抬头", "某某项目竞争性磋商文件\n第一章 磋商邀请"),
    ]
    for label, text in yes:
        check(f"判为招标：{label}", bool(_looks_like_tender(text)), True)

    no = [
        ("抬头的确不是招标", "关于本项目答疑如下：评审办法第3条分值调整，采购人已确认。"),
        # 「采购文件」是有的，但带句读 → 那是正文里的引述，不是抬头
        ("引述不算抬头",
         "## 一、响应函\n致：航天时代低空科技有限公司\n"
         "我们收到你们 实飞测试验证外协 采购文件，经详细研究，我们决定参加该项采购活动。"),
        ("投标文件抬头", "（实飞测试验证外协）\n响 应 文 件（正本）\n响应人：某某科技有限公司（盖单位章）"),
        # 刻意不收「响应文件格式」：它抽出来的本册目录是平铺的九个章名＝整本标书
        ("只传附件2", "附件2  响应文件格式\n一、响应函\n二、响应报价表\n三、响应情况对照表"),
        ("空文本", ""),
        # 抬头之后才出现自称词 → 超出发言区，不该判
        ("自称词在正文深处", "某某项目技术规格书\n" + "本页为技术参数说明。\n" * 400
                             + "详见本项目采购文件。"),
    ]
    for label, text in no:
        check(f"不判为招标：{label}", bool(_looks_like_tender(text)), False)


# ---------------------------------------------------------------- 2. 真实语料
def section_2() -> None:
    print("\n2) 真实语料：同一项目的两份文件，一份要认出来、一份要挡住")
    from app.agents.parser_agent import parse_document

    out = ROOT / "uploads" / "parsed" / "_verify_file_category"

    def md(p: Path) -> str:
        return parse_document(p, out / p.stem[:12], docx_mode="python_docx",
                              on_progress=lambda m: None)["markdown"]

    if not TENDER.exists():
        print(f"  [--] 找不到招标文件原件，跳过：{TENDER}")
    else:
        t = md(TENDER)
        check(f"《…采购文件》判为招标（{len(t)} 字）", bool(_looks_like_tender(t)), True)
        print(f"       依据：{_looks_like_tender(t)}")

    if not BID.exists():
        print(f"  [--] 找不到参考标书，跳过：{BID}")
    else:
        b = md(BID)
        why = _looks_like_tender(b)
        check(f"参考标书判为**非**招标（{len(b)} 字）", bool(why), False)
        if why:
            print(f"       !! 误判依据：{why}")
            print("       这份文件把招标文件的话题词引了一整遍（实测「评审办法」7 次、"
                  "「评标委员会」12 次，比招标文件本身还多）——")
            print("       一旦按主题词判就会在这里翻车。判据必须留在抬头自称那一层。")


def main() -> int:
    section_1()
    section_2()
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
