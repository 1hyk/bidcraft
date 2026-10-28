// 与导出的缩进树分图规则一致：整支搬运，未知语法保留原图。
export function splitMindmap(source, limit = 14) {
  // 与后端一致：只修显式根和同级叶子的错误缩进，不猜分组。
  const repair = source.split(/\r?\n/)
  const ids = repair.map((line,i)=>i>0 && line.trim()?i:null).filter(i=>i!==null)
  const pad = line => line.length-line.trimStart().length
  if (repair[0]?.trim()==='mindmap' && ids.length>1 && /^\s*root\(\(.+\)\)\s*$/.test(repair[ids[0]]) && ids.slice(1).every(i=>pad(repair[i])===pad(repair[ids[0]]) && !/\t|::|[\[\]{}()]|^\s*%%/.test(repair[i]))) {
    repair[ids[0]]=repair[ids[0]].trim()
    ids.slice(1).forEach(i=>{repair[i]='  '+repair[i].trim()})
    source=repair.join('\n')
  }
  const lines = source.trim().split(/\r?\n/)
  if (lines[0]?.trim() !== 'mindmap') return [source]
  const nodes = lines.slice(1).filter(line => line.trim())
  if (nodes.length <= limit || nodes.some(line => /\t|::|^\s*%%|^\s*icon\b/.test(line))) return [source]
  const indent = line => line.length - line.trimStart().length
  const root = nodes[0]
  if (nodes.slice(1).some(line => indent(line) <= indent(root))) return [source]
  const depth = Math.min(...nodes.slice(1).map(indent))
  const branches = []
  for (const line of nodes.slice(1)) {
    if (indent(line) === depth) branches.push([])
    if (!branches.length) return [source]
    branches.at(-1).push(line)
  }
  if (branches.some(branch => branch.length + 1 > limit)) return [source]
  const groups = []
  let group = []
  for (const branch of branches) {
    if (group.length && group.length + branch.length + 1 > limit) {
      groups.push(group)
      group = []
    }
    group.push(...branch)
  }
  if (group.length) groups.push(group)
  return groups.map(group => `mindmap\n${root}\n${group.join('\n')}\n`)
}
