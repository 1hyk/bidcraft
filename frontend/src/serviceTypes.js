// 常用分类与当前选择合并；不按行业删除业务，不把建议写入公共选项。
export function serviceChoices(common = [], selected = [], suggested = []) {
  return [...new Set([...common, ...selected, ...suggested].filter(v => typeof v === 'string' && v.trim()).map(v => v.trim()))]
}
export function existingServiceTypes(projects, domain = '') {
  return serviceChoices([], projects.filter(p => !domain || p.domain === domain).flatMap(p => p.project_types || []))
}
export function classificationImpact(before, after) {
  if (before.domain !== after.domain) return '应用行业已改变：请重新匹配素材并核对目录和正文；已有正文未改动。'
  if ((before.service_content || '') !== (after.service_content || '') || JSON.stringify(before.technical_objects || []) !== JSON.stringify(after.technical_objects || [])) return '服务内容或工作对象已改变：请核对受影响章节，已有正文未改动。'
  return '分类已保存；类型标签调整无需自动重生成正文。'
}
