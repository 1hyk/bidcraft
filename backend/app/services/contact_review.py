"""确定性识别具体联系信息，供人工确认；不将识别结果当作真实性判断。"""
import re

PHONE = re.compile(r'(?<![\dA-Za-z])(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)|(?<!\d)(?:0\d{2,3}[-—\s]?)\d{7,8}(?:[-转]\d{1,6})?(?!\d)|(?<!\d)(?:400|800)[-\s]?\d{3}[-\s]?\d{4}(?!\d)')
SURNAME = '赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹苏潘葛范彭鲁韦马苗方任袁柳鲍史唐费薛雷贺倪汤滕殷罗毕郝安常傅卞齐康伍余顾孟黄和穆萧尹姚邵汪祁毛禹狄贝明臧计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵季贾路江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房裘缪解应宗丁宣邓郁单杭洪包左石崔吉龚程邢裴陆荣翁荀羊甄曲封芮储靳段富焦侯宁巴仇武刘景詹龙叶白司韶黎乔赖卓谭申连艾向温庄柴阎慕习宦鱼容古易廖耿文寇阙欧殳沃利蔚越隆师巩聂晁勾敖融冷辛阚那简饶曾沙鞠丰巢关蒯相查后荆游竺权逯盖益桓公'
NAME = re.compile(r'(?:联系人|负责人|项目经理|工程师|姓名|技术负责人|售后负责人)\s*[：:为是由|｜]*\s*(['+SURNAME+r'][\u4e00-\u9fff]{1,3})(?=[\s，,。；;：:（(\d|｜]|$)')
HONORIFIC = re.compile(r'['+SURNAME+r'](?:[\u4e00-\u9fff])?(?:老师|先生|女士)(?![\u4e00-\u9fff])')
NOT_NAMES = {'待补充','待确认','未填写','项目经理','技术负责人','另行指定','指定人员','采购人','售后服务','技术支持','根据项目'}


def contact_review_excerpts(text, known=()):
    # 一行（含表格行）作为一处，同处姓名电话合并；保留精确片段供定位。
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 片段用于在编辑器里定位，必须给渲染后的纯文本：Markdown 的列表星号、粗体
        # 标记在正文里并不存在，带标记的片段会定位失败（2026-10-23）。
        clean = re.sub(r'[*_`]', '', line).strip()
        names = [m.group(1) for m in NAME.finditer(clean) if m.group(1) not in NOT_NAMES]
        names += [m.group(0) for m in HONORIFIC.finditer(clean)]
        names += [value for value in known if len(value) >= 2 and value in clean]
        phones = [m.group(0) for m in PHONE.finditer(clean)]
        if names or phones:
            token = (phones or names)[0]
            start = max(0, clean.find(token)-70)
            yield clean[start:start+240], '具体姓名或电话待人工确认：请核对身份、号码及使用用途；有来源也需确认，不代表公司人员或履约接口已核实'
        elif re.search(r'(?:姓名|联系人|联系电话|手机号|电话号码)\s*[：:]\s*(?:[_＿—-]+|待补充|待填写|待确认)?\s*$', clean):
            yield clean[:240], '联系信息待补充：正文明确设置了姓名或电话填写项，请据实填写或删除不必要的空项'
