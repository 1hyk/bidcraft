<template>
  <div class="knowledge">
    <!-- 页头 -->
    <header class="page-head">
      <div class="head-left">
        <h1 class="page-title">知识库</h1>
        <span class="page-sub">历史标书 · 工艺工法，两类知识分离管理</span>
      </div>
      <div class="head-tools">
        <el-select v-model="domain" class="domain-filter" @change="load">
          <el-option label="全部应用行业" value="" />
          <el-option v-for="d in domains" :key="d.key" :label="d.name" :value="d.key" />
        </el-select>
        <el-input v-model="keyword" placeholder="搜索文档…" clearable class="search">
          <template #prefix>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          </template>
        </el-input>
        <el-button type="primary" @click="openUpload">
          <span class="plus">＋</span> 上传文档
        </el-button>
      </div>
    </header>

    <!-- 两类库 Tab -->
    <div class="kb-tabs">
      <button
        v-for="t in TABS" :key="t.key"
        class="kb-tab" :class="{ on: activeTab === t.key }"
        @click="activeTab = t.key"
      >
        {{ t.label }}
        <span class="kb-tab-count num">{{ countByTab(t.key) }}</span>
      </button>
    </div>

    <!-- 说明条 -->
    <div class="kb-tip">
      <span class="kb-tip-dot" />
      <!-- 体裁词按域取（`${genre.full}` = 低空「技术方案」/ 铁路「施工组织设计」）。
           原先后半句写死「施工方法素材」，前半句跟着领域走、后半句不跟——低空项目读起来
           就是"技术方案里的施工方法"，两半对不上。别再把行业名词写死在这里。 -->
      {{ activeTab === 'sod' ? '历史标书库：存历史项目的标书文档，用于提炼「这类章节怎么写」的知识页（写法参考）' : `工艺工法库：存"这项工作按什么规范怎么做"的标准做法原文，写「${genre.full}」里方法类章节时整篇取用` }}
    </div>

    <!-- 文档列表 -->
    <main class="kb-table-wrap">
      <el-table :data="filteredDocs" class="kb-table" :empty-text="'暂无文档，点击右上角上传'">
        <el-table-column label="文件名" min-width="260">
          <template #default="{ row }">
            <div class="doc-cell">
              <span class="doc-icon" :class="row.kind">{{ fileIcon(row.file_name) }}</span>
              <span class="doc-name" :title="row.file_name">{{ row.file_name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="应用行业" width="110">
          <template #default="{ row }">
            <span class="doc-domain">{{ domainName(row.domain) }}</span>
          </template>
        </el-table-column>
        <!-- 归属：这份料归谁参考。点徽标即可改（候选池的第二道墙，见 api/index.js 注释） -->
        <el-table-column label="归属" width="150">
          <template #default="{ row }">
            <span
              class="scope-badge" :class="row.scope || 'domain'"
              :title="'点击修改归属 · ' + scopeDesc(row)"
              @click="openScope(row)"
            >{{ scopeLabel(row) }}<i class="scope-caret">▾</i></span>
          </template>
        </el-table-column>
        <el-table-column label="解析状态" width="180">
          <template #default="{ row }">
            <template v-if="row.status === 'parsing' && taskProgress[row.id]">
              <div class="row-progress">
                <div class="rp-head">
                  <span class="status-badge" :class="row.status"><span class="status-dot" />解析中</span>
                  <span class="rp-pct num">{{ taskProgress[row.id].progress }}%</span>
                </div>
                <div class="rp-bar"><div class="rp-fill" :style="{ width: taskProgress[row.id].progress + '%' }" /></div>
                <div class="rp-detail">{{ taskProgress[row.id].detail }}</div>
              </div>
            </template>
            <span v-else class="status-badge" :class="row.status">
              <span class="status-dot" />
              {{ STATUS_MAP[row.status] }}
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="activeTab === 'method' ? '概况' : '知识页'" width="100">
          <template #default="{ row }">
            <span class="num page-count">{{ row.pageCount }}</span>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="150">
          <template #default="{ row }">
            <span class="doc-time num">{{ fmtTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="290" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" size="small" @click="viewEntries(row)">
                {{ row.kind === 'method' ? '查看工法' : '查看知识页' }}
              </el-button>
              <el-button link size="small" @click="proofread(row)">解析校对</el-button>
              <el-button v-if="row.status === 'failed'" link type="warning" size="small" @click="reprocess(row)">重新提炼</el-button>
              <el-button link type="danger" size="small" @click="removeDoc(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </main>

    <!-- 上传对话框 -->
    <el-dialog v-model="showUpload" width="560" :show-close="false" class="kb-dialog">
      <template #header>
        <div class="dlg-head">
          <span class="dlg-title">上传文档到{{ activeTab === 'sod' ? '历史标书库' : '工艺工法库' }}</span>
          <button class="dlg-close" @click="showUpload = false">✕</button>
        </div>
      </template>
      <!-- 领域：决定提炼时给模型哪套专业标签词表，以及入库后哪些项目能看到这份料 -->
      <div class="up-domain">
        <span class="up-domain-label">应用行业</span>
        <el-select v-model="uploadDomain" class="up-domain-select">
          <el-option v-for="d in domains" :key="d.key" :label="d.name" :value="d.key" />
        </el-select>
        <span class="up-domain-tip">按应用行业隔离素材；每页再按服务类型和章节用途匹配，不代表同一行业的内容都适用</span>
      </div>
      <!-- 归属：领域墙挡得住别的行业，挡不住同域里别的项目。别人的投标文件别丢进通用池 -->
      <div class="up-domain">
        <span class="up-domain-label">归属</span>
        <el-select v-model="uploadScope" class="up-domain-select">
          <el-option label="行业通用" value="domain" />
          <el-option label="仅某个项目" value="project" />
          <el-option label="不参与检索" value="off" />
        </el-select>
        <span class="up-domain-tip">{{ SCOPE_TIPS[uploadScope] }}</span>
      </div>
      <div v-if="uploadScope === 'project'" class="up-domain">
        <span class="up-domain-label">归属项目</span>
        <el-select v-model="uploadProjectId" filterable placeholder="选择项目" class="up-domain-select">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <span class="up-domain-tip">只有这个项目生成时会参考它</span>
      </div>
      <el-upload
        drag multiple
        :auto-upload="false"
        accept=".pdf,.docx"
        :on-change="onFileChange"
        class="kb-upload"
      >
        <div class="upload-hint">
          <div class="upload-icon">⇪</div>
          <p class="upload-text">拖拽文件到此处，或点击选择</p>
          <p class="upload-sub">支持 .pdf / .docx，可批量上传</p>
        </div>
      </el-upload>
      <!-- 上传进度（仅上传阶段短暂展示：全部传完自动关弹窗，解析进度看列表行内） -->
      <div v-if="jobs.length" class="upload-progress">
        <div v-for="(j, i) in jobs" :key="i" class="job-row">
          <div class="up-head">
            <span class="job-name" :title="j.name">{{ j.name }}</span>
            <span class="num job-pct" :class="j.phase">{{ j.phase === 'done' ? '✓' : j.pct + '%' }}</span>
          </div>
          <div class="up-bar"><div class="up-fill" :class="j.phase" :style="{ width: (j.phase === 'done' ? 100 : j.pct) + '%' }" /></div>
          <div class="up-detail">{{ j.detail }}</div>
        </div>
      </div>
      <div v-if="uploadError" class="upload-error">{{ uploadError }}</div>
      <template #footer>
        <el-button @click="closeUpload">关闭</el-button>
        <el-button v-if="!jobs.length" type="primary" :loading="uploading" @click="doUpload">开始上传并解析</el-button>
        <el-button v-else-if="jobs.some(j => j.phase === 'failed')" type="primary" @click="doUpload">重试上传</el-button>
      </template>
    </el-dialog>

    <!-- 修改归属 -->
    <el-dialog v-model="showScope" width="520" :show-close="false" class="kb-dialog">
      <template #header>
        <div class="dlg-head">
          <span class="dlg-title">修改归属</span>
          <button class="dlg-close" @click="showScope = false">✕</button>
        </div>
      </template>
      <div class="sc-doc">{{ scopeRow?.file_name }}</div>
      <el-radio-group v-model="scopeForm.scope" class="sc-group">
        <el-radio v-for="o in SCOPE_OPTIONS" :key="o.value" :value="o.value" class="sc-opt">
          <span class="sc-label">{{ o.label }}</span>
          <span class="sc-desc">{{ o.desc }}</span>
        </el-radio>
      </el-radio-group>
      <div v-if="scopeForm.scope === 'project'" class="sc-proj">
        <span class="up-domain-label">归属项目</span>
        <el-select v-model="scopeForm.project_id" filterable placeholder="选择项目" class="up-domain-select">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <span class="up-domain-tip">改完立即生效，不用重新提炼</span>
      </div>
      <template #footer>
        <el-button @click="showScope = false">取消</el-button>
        <el-button type="primary" :loading="scopeSaving" @click="saveScope">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { listDomains, listKnowledgeDocs, uploadKnowledgeDoc, setKnowledgeDocScope, deleteKnowledgeDoc, reprocessKnowledgeDoc, knowledgeTaskStatus, listProjects } from '../api'
import { genreOf } from '../genre'

const router = useRouter()
const route = useRoute()
const keyword = ref('')
const activeTab = ref('sod')
const showUpload = ref(false)
const uploading = ref(false)
const uploadList = ref([])  // 存 raw File

// 领域：domain=列表筛选（''=全部）；uploadDomain=本次上传归属。
// 清单从 /api/domains 读，新增领域不在前端改代码。
const domains = ref([])
const domain = ref('')
const uploadDomain = ref('')
// 本次上传的归属；uploadProjectId=null 表示还没选（scope=project 时必须选）
const uploadScope = ref('domain')
const uploadProjectId = ref(null)
const defaultDomain = ref('')
const domainName = (key) => domains.value.find(d => d.key === key)?.name || key || '—'
// 文案里的体裁词（「技术方案」这类）按域取，见 src/genre.js。
// 取**当前筛选的领域**；筛「全部」时没有具体领域，拿默认域代表——知识库本身是跨域的，
// 一条提示语只能给一个说法。
const genre = computed(() => genreOf(domains.value, domain.value || defaultDomain.value))

const TABS = [
  { key: 'sod', label: '历史标书库' },
  { key: 'method', label: '工艺工法库' },
]
const STATUS_MAP = { pending: '待解析', parsing: '解析中', parsed: '已解析', failed: '解析失败' }

// ---- 归属（候选池的第二道墙）----
// 领域墙挡的是「别的行业」；它挡不住同域里**别的项目**。实测：一份别的项目的低空投标
// 文件传进通用池，lowalt 池 1156 → 1353 页，写正文时就会被它的写法带跑。
// 三态与后端 `knowledge_doc.scope` 一一对应（matching.SCOPE_*），改完立即生效。
const SCOPE_TIPS = {
  domain: '同域所有项目都能参考它（默认）',
  project: '只有选定的项目能参考它',
  off: '不进检索池；知识库里照样看得到',
}
const SCOPE_OPTIONS = [
  { value: 'domain', label: '行业通用', desc: '同域所有项目都能参考它。行业通用的做法、标准工法放这里。' },
  { value: 'project', label: '仅某个项目', desc: '只有选定的那个项目能参考它。本项目专用的资料放这里。' },
  { value: 'off', label: '不参与检索', desc: '只留档，任何项目都不会参考它（知识库页面里照样看得到）。别人的投标文件、临时传错的就放这里。' },
]

const projects = ref([])
let projectsLoaded = false
async function loadProjects() {
  if (projectsLoaded) return
  try {
    const { data } = await listProjects()
    // 已删除的项目不进选择器（后端 /projects 列表不按 status 过滤）
    projects.value = (data || []).filter(p => p.status !== 'deleted')
    projectsLoaded = true
  } catch { /* 取不到只是选择器空着，不影响其它功能 */ }
}

function scopeLabel(row) {
  if (row.scope === 'project') return row.project_name || (row.project_id ? `项目 ${row.project_id}` : '仅某项目')
  return (SCOPE_OPTIONS.find(o => o.value === row.scope) || SCOPE_OPTIONS[0]).label
}
function scopeDesc(row) {
  if (row.scope === 'project' && row.project_name) return `仅「${row.project_name}」可见`
  return (SCOPE_OPTIONS.find(o => o.value === row.scope) || SCOPE_OPTIONS[0]).desc
}

const showScope = ref(false)
const scopeRow = ref(null)
// project_id 用 null 表示「没选」——0 会被 el-select 当成一个查无此项的值显示出来
const scopeForm = ref({ scope: 'domain', project_id: null })
const scopeSaving = ref(false)

async function openScope(row) {
  scopeRow.value = row
  scopeForm.value = { scope: row.scope || 'domain', project_id: row.project_id || null }
  showScope.value = true
  await loadProjects()
}

async function saveScope() {
  if (scopeForm.value.scope === 'project' && !scopeForm.value.project_id) {
    return ElMessage.warning('请选择归属的项目')
  }
  scopeSaving.value = true
  try {
    const { data } = await setKnowledgeDocScope(
      scopeRow.value.id, scopeForm.value.scope, scopeForm.value.project_id,
    )
    // 就地改这一行，不整表重拉：列表每次 load() 都要为每份文档数一遍知识页
    const p = projects.value.find(x => x.id === data.project_id)
    scopeRow.value.scope = data.scope
    scopeRow.value.project_id = data.project_id
    scopeRow.value.project_name = p?.name || ''
    showScope.value = false
    ElMessage.success('归属已更新，立即生效')
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    scopeSaving.value = false
  }
}

const docs = ref([])

const filteredDocs = computed(() => {
  let list = docs.value.filter(d => d.kind === activeTab.value)
  if (keyword.value) list = list.filter(d => d.file_name.includes(keyword.value))
  return list
})
const countByTab = (key) => docs.value.filter(d => d.kind === key).length

const fileIcon = (name) => (/\.pdf$/i.test(name) ? 'PDF' : 'DOC')
const fmtTime = (t) => (t ? t.slice(5, 16).replace('T', ' ') : '')

async function loadDomains() {
  try {
    const { data } = await listDomains()
    domains.value = data.items || []
    defaultDomain.value = data.default || ''
    if (!uploadDomain.value) uploadDomain.value = defaultDomain.value
  } catch (e) { /* 领域清单取不到不影响其它功能 */ }
}

async function load() {
  // 领域筛选走服务端（文档量会涨，前端过滤迟早要改）
  const { data } = await listKnowledgeDocs(domain.value ? { domain: domain.value } : {})
  docs.value = data
}

// 跳子页时携带来源 tab，返回后恢复到进入前的库
function viewEntries(row) {
  if (row.kind === 'method') router.push({ path: '/knowledge/methods', query: { doc: row.id, from: row.kind } })
  else router.push({ path: '/knowledge/pages', query: { doc: row.id, from: row.kind } })
}
function proofread(row) {
  router.push({ path: '/knowledge/pages', query: { doc: row.id, view: 'proof', from: row.kind } })
}
// 从 URL ?tab= 恢复激活库（返回按钮带参 / 手动刷新保持）
function applyTabQuery() {
  const t = route.query.tab
  if (t === 'sod' || t === 'method') activeTab.value = t
}
watch(() => route.query.tab, applyTabQuery)
async function removeDoc(row) {
  try {
    await ElMessageBox.confirm(`确定删除「${row.file_name}」？`, '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    await deleteKnowledgeDoc(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) { /* 用户取消或错误 */ }
}

async function reprocess(row) {
  try {
    const { data } = await reprocessKnowledgeDoc(row.id)
    ElMessage.success('已重新提交提炼')
    await load()
    if (data.task_id) startPolling([data.task_id])
  } catch (e) {
    ElMessage.error('操作失败：' + (e?.response?.data?.detail || e.message))
  }
}

function onFileChange(file) {
  uploadList.value.push(file.raw)
}
const uploadPct = ref(0)  // 上传进度百分比
// 上传进度：{ name, phase: uploading|done|failed, pct, detail, taskId }
const jobs = ref([])
const uploadError = ref('')
const jobsUploading = computed(() => jobs.value.some(j => j.phase === 'uploading'))

// 每次打开都把「归属」拨回默认：上一次可能是给别人的标书设的「不参与检索」，
// 留着不改，下次传自己的参考标书就会**悄悄不被使用**——比串味更难发现。
// 领域不重置：绝大多数人在同一个领域里干活，来回重选只是添乱。
function openUpload() {
  uploadScope.value = 'domain'
  uploadProjectId.value = null
  showUpload.value = true
}

function closeUpload() {
  showUpload.value = false
  uploadList.value = []
  jobs.value = []
  uploadError.value = ''
}

async function doUpload() {
  if (!uploadList.value.length) return ElMessage.warning('请先选择文件')
  if (uploadScope.value === 'project' && !uploadProjectId.value) {
    return ElMessage.warning('归属选了「仅某个项目」，请先选择项目')
  }
  uploading.value = true
  uploadError.value = ''
  const taskIds = []
  let hasFail = false
  // 全部成功 → 关弹窗，进度交给列表行内异步解析轮询；有失败 → 弹窗停留显示错误可重试
  try {
    for (const f of [...uploadList.value]) {
      const job = { name: f.name, phase: 'uploading', pct: 0, detail: '上传中…', taskId: null }
      jobs.value.push(job)
      try {
        const { data } = await uploadKnowledgeDoc(
          f, activeTab.value, uploadDomain.value, uploadScope.value, uploadProjectId.value,
          (p) => {
            job.pct = p
            job.detail = `上传中 ${p}%`
          },
        )
        job.phase = 'done'
        job.pct = 100
        job.detail = '已提交解析'
        if (data.task_id) taskIds.push(data.task_id)
      } catch (e) {
        job.phase = 'failed'
        job.detail = '上传失败：' + (e?.response?.data?.detail || e.message)
        hasFail = true
      }
    }
    uploadList.value = []
    if (taskIds.length) startPolling(taskIds)
    await load()
    if (!hasFail) {
      ElMessage.success(`已上传 ${jobs.value.length} 个文件，解析提炼进行中（列表查看进度）`)
      setTimeout(() => { showUpload.value = false; jobs.value = [] }, 1200)  // 稍作停留展示✓后自动关
    } else {
      ElMessage.error('部分文件上传失败，请在弹窗内重试')
    }
  } finally {
    uploading.value = false
  }
}

// 提炼任务轮询：有任务在跑时每 3s 刷新列表与进度，全部结束停止
let pollTimer = null
const runningTasks = new Set()
const taskProgress = ref({})  // taskId -> { progress, detail }
function startPolling(taskIds = []) {
  taskIds.forEach(id => runningTasks.add(id))
  if (pollTimer || !runningTasks.size) return
  pollTimer = setInterval(async () => {
    await load()
    let anyDone = false
    for (const id of [...runningTasks]) {
      try {
        const { data } = await knowledgeTaskStatus(id)
        // data.ref_id 即文档 id，按文档维度记录进度供列表行展示
        taskProgress.value = { ...taskProgress.value, [data.ref_id]: { progress: data.progress, detail: data.detail } }
        if (data.status === 'success' || data.status === 'failed') {
          runningTasks.delete(id)
          anyDone = true
        }
      } catch { runningTasks.delete(id) }
    }
    if (anyDone && !runningTasks.size && pollTimer) {
      clearInterval(pollTimer); pollTimer = null
      await load()
      ElMessage.success('解析提炼完成')
      setTimeout(() => { taskProgress.value = {} }, 4000)
    }
  }, 3000)
}

onMounted(async () => {
  applyTabQuery()
  await loadDomains()
  await loadProjects()
  await load()
  await resumeRunningTasks()
})

// 页面切走再回来：从后端恢复仍在运行的提炼任务的行内进度轮询
async function resumeRunningTasks() {
  try {
    const { data } = await api.get('/knowledge/tasks/running')
    if (!data.length) return
    const ids = []
    for (const t of data) {
      taskProgress.value = { ...taskProgress.value, [t.ref_id]: { progress: t.progress, detail: t.detail } }
      ids.push(t.task_id)
    }
    startPolling(ids)
  } catch { /* 忽略 */ }
}

onUnmounted(() => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
})
</script>

<style scoped>
.knowledge { height: 100%; display: flex; flex-direction: column; padding: 28px 32px; }

.page-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; gap: 16px; }
.head-left { display: flex; align-items: baseline; gap: 14px; }
.page-title { font-size: 20px; font-weight: 700; margin: 0; }
.page-sub { font-size: 13px; color: var(--text-3); }
.head-tools { display: flex; align-items: center; gap: 10px; }
.search { width: 200px; }
.domain-filter { width: 140px; }
.doc-domain { font-size: 13px; color: var(--text-2); }

/* 归属徽标：本体就是入口（点开改），所以要有「可以点」的样子 */
.scope-badge {
  display: inline-flex; align-items: center; gap: 3px; max-width: 100%;
  padding: 2px 8px; border: 1px solid transparent; border-radius: 10px;
  font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  cursor: pointer; transition: border-color 0.15s;
}
.scope-badge:hover { border-color: var(--brand); }
.scope-badge.domain { color: var(--text-2); background: var(--paper-warm); }
.scope-badge.project { color: var(--brand); background: var(--brand-soft); }
.scope-badge.off { color: var(--text-3); background: transparent; border: 1px dashed var(--line); }
.scope-caret { font-style: normal; font-size: 9px; opacity: 0.6; }

/* 上传弹窗的领域/归属选择 */
.up-domain { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.up-domain-label { font-size: 13px; color: var(--text-2); flex-shrink: 0; width: 60px; }
.up-domain-select { width: 160px; }
.up-domain-tip { font-size: 12px; color: var(--text-3); }

/* 修改归属弹窗 */
.sc-doc {
  font-size: 13px; color: var(--text-2); padding: 8px 10px; margin-bottom: 12px;
  background: var(--paper-warm); border-radius: var(--radius-s);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sc-group { display: flex; flex-direction: column; align-items: stretch; gap: 6px; }
.sc-group :deep(.el-radio) {
  height: auto; margin: 0; padding: 10px 12px; align-items: flex-start;
  border: 1px solid var(--line); border-radius: var(--radius-s);
}
.sc-group :deep(.el-radio.is-checked) { border-color: var(--brand); background: var(--brand-soft); }
.sc-group :deep(.el-radio__label) { display: flex; flex-direction: column; gap: 3px; white-space: normal; }
.sc-label { font-size: 14px; color: var(--text); line-height: 1.4; }
.sc-desc { font-size: 12px; color: var(--text-3); line-height: 1.5; }
.sc-proj { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
.plus { font-weight: 400; margin-right: 2px; }

.kb-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); }
.kb-tab {
  display: flex; align-items: center; gap: 7px; padding: 10px 16px; border: none; background: none;
  font-size: 15px; color: var(--text-2); cursor: pointer; position: relative; transition: color 0.15s;
}
.kb-tab:hover { color: var(--text); }
.kb-tab.on { color: var(--brand); font-weight: 600; }
.kb-tab.on::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; background: var(--brand);
}
.kb-tab-count {
  font-size: 12px; padding: 1px 7px; border-radius: 999px; background: var(--paper-warm); color: var(--text-3);
}
.kb-tab.on .kb-tab-count { background: var(--brand-soft); color: var(--brand); }

.kb-tip {
  display: flex; align-items: center; gap: 8px; margin: 14px 0; padding: 9px 14px;
  background: var(--brand-soft); border: 1px solid #d8e4f8; border-radius: var(--radius-s);
  font-size: 13px; color: #33518f;
}
.kb-tip-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--brand); flex-shrink: 0; }

.kb-table-wrap { flex: 1; overflow: hidden; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-m); }
.kb-table { width: 100%; }
.kb-table :deep(.el-table__cell) { font-size: 15px; }   /* 表体字号上调（Element 默认 14px） */
.doc-cell { display: flex; align-items: center; gap: 10px; }
.doc-icon {
  width: 28px; height: 28px; flex-shrink: 0; border-radius: 6px; font-size: 10px; font-weight: 700;
  display: flex; align-items: center; justify-content: center; font-family: var(--font-num);
}
.doc-icon.PDF { background: #fdeaea; color: #c0392b; }
.doc-icon.DOC { background: #e8eefa; color: var(--brand); }
.doc-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.status-badge { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-2); }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-3); }
.status-badge.parsed .status-dot { background: var(--green); }
.status-badge.parsing .status-dot { background: var(--amber); }
.status-badge.pending .status-dot { background: var(--amber); }
.status-badge.failed .status-dot { background: var(--red); }
.status-badge.failed { color: var(--red); }

