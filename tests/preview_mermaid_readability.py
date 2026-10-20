"""真实浏览器对照：只调整布局空隙，不改流程源码。显式运行，保留图片证据。"""
import json
import sys
from pathlib import Path
from PIL import Image
import io

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.services import mermaid_render as mr, docx_export as dx

if __name__=='__main__':
    directory=ROOT/'uploads/_verification/mermaid_readability_20261019'
    directory.mkdir(exist_ok=True)
    source=(ROOT/'uploads/_verification/quality_context_20261019/long-flow.mmd').read_text(encoding='utf-8')
    config=mr._FLOWCHART
    result=[]
    for name,flow in [('before',{'htmlLabels':False}),('compact',config)]:
        mr._FLOWCHART=flow
        blob=mr._render_chunk([source])[0]
        assert blob, name+' failed'
        path=directory/(name+'.png');path.write_bytes(blob)
        width=dx._mermaid_width_cm(path) if name=='compact' else dx._fit_width_cm(path)
        with Image.open(io.BytesIO(blob)) as image:
            result.append({'variant':name,'pixels':image.size,'word_width_cm':width,
                           'word_height_cm':width*image.height/image.width,
                           'estimated_font_pt':round(16*mr._SCALE*width/2.54*72/image.width,2)})
    mr._FLOWCHART=config
    (directory/'comparison.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)
