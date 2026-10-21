export const canAdoptArrangement = row => (!row.arranged_chapters?.length || row.arrangement_stale) && !!row.chapter_candidates?.length

export function arrangementPreview(rows) {
  return rows.filter(canAdoptArrangement).map(row => ({id:row.id,title:row.title,nodeIds:[row.chapter_candidates[0].id]}))
}

export async function persistArrangements(items, save, isCurrent = () => true) {
  let completed = 0
  const failed = []
  for (const item of items) {
    if (!isCurrent()) break
    try { await save(item); completed++ }
    catch { failed.push(item.title) }
  }
  return {completed,failed}
}
