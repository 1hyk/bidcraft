import axios from 'axios'

export const imageAssets = id => api.get(`/projects/${id}/image-assets`)
export const regenerateImage = (id,body) => api.post(`/projects/${id}/image-assets/regenerate`,body,{timeout:240000})
export const reviseMermaid = (id,body) => api.post(`/projects/${id}/image-assets/mermaid/revise`,body,{timeout:120000})
export const uploadImageAsset = (id,file,caption) => { const form=new FormData();form.append('file',file);form.append('caption',caption);return api.post(`/projects/${id}/image-assets/upload`,form) }

const api = axios.create({ baseURL: '/api', timeout: 60000 })
export const companyDocuments = () => api.get('/company/documents')
export const companyFacts = () => api.get('/company/facts')
export const companyUpload = file => {const data=new FormData();data.append('file',file);return api.post('/company/documents',data)}
export const companyRetry = id => api.post(`/company/documents/${id}/extract`)
export const companySaveBasic = data => api.put('/company/profile',data)
export const companyCreateFact = data => api.post('/company/facts',data)
export const companyReviseFact = (id,data) => api.patch(`/company/facts/${id}`,data)
export const companyChapters = id => api.get(`/company/projects/${id}/chapters`)
export const companySuggestions = (id,node) => api.post(`/company/projects/${id}/chapters/${node}/suggest`,{}, {timeout:240000})
export const companyApply = (id,node,tokens) => api.post(`/company/projects/${id}/chapters/${node}/apply`,{tokens})
export const companyRevisions = id => api.get(`/company/projects/${id}/revisions`)
export const companyUndo = (id,revision) => api.post(`/company/projects/${id}/revisions/${revision}/undo`)
export const getUnderstanding = id => api.get(`/projects/${id}/understanding`)
export const saveRequirementArrangement = (id, reqId, nodeIds) => api.put(`/projects/${id}/requirements/${reqId}/arrangement`, {node_ids:nodeIds})
export const confirmUnderstanding = (id,data) => api.put(`/projects/${id}/understanding`,data)
export const getCompanyProfile = () => api.get('/company-profile')
export const saveCompanyProfile = data => api.put('/company-profile',data)
export const uploadCompanyFile = file => { const form=new FormData();form.append('file',file);return api.post('/company-profile/files',form) }

// ---------- 项目 ----------
export const listProjects = (keyword = '') => api.get('/projects', { params: { keyword } })
export const createProject = (data) => api.post('/projects', data)
export const updateProject = (id, data) => api.patch(`/projects/${id}`, data)
export const classificationSuggestions = (id) => api.get(`/projects/${id}/classification-suggestions`)
export const refreshClassificationSuggestions = (id) => api.post(`/projects/${id}/classification-suggestions/refresh`)
export const deleteProject = (id) => api.delete(`/projects/${id}`)
// 标段（M1-2 三种形态：多选一 / 单标段确认 / 无标段直接进入）
export const listLots = (projectId) => api.get(`/projects/${projectId}/lots`)
export const selectLot = (projectId, lotId = null) => api.post(`/projects/${projectId}/lots/select`, { lot_id: lotId })
// 标段识别：从已解析的招标文件重新识别划分（后台任务）；status 供弹窗区分空态原因
export const extractLots = (projectId, overwrite = false) =>
  api.post(`/projects/${projectId}/lots/extract`, { overwrite })
export const getLotsStatus = (projectId) => api.get(`/projects/${projectId}/lots/status`)
// 标段档案（本标段包含什么；**有哪些字段由项目所属领域声明**，见后端 domains.lot_fields）
export const getLotProfile = (projectId) => api.get(`/projects/${projectId}/lot-profile`)
export const saveLotProfile = (projectId, payload) => api.put(`/projects/${projectId}/lot-profile`, payload)
export const rebuildLotProfile = (projectId) => api.post(`/projects/${projectId}/lot-profile/rebuild`)
export const getLotProfileRebuildStatus = (projectId) => api.get(`/projects/${projectId}/lot-profile/rebuild-status`)
// 取材来源条目详情（点取材弹窗里的某条 → 看该条原文/全文）
export const getUsedDetail = (projectId, kind, itemId) =>
  api.get('/sections/used-detail', { params: { project_id: projectId, kind, item_id: itemId } })
