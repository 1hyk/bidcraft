"""采购文件中的联系人只用于采购沟通，不能作为投标方人员能力依据。"""
import re

CONTACT = re.compile(r"联系人|联系电话|联系方式|投诉.*(?:人|电话)|电子邮箱|联系邮箱")
ROLE_RULE = (
    "【事实归属】本项目文件和项目事实来自采购方，属于需求、约束与采购沟通信息，"
    "不是本单位人员、设备、资质或已完成业绩的证明。采购文件联系人不得担任我方项目经理、"
    "售后人员或培训负责人。人员未定时优先完整设计岗位、职责和拟配置安排，不主动要求实名；"
    "仅招标明确要求的真实身份或资质证明缺失时标注待补充。可明确作出满足招标要求的履约承诺，"
    "并写明实施路径；不得把拟实施安排写成已完成事实或已经验证的结论。"
    "人员劳动关系、正式在职、社保归属和证书有效性也是真实事实：无已确认公司材料时，"
    "只能写拟按要求选派并提交证明，不得声称团队成员均为本单位正式员工、劳动关系已核实有效。"
    "招标未规定的人员更换通知天数、双岗交接天数不得擅自写成硬性合同条件；"
    "可写提前申请、经采购人确认并完成交接，具体时限按合同或双方确认执行。"
)


def technical_facts(facts):
    return [f for f in facts if not CONTACT.search(str(f.get('fact_key') or ''))]


def flag_unverified_conclusions(body: str) -> str:
    """生成器不能代替本单位完成偏离核查；保留结论并明确要求确认。"""
    pattern = re.compile(r'经(?:逐条|逐项|全面)?(?:核查|核实|自查)[^。！？\n]{0,100}(?:零负偏离|无负偏离)[^。！？\n]*')
    parts = re.split(r'(```.*?```)', body, flags=re.S)
    def flag(match):
        value = match[0]
        return value if '待确认' in value else value+'[待确认：偏离结论须由本单位依据实际能力和证据核实]'
    return ''.join(p if p.startswith('```') else pattern.sub(flag,p) for p in parts)


def flag_capability_claims(body: str) -> str:
    """资质等声明需核对整句及适用范围，不以招标要求或模型输出作为已核实依据。"""
    claim=re.compile(r'(?:我方|本公司|本单位)(?:承诺|现已|目前|已)?(?:拥有|具备|具有|持有|取得)[^。！？\n]{0,70}?(?:资质|证书|认证|业绩)'
                     r'|作为(?:具备|拥有|持有)[^。！？\n]{0,70}?(?:资质|证书|认证|业绩)[^。！？\n]{0,50}?(?:我方|本公司|本单位)')
    marker='[待确认：本单位资质或业绩声明须核对真实材料及适用范围]'
    def process(part):
        sentences=re.split(r'([。！？\n])',part)
        for i in range(0,len(sentences),2):
            sentence=sentences[i]
            if employment_claim(sentence) and '待确认' not in sentence:
                sentence += '[待确认：拟派人员劳动关系及社保证明须核对真实材料]'
                sentences[i]=sentence
            match=claim.search(sentence)
            if not match or marker in sentence:continue
            if re.search(r'(?:要求|规定|须|应当).{0,6}$',sentence[:match.start()]):continue
            sentences[i]=(re.sub(r'\|(\s*)$',lambda m:' '+marker+'|'+m[1],sentence)
                          if sentence.lstrip().startswith('|') and sentence.rstrip().endswith('|') else sentence+marker)
        return ''.join(sentences)
    return ''.join(p if p.startswith('```') else process(p) for p in re.split(r'(```.*?```)',body,flags=re.S))


def employment_claim(sentence):
    """拟派不豁免其后正式员工的事实断言。"""
    assertion = re.search(r'(?:团队成员|项目人员|所有成员|拟派人员).{0,18}(?:均为|均系|全部为|均属于).{0,12}(?:本单位|本公司|我方).{0,12}(?:员工|职工|人员)', sentence)
    verified = re.search(r'(?:劳动关系|社保关系).{0,8}(?:真实有效|已核实|已确认)', sentence)
    return bool((assertion or verified) and not re.search(r'应当|必须|须|需|将按|拟从|拟选|待确认|待核实',sentence))
