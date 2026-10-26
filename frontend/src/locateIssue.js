const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&nbsp;': ' ' }
const plain = s => String(s || '').replace(/&(?:amp|lt|gt|quot|#39|nbsp);/g, m => ENTITIES[m])

// 检查结果里的片段来自后端正文，可能带 HTML 实体，或被段落/空白折叠过；
// 先按原文精确定位，再按空白归一化定位。只返回选择范围，不改正文。
function normalizeText(text, positions) {
  const chars = [], map = []
  let pendingSpace = false
  for (let i = 0; i < text.length; i++) {
    if (/\s/.test(text[i])) { pendingSpace = chars.length > 0; continue }
    if (pendingSpace) { chars.push(' '); map.push(positions[i]); pendingSpace = false }
    chars.push(text[i]); map.push(positions[i])
  }
  return { text: chars.join(''), map }
}

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
  const wanted = plain(excerpt)
  const start = text.indexOf(wanted)
  if (start >= 0 && positions[start] != null && positions[start + wanted.length - 1] != null)
    return { from: positions[start], to: positions[start + wanted.length - 1] + 1 }
  const normalized = normalizeText(text, positions)
  // 检查结果里的片段是 Markdown 源码，编辑器里是渲染后的文字：去掉标题、列表、引用、
  // 表格分隔等标记再匹配，否则「* **拟选派标准**」这类行永远定位不到。
  const demark = s => s
    .replace(/\*\*|__|`/g, '')
    .replace(/^\s*#{1,6}\s+/gm, '')
    .replace(/^\s*(?:[*+-]|\d+[.、])\s+/gm, '')
    .replace(/^\s*>\s?/gm, '')
    .replace(/\s*\|\s*/g, ' ')
  const candidates = [...new Set([
    wanted.replace(/\s+/g, ' ').trim(),
    demark(wanted).replace(/\s+/g, ' ').trim(),
  ].filter(Boolean))]
  for (const wantedNormalized of candidates) {
    const at = normalized.text.indexOf(wantedNormalized)
    if (at >= 0)
      return { from: normalized.map[at], to: normalized.map[at + wantedNormalized.length - 1] + 1 }
  }
  return null
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
