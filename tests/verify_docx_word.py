"""用本机 Word 打开导出的 .docx，判定「没被判成损坏」并取真实页数。

比只解析 XML 硬：元素顺序/域代码这类问题 Word 会直接弹修复对话框，解析器不报错。
也顺带记录改排版前后的页数，供「页数控制」那轮估 字/页 系数。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_docx_word.py [project_id]

注意：首次调用要起 Word 进程，实测 >2 分钟，别用默认 120s 的超时打断。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.docx_style import from_dict                   # noqa: E402
from app.services.docx_export import export_project_docx     # noqa: E402

WD_STAT_PAGES = 2


def main() -> int:
    import pythoncom
    import win32com.client as win32

    project_id = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    cases = [
        ("默认（宋体小四 1.5 倍）", None),
        ("放大（宋体三号 16pt / 2 倍行距 / 窄边距）",
         from_dict({"body_size_pt": 16.0, "line_spacing": 2.0,
                    "margin_top_cm": 1.27, "margin_bottom_cm": 1.27,
                    "margin_left_cm": 1.27, "margin_right_cm": 1.27})),
    ]

    # 全程复用一个 Word 实例：每个文档都 Dispatch/Quit 一次的话，第二次会撞上
    # 上一个刚退出的 COM 服务（"远程过程调用失败"），跟文档本身无关。
    pythoncom.CoInitialize()
    app = win32.Dispatch("Word.Application")
    app.Visible = False
    app.DisplayAlerts = 0        # 弹修复对话框就没人点了，静默并按异常处理
    # 保活空文档：Visible=False 时，关掉最后一个打开的文档 Word 会自己退出，
    # 于是下一个 Documents.Open 就撞上"RPC 服务器不可用"——跟被测文档无关。
    keep = app.Documents.Add()
    rc = 0
    try:
        for label, style in cases:
            path, name = export_project_docx(project_id, style=style)
            print(f"{label}: {name}")
            doc = None
            try:
                doc = app.Documents.Open(str(path), ReadOnly=True, AddToRecentFiles=False)
                doc.Fields.Update()      # 目录与页码是域，先算出真实值
                print(f"  [OK] Word 正常打开（未判损坏）  "
                      f"页数={doc.ComputeStatistics(WD_STAT_PAGES)}  "
                      f"字数={doc.ComputeStatistics(0)}  "
                      f"段落={doc.Paragraphs.Count}  表格={doc.Tables.Count}")
            except Exception as e:  # noqa: BLE001
                print(f"  [!!] Word 打开失败：{e}")
                rc = 1
            finally:
                if doc is not None:
                    doc.Close(False)
    finally:
        try:
            keep.Close(False)
            app.Quit()
        except Exception:  # noqa: BLE001 Word 已自行退出时 Quit 会失败，忽略
            pass
        pythoncom.CoUninitialize()
    return rc


if __name__ == "__main__":
    sys.exit(main())
