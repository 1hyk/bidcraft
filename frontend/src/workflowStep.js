export function workflowMode({understanding,checking,mode,hasDirectory,stage,streaming}) {
  if (understanding) return 'understanding'
  if (checking) return 'checking'
  if (['understanding','planning','writing','checking'].includes(mode)) return mode
  if (stage === 'writing' || streaming) return 'writing'
  if (['generating_toc','generating_outline'].includes(stage)) return 'planning'
  if (!hasDirectory) return 'understanding'
  return 'writing'
}
export function workflowStep(state) {
  return {understanding:1,planning:2,writing:3,checking:4}[workflowMode(state)]
}
export function openUnderstandingOnEntry({directoryLoaded,hasDirectory,mode,stage}) {
  return directoryLoaded && !hasDirectory && !mode && !['writing','generating_toc','generating_outline'].includes(stage)
}
// 分类保存导致的指纹变化可预期；确认之前仍须检查用户看过的招标依据是否发生变化。
export function understandingBasis(data) {
  return JSON.stringify({lot:data.lot,requirements:(data.requirements || []).map(({chapter_candidates,...row})=>row),files:data.files || []})
}

export async function saveBeforeWorkflow({prepare, pendingSave, nodeId, save, reload}) {
  const pending=prepare?.()
  try { await pendingSave } catch(error) {
    // 旧自动保存失败后允许用仍在编辑器内的草稿重试，不能永久锁死导航。
    if(!pending?.dirty || !nodeId) throw error
  }
  if(pending?.dirty && nodeId) {
    await save(nodeId,{format:'html',body:pending.html})
    await reload(nodeId)
  }
}
