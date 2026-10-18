"""校验「投标文件格式 ≠ 本册目录」：抽取侧绝不把整本标书的章清单当成本册目录（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_tender_toc_scope.py

**这个脚本会真调一次模型**（整份招标文件进 prompt，约 1~3 分钟），因为它验的不是
函数逻辑，是**提示词认不认得出「投标文件格式」不是本册目录**。

背景（项目 4，2026-10-15）：招标文件是服务采购，只有「附件2 响应文件格式」——
一份平铺的九个章名（响应函 / 响应报价表 / … / 四、技术方案 / … / 公司简介 / 其他资料）。
抽取侧把它当成了施组目录，`found=true`、note 写「依据附件2响应文件格式及已有规定目录提取」。
这九个章随后被 `TOC_TITLES_SYSTEM` 规则 2「最高优先级、原样保留」无条件套成目录骨架，
而这条链一路定义 `suggested_chapter` 的靶子——售后 8 分那条需求因此匹配到 0 个节点。

修法（用户 2026-10-15 拍板）：提示词加判据——出现资信商务件的目录是投标文件的
组成部分清单，不是施组目录；只有其中**单独成支**的「施工组织设计」才取；否则 found=false。

检查：
  1. 提示词确实带上了判据（快速自检，不必调模型）；
  2. **真实语料 · 红线**：抽出的本册目录里不许出现资信商务件（响应函/报价表/公司简介…），
     即**不许是整本标书**。这是「扔进去一份招标文件，直接冒出一整本标书」那条老问题的守门员。
     `found` 的真假只打印不断言——它取决于招标有没有单独规定本册目录，会随模型判断浮动；
     这份文件的附件3「技术方案编制」项确实列了四个方案（2026-10-16 逐字核过原文），
     所以取到它们是对的，found=false 也不算错（生成侧有知识库骨架兜底）；
  3. 顺带打印 requirements 的 suggested_chapter 分布——靶子不该再是「响应函」这类
     投标文件格式的章，否则按章注入要求时一条都挂不上（售后 8 分那条就是这么丢的）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import tender_agent as TA                                  # noqa: E402

ok = True
DOCX = ROOT / "uploads" / "projects" / "4" / "202610" / "1e317f1c_实飞测试验证外协采购文件(1).docx"


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


# ---------------------------------------------------------------- 1. 提示词自检
def section_1() -> None:
    print("\n1) 提示词带上了「投标文件格式不算施组目录」的判据")
    p = TA.EXTRACT_SYSTEM
    for kw in ("资信商务件", "投标文件格式", "found=false", "单独列成一支"):
        check(f"提示词含「{kw}」", kw in p, True)
    # 旧措辞必须消失：它把「投标文件格式」直接指成了施组目录的出处，是这次误判的源头
    check("旧措辞已改掉", "招标文件通常在「投标文件格式」或「评标办法」中规定施组应包含的目录" in p, False)


# ---------------------------------------------------------------- 2. 真实语料
def section_2() -> None:
    print("\n2) 真实语料：抽出的本册目录不许是整本标书（红线），found 真假只打印")
    if not DOCX.exists():
        print(f"  [--] 找不到原件，跳过：{DOCX}")
        return
    from app.agents.parser_agent import parse_document

    out_dir = ROOT / "uploads" / "parsed" / "_verify_toc_scope"
    parsed = parse_document(DOCX, out_dir, docx_mode="python_docx",
                            on_progress=lambda m: print(f"     {m}"))
    md = parsed.get("markdown") or ""
    print(f"     已解析 {len(md)} 字")

    # project_id=None：**不把库里那份错的 toc_hint 喂进提示词**。
    # 要验的是提示词本身认不认得出，而不是它会不会被旧数据锚住。
    res = TA.extract_tender_all(md, DOCX.name, None)
    toc = res["toc"]
    print(f"     toc = {toc}")

    # **红线，与 found 真假无关**：投标文件整体的组成部分清单不许被当成本册目录。
    # 这份文件的「附件2 响应文件格式」平铺了九个章名（响应函 / 响应报价表 / … / 四、技术方案 /
    # 公司简介 / 其他资料）。整份取进来，下游 TOC_TITLES_SYSTEM 规则 2「最高优先级、原样保留」
    # 会把它套成目录骨架——于是「扔一份招标文件进去，直接冒出一整本标书」，那正是要防的事。
    MATERIALS = ("响应函", "投标函", "报价表", "开标一览表", "法定代表人", "授权书",
                 "营业执照", "资质证书", "财务报告", "投标保证金", "承诺书",
                 "公司简介", "其他资料")
    got = [c.get("title") or "" for c in toc.get("chapters") or []]
    check("本册目录不掺资信商务件", [w for w in MATERIALS if any(w in t for t in got)], [])

    # found 的真假**允许浮动**：它回答的是「招标有没有单独规定本册目录」。
    # 这份文件的附件3 评审办法「技术方案编制」项明确列了 2.1~2.4 四个方案
    # （2026-10-16 逐字核过原文：工程化交付方案 / 测试验收准备方案 / 项目实施方案 /
    #   项目售后服务与保障能力）——所以取到那四支是对的，
    # 提示词也确实允许「优先取招标文件明确规定的本册目录」。
    # 没取到（found=false）也不算错：生成侧有知识库骨架兜底。
    # **两种都不许是整本标书**，所以上面那条红线才是这个用例真正要盯的。
    if toc.get("found"):
        print(f"     （found=true，取到 {len(got)} 章：{got}）")
    else:
        print("     （found=false：本次按「没有单独规定本册目录」处理，生成侧走知识库骨架兜底）")

    reqs = res["requirements"]
    print(f"\n     招标要求 {len(reqs)} 条，suggested_chapter 分布：")
    from collections import Counter

    dist = Counter(r.get("suggested_chapter") or "（空）" for r in reqs)
    for name, n in dist.most_common():
        print(f"       {n:>3}  {name}")
    # 这一条是这次修复的下游价值：靶子不该是「响应函」这类投标文件格式的章，
    # 否则按章注入要求时一条都挂不上（售后 8 分那条就是这么丢的）。
    check("没有需求挂到资信商务件上",
          [w for w in MATERIALS
           if any(w in (r.get("suggested_chapter") or "") for r in reqs)], [])


def main() -> int:
    section_1()
    section_2()
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
