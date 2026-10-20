// 展示用途分类，不改原始事实和后端抽取类别；新旧条目使用同一入口。
export const FACT_CATEGORIES = ['项目基本信息', '服务与技术要求', '进度与交付', '人员与资源要求', '质量与验收', '售后与保障', '商务与投标事项', '其他']

export function factCategory(fact) {
  const key = String(fact.fact_key || '')
  const category = String(fact.category || '')
  if (/质保|售后|故障|巡检|响应时限|响应时间|服务支持时段/.test(key)) return '售后与保障'
  if (/评分|分值|得分|扣分|加分|评审|报价|付款|资金|限价|投标|响应文件|递交|联合体|业绩|采购方式|采购平台|线上响应|采购文件获取|投诉|联系人/.test(key)) return '商务与投标事项'
  if (/验收|质量|合格|检测标准/.test(key)) return '质量与验收'
  if (/人员|岗位|团队|工程师|设备|仪器|工具|资源|培训人员/.test(key)) return '人员与资源要求'
  if (/工期|周期|进度|交付|交货|里程碑/.test(key)) return '进度与交付'
  if (/项目名称|采购人名称|采购人地址|项目编号|验证地点|项目地点|建设地点/.test(key)) return '项目基本信息'
  if (/技术|功能|性能|指标|验证模块|培训|标的数量|服务范围/.test(key)) return '服务与技术要求'
  return ({ 工期: '进度与交付', 人员: '人员与资源要求', 机械: '人员与资源要求', 造价: '商务与投标事项', 地质: '服务与技术要求', 结构: '服务与技术要求', 质量标准: '质量与验收', 安全目标: '质量与验收' })[category] || (FACT_CATEGORIES.includes(category) ? category : '其他')
}