.page-count { color: var(--text-2); }
.doc-time { font-size: 13px; color: var(--text-3); }
.row-actions { display: flex; gap: 4px; }

/* 解析进度（列表行内） */
.row-progress { display: flex; flex-direction: column; gap: 4px; }
.rp-head { display: flex; align-items: center; justify-content: space-between; }
.rp-pct { font-size: 12px; color: var(--amber); font-weight: 700; }
.rp-bar { height: 3px; background: var(--paper-warm); border-radius: 2px; overflow: hidden; }
.rp-fill { height: 100%; background: linear-gradient(90deg, var(--brand), var(--cyan)); transition: width 0.4s ease-out; }
.rp-detail { font-size: 11px; color: var(--text-3); max-width: 150px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 上传/解析进度（逐文件） */
.upload-progress { margin-top: 12px; padding: 10px 12px; background: var(--paper-warm); border-radius: var(--radius-s); max-height: 260px; overflow-y: auto; }
.job-row { margin-bottom: 10px; }
.job-row:last-child { margin-bottom: 0; }
.up-head { display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: var(--text-2); margin-bottom: 4px; }
.job-name { max-width: 65%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.job-pct.done { color: var(--green); font-weight: 700; }
.job-pct.failed { color: var(--red); font-weight: 700; }
.up-bar { height: 4px; background: var(--line); border-radius: 2px; overflow: hidden; }
.up-fill { height: 100%; background: var(--brand); transition: width 0.2s ease-out; }
.up-fill.done { background: var(--green); }
.up-fill.failed { background: var(--red); }
.up-detail { font-size: 11px; color: var(--text-3); margin-top: 2px; }
.upload-error { margin-top: 10px; padding: 8px 12px; font-size: 13px; color: var(--red); background: #fdf2f2; border: 1px solid #f5c6c6; border-radius: var(--radius-s); }

.dlg-head { display: flex; align-items: center; justify-content: space-between; }
.dlg-title { font-size: 15px; font-weight: 700; }
.dlg-close { border: none; background: none; font-size: 14px; color: var(--text-3); cursor: pointer; padding: 4px 6px; border-radius: 4px; }
.dlg-close:hover { background: var(--paper-warm); color: var(--text); }
.kb-upload :deep(.el-upload-dragger) { padding: 30px 0; }
.upload-hint { text-align: center; }
.upload-icon { font-size: 30px; color: var(--brand); margin-bottom: 6px; }
.upload-text { font-size: 14px; color: var(--text-2); margin: 0 0 4px; }
.upload-sub { font-size: 12px; color: var(--text-3); margin: 0; }
</style>
