"""校验「排版设置」真的落到导出的 .docx 里（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_docx_style.py [project_id]

检查三件事：
  1. 默认档导出 = 改造前的那套规格（逐项比对，防止参数化时改跑偏）；
  2. 改字号/字体/行距/页边距/页眉页脚后，产物的 styles.xml / sectPr / footer 里
     确实是新值——不是被静默忽略；
  3. 整套 OOXML 能被解析（参数化最容易弄坏元素顺序，顺序错 Word 会报文档损坏）。
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.docx_style import DocxStyle, from_dict           # noqa: E402
from app.services.docx_export import export_project_docx       # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def read_part(path: Path, part: str) -> ET.Element:
    with zipfile.ZipFile(path) as z:
        return ET.fromstring(z.read(part))


def normal_style(doc_xml_root) -> ET.Element:
    for st in doc_xml_root.iter(f"{W}style"):
        if st.get(f"{W}styleId") == "Normal":
            return st
    raise AssertionError("styles.xml 里没有 Normal 样式")


def main() -> int:
    project_id = int(sys.argv[1]) if len(sys.argv) > 1 else 4

    # ---- 1. 默认档 == 改造前的规格 ----
    print("1) 默认档导出（应与你拍板的那套逐项一致）")
    p_default, _ = export_project_docx(project_id)
    styles = read_part(p_default, "word/styles.xml")
    normal = normal_style(styles)
    rfonts = normal.find(f"{W}rPr/{W}rFonts")
    check("正文中文字体", rfonts.get(f"{W}eastAsia"), "宋体")
    check("正文西文字体", rfonts.get(f"{W}ascii"), "Times New Roman")
    check("正文字号(半磅)", normal.find(f"{W}rPr/{W}sz").get(f"{W}val"), "24")
    check("正文行距(240=单倍)", normal.find(f"{W}pPr/{W}spacing").get(f"{W}line"), "360")
    sect = read_part(p_default, "word/document.xml").find(f"{W}body/{W}sectPr")
    pg = sect.find(f"{W}pgSz")
    check("页宽(缇)", pg.get(f"{W}w"), "11906")
    check("上下页边距(缇)", (sect.find(f"{W}pgMar").get(f"{W}top"),
                            sect.find(f"{W}pgMar").get(f"{W}bottom")), ("1440", "1440"))
    check("左右页边距(缇)", (sect.find(f"{W}pgMar").get(f"{W}left"),
                            sect.find(f"{W}pgMar").get(f"{W}right")), ("1797", "1797"))

    # ---- 2. 改过的那套必须真的生效 ----
    print("2) 仿宋 三号(16pt) 2 倍行距 / 窄边距 / 自定义页眉页脚")
    custom = from_dict({
        "body_font": "仿宋", "body_size_pt": 16, "line_spacing": 2.0,
        "margin_top_cm": 1.27, "margin_bottom_cm": 1.27,
        "margin_left_cm": 1.27, "margin_right_cm": 1.27,
        "header_text": "XX 项目响应文件", "footer_format": "第 {page} 页",
        "cover_title": "技术响应文件",
    })
    p_custom, _ = export_project_docx(project_id, style=custom)
    normal = normal_style(read_part(p_custom, "word/styles.xml"))
    rfonts = normal.find(f"{W}rPr/{W}rFonts")
    check("正文中文字体", rfonts.get(f"{W}eastAsia"), "仿宋")
    check("正文字号(半磅)", normal.find(f"{W}rPr/{W}sz").get(f"{W}val"), "32")
    check("正文行距(2 倍)", normal.find(f"{W}pPr/{W}spacing").get(f"{W}line"), "480")
    sect = read_part(p_custom, "word/document.xml").find(f"{W}body/{W}sectPr")
    check("上下页边距(缇)", sect.find(f"{W}pgMar").get(f"{W}top"), "720")
    check("左右页边距(缇)", sect.find(f"{W}pgMar").get(f"{W}left"), "720")
    # 三号正文 → 标题应推导成 20/18/16/16
    sizes = [int(s.find(f"{W}rPr/{W}sz").get(f"{W}val")) // 2
             for s in read_part(p_custom, "word/styles.xml").iter(f"{W}style")
             if s.get(f"{W}styleId") in ("Heading1", "Heading2", "Heading3", "Heading4")]
    check("各级标题字号(推导)", sorted(set(sizes), reverse=True), [20, 18, 16])
    # 页眉页脚
    with zipfile.ZipFile(p_custom) as z:
        htxt = " ".join(z.read(n).decode("utf-8") for n in z.namelist()
                        if re.match(r"word/header\d+\.xml", n))
        ftxt = " ".join(z.read(n).decode("utf-8") for n in z.namelist()
                        if re.match(r"word/footer\d+\.xml", n))
    check("页眉用了自定义文本", "XX 项目响应文件" in htxt, True)
    check("页脚用了「第 {page} 页」的前后缀", ("第" in ftxt and "页" in ftxt), True)
    check("页脚仍是 PAGE 域", "PAGE" in ftxt, True)

    # ---- 3. 所有 XML 部件可解析（元素顺序错会在这里或 Word 里炸） ----
    print("3) OOXML 部件解析")
    for p in (p_default, p_custom):
        with zipfile.ZipFile(p) as z:
            bad = []
            for n in z.namelist():
                if n.endswith(".xml") or n.endswith(".rels"):
                    try:
                        ET.fromstring(z.read(n))
                    except ET.ParseError:
                        bad.append(n)
        check(f"{p.name} 全部 XML 可解析", bad, [])

    print("\n结论：" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
