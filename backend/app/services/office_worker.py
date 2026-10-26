"""由 LibreOffice 自带 Python 执行，更新目录/域并返回正文的真实页范围。"""
import json
import sys
import time
from pathlib import Path
import uno
from com.sun.star.beans import PropertyValue


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


def main():
    booted = time.perf_counter()
    pipe, input_path, output_dir = sys.argv[1:]
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', local)
    context = None
    last_error = ''
    # 首次用新 profile 启动 LibreOffice 实测要 46–50 秒；给足冷启动余量，
    # 但必须小于 document_layout 的 docx_renderer_timeout（240 秒）。
    for _ in range(900):
        try:
            context = resolver.resolve('uno:pipe,name=' + pipe + ';urp;StarOffice.ComponentContext')
            break
        except Exception as exc:
            last_error = str(exc)
            time.sleep(.2)
    if context is None:
        raise RuntimeError('Cannot connect to local office: '+last_error)
    connected = round(time.perf_counter() - booted, 2)
    desktop = context.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', context)
    doc = None
    timings = {'connect': connected}
    started = time.perf_counter()
    try:
        doc = desktop.loadComponentFromURL(Path(input_path).resolve().as_uri(), '_blank', 0,
                                            (prop('Hidden', True), prop('UpdateDocMode', 3)))
        if doc is None:
            raise RuntimeError('排版引擎未能打开文档')
        timings['open'] = round(time.perf_counter() - started, 2)
        for _ in range(2):
            doc.refresh()
            indexes = doc.getDocumentIndexes()
            for n in range(indexes.Count): indexes.getByIndex(n).update()
            doc.getTextFields().refresh()
        timings['update_fields'] = round(time.perf_counter() - started
                                        - timings['open'], 2)
        out = Path(output_dir)
        mark = time.perf_counter()
        doc.storeToURL((out/'document.docx').resolve().as_uri(),
                       (prop('FilterName', 'Office Open XML Text'), prop('Overwrite', True)))
        timings['save_docx'] = round(time.perf_counter() - mark, 2)
        mark = time.perf_counter()
        doc.storeToURL((out/'document.pdf').resolve().as_uri(),
                       (prop('FilterName', 'writer_pdf_Export'), prop('Overwrite', True)))
        timings['save_pdf'] = round(time.perf_counter() - mark, 2)
        cursor = doc.CurrentController.getViewCursor()
        pages = {}
        for name in ('_bc_body_start', '_bc_body_end'):
            if not doc.Bookmarks.hasByName(name):
                raise RuntimeError('文档缺少正文分页标记，不能将总页数冒充正文页数')
            cursor.gotoRange(doc.Bookmarks.getByName(name).Anchor, False)
            pages[name] = cursor.getPage()
        result = {'body_start': pages['_bc_body_start'], 'body_end': pages['_bc_body_end'],
                  'body_pages': pages['_bc_body_end'] - pages['_bc_body_start'] + 1,
                  'toc_updated': indexes.Count > 0, 'engine': 'LibreOffice',
                  'timings': {**timings, 'total': round(time.perf_counter() - started, 2)}}
        (out/'layout.json').write_text(json.dumps(result), encoding='utf-8')
    finally:
        closing = time.perf_counter()
        if doc is not None: doc.close(True)
        closed = time.perf_counter()
        desktop.terminate()
        terminated = time.perf_counter()
        try:
            layout = Path(output_dir) / 'layout.json'
            if layout.is_file():
                data = json.loads(layout.read_text(encoding='utf-8'))
                data.setdefault('timings', {}).update(
                    close=round(closed - closing, 2),
                    terminate=round(terminated - closed, 2),
                    worker_total=round(terminated - booted, 2))
                layout.write_text(json.dumps(data), encoding='utf-8')
        except Exception:
            pass


if __name__ == '__main__': main()