// 招标要求：编辑 / 删除（替代原来的"不适用"状态流转）
export const updateRequirement = (reqId, payload) => api.patch(`/projects/requirements/${reqId}`, payload)
export const deleteRequirement = (reqId) => api.delete(`/projects/requirements/${reqId}`)

// ---------- 项目文件 ----------
export const listFiles = (projectId) => api.get(`/projects/${projectId}/files`)
export const deleteProjectFile = (projectId, fileId) => api.delete(`/projects/${projectId}/files/${fileId}`)
export const reparseProjectFile = (projectId, fileId) => api.post(`/projects/${projectId}/files/${fileId}/reparse`)
export const uploadEditorImage = (projectId, file) => {
  const form = new FormData()
  form.append('file', file)
  return api.post(`/projects/${projectId}/files/image`, form)
}
export const fileSections = (projectId, fileId) => api.get(`/projects/${projectId}/files/${fileId}/sections`)
export const locateRequirementSource = (projectId, fileId, requirementId) => api.get(`/projects/${projectId}/files/${fileId}/locate-requirement/${requirementId}`)
export const fileSectionContent = (projectId, fileId, indexId) =>
  api.get(`/projects/${projectId}/files/${fileId}/sections/${indexId}/content`)
export const uploadFile = (projectId, file, category, onProgress) => {
  const form = new FormData()
  form.append('file', file)
  return api.post(`/projects/${projectId}/files?category=${category}`, form, {
    onUploadProgress: (e) => {
      if (e.total) onProgress?.(Math.round((e.loaded / e.total) * 100))
    },
  })
}

// ---------- 对话（SSE 流式） ----------
export const getChatHistory = (projectId, params = {}) => api.get('/chat/history', { params: { project_id: projectId, ...params } })
export const clearChatHistory = (projectId) => api.delete('/chat/history', { params: { project_id: projectId } })

export function chatStream(body, onDelta, onDone, onError, onEvent) {
  const ctrl = new AbortController()
  fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: ctrl.signal,
  }).then(async (resp) => {
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n\n')
      buf = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data:')) continue
        const evt = JSON.parse(line.slice(5))
        if (evt.type === 'delta') onDelta?.(evt.content)
        else if (evt.type === 'error') onError?.(evt.content)
        else if (evt.type === 'done') onDone?.()
        else onEvent?.(evt)
      }
    }
  }).catch((e) => onError?.(String(e)))
  return ctrl
}

// ---------- 编制流程（异步：start / status） ----------
// 没有 resume：v0.2（2026-09-10）起图里没有 interrupt，编制一路跑到 END，无处可确认。
// 后端 /compile/resume 端点尚在（只为兜改版前 stage='confirm_toc' 的历史会话行），
// 但**前端已无任何调用方**——别照着它写新代码。
export const compileStart = (data) => api.post('/compile/start', data)
export const compileStatus = (projectId) => api.get(`/compile/status/${projectId}`)
// 批量生成正文（并发消费 thought_ready 章节）/ 中途停止
export const writeBatch = (projectId) => api.post('/compile/write_batch', { project_id: projectId })
// 全文编制：对全部章节重新生成正文（覆盖式，旧版可回退）
export const writeAll = (projectId) => api.post('/compile/write_all', { project_id: projectId })
export const writeStop = (projectId) => api.post('/compile/write_stop', { project_id: projectId })

