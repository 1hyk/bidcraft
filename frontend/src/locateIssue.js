// 只计算正文选择范围，不插入高亮标记，不改变保存/导出内容。
export function findIssueRange(doc, excerpt) {
  if (!excerpt) return null
  let text = ''
  const positions = []
  doc.descendants((node, pos) => {
    if (node.isTextblock) { text += '\n'; positions.push(null) }
    if (!node.isText) return
    text += node.text
    for (let i = 0; i < node.text.length; i++) positions.push(pos + i)
  })
  const start = text.indexOf(excerpt)
  if (start < 0) return null
  return { from: positions[start], to: positions[start + excerpt.length - 1] + 1 }
}

export function findImageIssue(doc, { asset_id, excerpt = '' }) {
  const matches = []
  const normalize = s => String(s || '').replace(/（AI生成示意[^）]*）|\(AI生成示意[^)]*\)/g, '').trim()
  doc.descendants((node, pos) => {
    if (node.type?.name !== 'materialImage') return
    if (asset_id ? node.attrs.assetId === asset_id : normalize(node.attrs.caption) === normalize(excerpt))
      matches.push({ from: pos, image: true })
  })
  // 不按相似名称猜图；同名多图时必须由素材ID消歧。
  return matches.length === 1 ? matches[0] : null
}