// 单章生成（SSE 流式：chapter_start / chapter_delta / chapter_done / chapter_error）
export function generateSectionStream(nodeId, projectId, onEvent, onDone, onError) {
  const ctrl = new AbortController()
  fetch(`/api/sections/content/${nodeId}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: projectId }),
    signal: ctrl.signal,
  }).then(async (resp) => {
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n\n')
      buf = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data:')) continue
        const evt = JSON.parse(line.slice(5))
        if (evt.type === 'done') onDone?.()
        else if (evt.type === 'chapter_error') onError?.(evt.content)
        else onEvent?.(evt)
      }
    }
  }).catch((e) => onError?.(String(e)))
  return ctrl
}

// ---------- 领域 ----------
// 领域清单从后端读（app/core/domains.py 注册表），前端不硬编码
export const listDomains = () => api.get('/domains')

// ---------- 知识库 ----------
export const listKnowledgeDocs = (params = {}) => api.get('/knowledge/docs', { params })
// scope/projectId 是「归属」（候选池第二道墙，见 backend/app/services/matching.py）：
// domain=领域通用 / project=仅某项目可见 / off=不参与检索。默认 domain = 加这道墙之前的行为。
export const uploadKnowledgeDoc = (file, category, domain, scope, projectId, onProgress) => {
  const form = new FormData()
  form.append('file', file)
  const q = new URLSearchParams({ category, ...(domain ? { domain } : {}), scope: scope || 'domain' })
  if (scope === 'project' && projectId) q.set('project_id', String(projectId))
  return api.post(`/knowledge/docs?${q}`, form, {
    onUploadProgress: (e) => {
      if (e.total) onProgress?.(Math.round((e.loaded / e.total) * 100))
    },
  })
}
// 改归属：改完立即生效，不需要重新提炼（过滤在匹配查询里做）
export const setKnowledgeDocScope = (id, scope, projectId = 0) =>
  api.put(`/knowledge/docs/${id}/scope`, { scope, project_id: projectId || 0 })
export const deleteKnowledgeDoc = (id) => api.delete(`/knowledge/docs/${id}`)
export const reprocessKnowledgeDoc = (id) => api.post(`/knowledge/docs/${id}/reprocess`)
export const knowledgeTaskStatus = (taskId) => api.get(`/knowledge/tasks/${taskId}`)
// 通用异步任务进度（事实抽取/知识提炼共用 task 表）
export const taskStatus = (taskId) => api.get(`/knowledge/tasks/${taskId}`)
export const listChapterTypes = () => api.get('/knowledge/chapter-types')
export const listKnowledgePages = (params = {}) => api.get('/knowledge/pages', { params })
export const getKnowledgePage = (id) => api.get(`/knowledge/pages/${id}`)
export const updateKnowledgePage = (id, data) => api.put(`/knowledge/pages/${id}`, data)
// 解析校对（M2-3：左原文右解析对照）
export const getParsed = (docId) => api.get(`/knowledge/docs/${docId}/parsed`)
export const saveParsed = (docId, markdown) => api.put(`/knowledge/docs/${docId}/parsed`, { markdown })
// 工艺工法（一工法一条概况 + 整篇原文浏览）
export const listMethods = (params = {}) => api.get('/knowledge/methods', { params })
export const getMethodContent = (docId) => api.get(`/knowledge/methods/${docId}/content`)

// ---------- 经验库 ----------
export const listExperience = (params = {}) => api.get('/experience', { params })
export const confirmExperience = (id) => api.post(`/experience/${id}/confirm`)
export const updateExperience = (id, data) => api.put(`/experience/${id}`, data)
export const deleteExperience = (id) => api.delete(`/experience/${id}`)

// ---------- 全局事实表 ----------
export const listFacts = (projectId, category = '') =>
  api.get(`/facts/project/${projectId}`, { params: { category } })
// 按当前选定标段重新提取全局参数（后台任务，返回 task_id 供轮询；不动标段档案）
export const reextractFacts = (projectId) => api.post(`/facts/project/${projectId}/reextract`)
export const confirmFact = (id) => api.post(`/facts/${id}/confirm`)
export const confirmAllFacts = (projectId) => api.post('/facts/confirm_all', { project_id: projectId })
export const updateFact = (id, data) => api.patch(`/facts/${id}`, data)
export const ignoreFact = (id) => api.post(`/facts/${id}/ignore`)
export const deleteFact = (id) => api.delete(`/facts/${id}`)

// ---------- 招标要求（覆盖检查清单） ----------
export const listRequirements = (projectId, category = '') =>
  api.get(`/projects/${projectId}/requirements`, { params: { category } })
export const setRequirementStatus = (id, status) => api.patch(`/projects/requirements/${id}/status`, { status })
export const confirmAllRequirements = (projectId) => api.post('/projects/requirements/confirm_all', { project_id: projectId })



// ---------- 目录树与正文 ----------
export const getChapterTree = (projectId) => api.get(`/sections/${projectId}/tree`)
export const putChapterTree = (projectId, tree) => api.put(`/sections/${projectId}/tree`, { tree })
export const createChapterNode = (projectId, data) => api.post(`/sections/${projectId}/nodes`, data)
export const updateChapterNode = (nodeId, data) => api.patch(`/sections/nodes/${nodeId}`, data)
export const deleteChapterNode = (nodeId) => api.delete(`/sections/nodes/${nodeId}`)
export const putNodeOutline = (nodeId, outline) => api.put(`/sections/nodes/${nodeId}/outline`, { outline })
export const regenerateNodeOutline = (nodeId) => api.post(`/sections/nodes/${nodeId}/regenerate_outline`)
export const runningOutlineTasks = (projectId) => api.get(`/sections/${projectId}/outline_tasks`)
export const getSectionContent = (nodeId) => api.get(`/sections/content/${nodeId}`)
export const saveSectionContent = (nodeId, data) => api.put(`/sections/content/${nodeId}`, data)
export const confirmSection = (nodeId) => api.post(`/sections/content/${nodeId}/confirm`)
export const revertSection = (nodeId) => api.post(`/sections/content/${nodeId}/revert`)
// 目录批量编辑（粘贴文字生成目录）
export const tocPreview = (projectId, text) => api.post(`/sections/${projectId}/toc/preview`, { text })
export const tocApply = (projectId, text) => api.post(`/sections/${projectId}/toc/apply`, { text })
export const generateTreeOutlines = (projectId) => api.post(`/sections/${projectId}/generate_outlines`)

// ---------- 导出 ----------
// 导出要遍历全项目正文、拼 Word，默认 60s 对长文档不够，故覆写 timeout；
// responseType=blob 拿到的是二进制流，文件名在 Content-Disposition 里（见 exportDocx）。
// 不带参数：排版与封面文档名都取自项目的排版设置（下面这组），导出接口不吃参数。
export const exportProjectDocx = (projectId) =>
  api.post(`/export/${projectId}/docx`, null, { responseType: 'blob', timeout: 600000 })

// ---------- 排版设置（导出的 Word 长什么样） ----------
// 受控取值清单（字体/字号/行距/页边距预设）由后端 app/core/docx_style.py 下发，
// 前端不硬编码；GET 一次连 style 和 options 一起给，抽屉打开一个来回就够。
export const getDocxStyle = (projectId) => api.get(`/projects/${projectId}/docx-style`)
export const saveDocxStyle = (projectId, payload) => api.put(`/projects/${projectId}/docx-style`, payload)

// ---------- 篇幅控制（目标正文页数 → 各章目标字数） ----------
// GET 一次给全（目标页数、字/页系数及其估算/实测来源、全书目标字数、每章目标与实际字数）。
// PUT 的两半语义不同：target_pages 是整体覆盖（漏填 = 不设目标），overrides 是稀疏补丁
// （值为空 = 清掉该章额度、回到按权重分摊）。
export const getPagePlan = (projectId) => api.get(`/projects/${projectId}/page-plan`)
export const savePagePlan = (projectId, payload) => api.put(`/projects/${projectId}/page-plan`, payload)
export const measurePagePlan = (projectId) => api.post(`/projects/${projectId}/page-plan/measure`, null, { timeout: 600000 })
export const adjustPagePlan = (projectId) => api.post(`/projects/${projectId}/page-plan/adjust`)
export const checkProposal = (projectId) => api.get(`/export/${projectId}/check`)
export const resolveProposalIssue = (projectId, data) => api.post(`/export/${projectId}/issues/resolve`, data)
export const reviewProposal = (projectId) => api.post(`/export/${projectId}/review`, null, { timeout: 300000 })
export const proposalRequirements = (projectId) => api.get(`/export/${projectId}/requirements`)
export const syncScoringIndex = (projectId) => api.post(`/sections/${projectId}/scoring-index/sync`)

export default api

export const serviceTypeOptions = () => api.get("/projects/service-type-options")
