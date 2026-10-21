<template>
  <div class="home">
    <section class="welcome-panel">
      <div class="welcome-copy">
        <span class="welcome-eyebrow">嘉驰科技咨询 · 技术方案工作台</span>
        <h1>技术方案，从这里开始</h1>
        <p>以招标要求为依据，结合公司资料与历史经验，完成编写、检查与导出。</p>
        <div class="welcome-actions">
          <el-button type="primary" size="large" @click="openCreate">＋ 新建编制项目</el-button>
          <router-link to="/company">管理公司资料 <span>↗</span></router-link>
        </div>
      </div>
      <ol class="welcome-flow" aria-label="方案编制流程">
        <li><span>01</span><div><strong>导入招标文件</strong><small>梳理项目要求与评分重点</small></div></li>
        <li><span>02</span><div><strong>确认目录与编写</strong><small>选择排版，设置目标页数</small></div></li>
        <li><span>03</span><div><strong>检查并导出</strong><small>核对响应内容，完善定稿</small></div></li>
      </ol>
    </section>
    <!-- 页头 -->
    <header class="page-head">
      <div class="head-left">
        <h2 class="page-title">我的项目</h2>
        <span class="page-count num">{{ projects.length }} 个项目</span>
      </div>
      <div class="head-tools">
        <el-input
          v-model="keyword"
          placeholder="搜索项目…"
          clearable
          class="search"
          @input="load"
        >
          <template #prefix>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          </template>
        </el-input>
        <el-select v-model="domainFilter" placeholder="全部领域" clearable class="type-select" @change="changeDomainFilter">
          <el-option v-for="d in domains" :key="d.key" :label="d.name" :value="d.key" />
        </el-select>
        <el-select v-model="typeFilter" placeholder="全部服务类型" title="仅筛选项目已保存的类型；不含尚未采用的 AI 建议" clearable class="type-select" @change="applyFilter">
          <el-option v-for="t in filterTypeOptions" :key="t" :label="t" :value="t" />
        </el-select>
        <el-dropdown trigger="click" @command="sortBy">
          <button class="sort-btn">
            {{ sortLabel }}
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"/></svg>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="updated_desc">最近更新</el-dropdown-item>
              <el-dropdown-item command="updated_asc">最早更新</el-dropdown-item>
              <el-dropdown-item command="name">按名称</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <!-- 卡片墙 -->
    <main class="card-area">
      <div v-if="!loading && filtered.length === 0" class="empty-wrap">
        <div class="empty-card">
          <div class="empty-icon">◈</div>
          <p class="empty-title">{{ keyword || domainFilter || typeFilter ? '未找到匹配项目' : '还没有项目' }}</p>
          <p class="empty-desc">{{ keyword || domainFilter || typeFilter ? '试试其他关键词，或调整筛选条件。' : '新建项目后，上传招标文件即可开始编制。' }}</p>
          <el-button type="primary" @click="openCreate">新建第一个项目</el-button>
        </div>
      </div>

      <div v-else class="card-grid">
        <div v-for="(p, i) in filtered" :key="p.id" class="project-card" role="link" tabindex="0" :aria-label="'进入项目：'+p.name" @click="enter(p.id)" @keydown.enter.self="enter(p.id)">
          <div class="card-top">
            <div class="card-badge" :title="p.project_types?.join('、') || '未设置服务类型'" :style="{ background: badgeBg(p.id), color: badgeColor(p.id) }">
              {{ p.project_types?.join('、') || '未设置服务类型' }}
            </div>
            <div class="card-top-right">
              <span class="card-no num">№ {{ String(p.id).padStart(3, '0') }}</span>
              <el-dropdown trigger="click" @command="(c) => onCardMenu(c, p)" @click.stop>
                <button class="more-btn" :aria-label="p.name+'的项目操作'" @click.stop>⋯</button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="settings">项目设置</el-dropdown-item>
                    <el-dropdown-item command="delete" divided>删除项目</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
          <div class="card-title">
            {{ p.name }}
            <span class="card-domain">{{ domainName(p.domain) }}</span>
          </div>
          <div class="card-desc" v-if="p.description">{{ p.description }}</div>
          <!-- 标段数据：多标段显示代码范围/数量，单标段显示代码，无标段显示"全线" -->
          <div class="card-lots" v-if="p.lots?.length || p.id">
            <span class="lot-icon">⬒</span>
            <template v-if="p.lots?.length">
              <span class="lot-text" :title="p.lots.map(l => l.lot_code + ' ' + l.lot_name).join('\n')">
                {{ lotSummary(p.lots) }}
              </span>
              <span v-if="p.lots.some(l => l.selected)" class="lot-selected">
                已选 {{ p.lots.find(l => l.selected).lot_code }}
              </span>
            </template>
            <span v-else-if="p.has_parsed_tender" class="lot-text">招标文件已解析 · 无标段划分</span>
            <span v-else-if="p.has_tender" class="lot-text muted">已上传招标文件 · 待完成解析</span>
            <span v-else class="lot-text muted">未上传招标文件</span>
          </div>
          <div class="card-foot">
            <span class="card-time">{{ formatTime(p.updated_at) }}</span>
            <span class="card-arrow">打开工作台 →</span>
          </div>
          <div class="card-accent" :style="{ background: badgeColor(p.id) }" />
        </div>
      </div>
    </main>

    <!-- 新建项目向导（M1-2：基本信息 → 上传招标文件 → 标段识别与选择 → 进工作台） -->
    <el-dialog v-model="showCreate" width="560" class="create-dialog" :show-close="false" :close-on-click-modal="false">
      <template #header>
        <div class="dlg-head">
          <span class="dlg-title">新建项目</span>
          <button class="dlg-close" @click="closeWizard">✕</button>
        </div>
      </template>

      <el-steps :active="wizStep" align-center class="wiz-steps">
        <el-step title="基本信息" />
        <el-step title="招标文件" />
        <el-step title="标段确认" />
      </el-steps>

      <!-- 第 1 步：基本信息 -->
      <el-form v-if="wizStep === 0" label-position="top" class="create-form">
        <el-form-item label="项目名称" required>
          <el-input v-model="form.name" placeholder="如：XX 项目技术方案编制服务" size="large" />
        </el-form-item>
        <el-form-item label="应用行业">
          <el-select v-model="form.domain" size="large" class="domain-select">
            <el-option v-for="d in domains" :key="d.key" :label="d.name" :value="d.key" />
          </el-select>
          <p class="domain-hint">{{ domainHelp(form.domain) }}</p>
        </el-form-item>
        <el-collapse><el-collapse-item title="补充业务信息（可选，不填也能编制）">
        <el-form-item label="服务类型（可多选，可留空）">
          <el-select v-model="form.project_types" multiple filterable allow-create default-first-option style="width:100%" placeholder="选预设分类或输入自定义类型，可留空"><el-option v-for="t in formTypes" :key="t" :value="t" :label="t" /></el-select>
          <p class="domain-hint">只提示业务侧重点，不隔离素材；最终编制范围以招标要求为准。</p>
        </el-form-item>
        <el-form-item label="本次服务内容"><el-input v-model="form.service_content" maxlength="2000" placeholder="招标中的具体业务，可解析后核对填写" /></el-form-item>
        <el-form-item label="项目描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="标段范围、工期、主要工程量等（可选）" />
        </el-form-item>
        </el-collapse-item></el-collapse>
      </el-form>

      <!-- 第 2 步：上传招标文件 -->
      <div v-else-if="wizStep === 1" class="wiz-body">
        <el-upload
          v-if="!wizTask.active"
          drag :show-file-list="false" accept=".pdf,.docx"
          :before-upload="wizUpload" class="wiz-upload"
        >
          <div class="wiz-upload-icon">⇪</div>
          <p>拖拽招标文件到此处，或点击上传（PDF / Word）</p>
          <p class="wiz-upload-sub">上传后 AI 自动解析，识别标段划分并抽取关键事实</p>
        </el-upload>
        <div v-else class="wiz-parsing">
          <div class="ftask-head">
            <span>{{ wizTask.detail || '解析中…' }}</span>
            <span class="num">{{ wizTask.progress }}%</span>
          </div>
          <el-progress :percentage="wizTask.progress" :show-text="false" />
          <p class="wiz-upload-sub">大文件解析需要几分钟，请稍候</p>
        </div>
      </div>

      <!-- 第 3 步：标段识别结果（三种形态） -->
      <div v-else-if="wizStep === 2" class="wiz-body">
        <div v-if="lotsLoading" class="wiz-parsing"><p>正在读取标段识别结果…</p></div>
        <template v-else-if="lots.length > 1">
          <p class="wiz-lot-tip">识别到 <b>{{ lots.length }}</b> 个标段，请选择本项目要投标的标段：</p>
          <div class="lot-list">
            <div
              v-for="l in lots" :key="l.id"
              class="lot-card" :class="{ on: selectedLotId === l.id }"
              @click="selectedLotId = l.id"
            >
              <div class="lot-code">{{ l.lot_code }}</div>
              <div class="lot-name">{{ l.lot_name || '—' }}</div>
              <div v-if="l.scope" class="lot-scope">{{ l.scope }}</div>
              <div v-if="l.price_limit" class="lot-price num">限价 {{ l.price_limit }} 万元</div>
            </div>
          </div>
        </template>
        <template v-else-if="lots.length === 1">
          <p class="wiz-lot-tip">本项目为<b>单标段</b>，请确认：</p>
          <div class="lot-card on">
            <div class="lot-code">{{ lots[0].lot_code }}</div>
            <div class="lot-name">{{ lots[0].lot_name || '—' }}</div>
            <div v-if="lots[0].scope" class="lot-scope">{{ lots[0].scope }}</div>
          </div>
        </template>
        <template v-else>
          <div class="wiz-parsing">
            <p>未识别到标段划分，本项目按整体编制。</p>
            <p class="wiz-upload-sub">（以招标文件实际内容为准，也可稍后在工作中重新上传招标文件）</p>
          </div>
        </template>
      </div>

      <div v-else class="wiz-body">
        <p>根据当前标段的已解析招标内容给出建议，请确认或修改；不会自动更换应用行业。</p>
        <p v-if="classificationError">{{ classificationError }}，可留空，稍后在项目设置中修改。</p>
        <el-form label-position="top">
          <el-form-item label="服务类型（可多选）">
            <el-select v-model="wizTypes" multiple filterable allow-create default-first-option style="width:100%"><el-option v-for="t in serviceChoices(commonTypes, wizTypes)" :key="t" :value="t" :label="t" /></el-select>
          </el-form-item>
          <el-form-item label="测试或服务对象（可增删）">
            <el-select v-model="wizObjects" multiple filterable allow-create default-first-option style="width:100%"><el-option v-for="o in objectOptions" :key="o" :label="o" :value="o" /></el-select>
          </el-form-item>
        </el-form>
        <p v-for="e in suggestionEvidence" :key="e.type" class="domain-hint">建议 {{ e.type }}：{{ e.excerpt }}</p>
      </div>

      <template #footer>
        <template v-if="wizStep === 0">
          <el-button @click="closeWizard">取消</el-button>
          <el-button type="primary" :loading="creating" @click="create">创建项目，下一步 →</el-button>
        </template>
        <template v-else-if="wizStep === 1">
          <el-button :disabled="wizTask.active" @click="enterWorkspace">跳过，直接进入工作台</el-button>
        </template>
        <template v-else-if="wizStep === 2">
          <el-button type="primary" :loading="classifying" :disabled="lots.length > 1 && !selectedLotId" @click="confirmLot">进入工作台 →</el-button>
        </template>
        <template v-else>
          <el-button type="primary" :loading="saving" @click="confirmClassification">确认业务，进入工作台 →</el-button>
        </template>
      </template>
    </el-dialog>

    <!-- 项目设置弹窗 -->
    <el-dialog v-model="showSettings" width="520" class="create-dialog" :show-close="false">
      <template #header>
        <div class="dlg-head">
          <span class="dlg-title">项目设置</span>
          <button class="dlg-close" @click="showSettings = false">✕</button>
        </div>
      </template>
      <el-form label-position="top" class="create-form">
        <el-form-item label="项目名称" required>
          <el-input v-model="editForm.name" size="large" />
        </el-form-item>
        <el-form-item label="应用行业">
          <el-select v-model="editForm.domain" class="domain-select">
            <el-option v-for="d in domains" :key="d.key" :label="d.name" :value="d.key" />
          </el-select>
          <p class="domain-hint">{{ domainHelp(editForm.domain) }} 修改领域会影响后续素材检索，不改已有正文。</p>
        </el-form-item>
        <el-form-item label="服务类型（可多选，可留空）">
          <el-select v-model="editForm.project_types" multiple filterable allow-create default-first-option style="width:100%" placeholder="选预设分类或输入自定义类型，可留空"><el-option v-for="t in editFormTypes" :key="t" :value="t" :label="t" /></el-select>
          <p v-if="editingLegacy" class="domain-hint">原分类“{{ editingLegacy }}”已保留历史记录，请按业务重新选择服务类型。</p>
        </el-form-item>
        <el-form-item label="本次服务内容"><el-input v-model="editForm.service_content" maxlength="2000" placeholder="招标中的具体业务，可解析后核对填写" /></el-form-item>
        <el-form-item label="测试或服务对象（可增删）">
          <el-select v-model="editForm.technical_objects" multiple filterable allow-create default-first-option style="width:100%"><el-option v-for="o in editForm.technical_objects" :key="o" :label="o" :value="o" /></el-select>
          <el-button link :loading="classifying" @click="suggestForSettings">从当前标段招标内容获取建议</el-button>
          <div v-if="settingsSuggestion" class="domain-hint">
            <p>建议具体业务：{{ settingsSuggestion.service_content || '未能识别' }}。已有服务内容不自动覆盖，请核对后保存。</p>
            <p v-for="w in settingsSuggestion.warnings || []" :key="w">{{ w }}</p>
            <details v-for="e in [...(settingsSuggestion.evidence || []), ...(settingsSuggestion.object_evidence || [])]" :key="e.source_id + e.name"><summary>{{ e.name }} · 查看原文依据</summary><p>{{ e.excerpt }}</p><small>{{ e.source_file }} · {{ e.source_location }}</small></details>
          </div>
        </el-form-item>
        <el-form-item label="项目描述">
          <el-input v-model="editForm.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showSettings = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveSettings">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref, reactive, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listProjects, createProject, updateProject, deleteProject,
  serviceTypeOptions, listDomains, uploadFile, taskStatus, listLots, selectLot, classificationSuggestions } from '../api'

import {serviceChoices, existingServiceTypes, classificationImpact} from '../serviceTypes'
const commonTypes = ref([])
const router = useRouter()
const projects = ref([])
const keyword = ref('')
const typeFilter = ref('')
// 领域（/api/domains 注册表）：项目归属决定它匹配哪个领域的知识库
const domains = ref([])
const defaultDomain = ref('')
const domainFilter = ref('')
const domainName = (key) => domains.value.find(d => d.key === key)?.name || key || ''
const sort = ref('updated_desc')
const loading = ref(false)
const showCreate = ref(false)
const showSettings = ref(false)
const creating = ref(false)
const saving = ref(false)
const editingId = ref(null)
const form = reactive({ name: '', description: '', project_types: [], technical_objects: [], service_content: '', domain: '' })
const editForm = reactive({ name: '', description: '', project_types: [], technical_objects: [], service_content: '', domain: '' })
const editingLegacy = ref('')
const classifying = ref(false), classificationError = ref(''), settingsSuggestion = ref(null)
const wizTypes = ref([]), wizObjects = ref([]), objectOptions = ref([]), suggestionEvidence = ref([]), wizDomain = ref('')
const domainHelp = key => domains.value.find(d => d.key === key)?.description || '应用行业决定素材范围，服务类型描述具体业务。'
function toggleType(values, value) { const i = values.indexOf(value); if (i < 0) values.push(value); else values.splice(i, 1) }

const SORT_LABEL = { updated_desc: '最近更新', updated_asc: '最早更新', name: '按名称' }
const sortLabel = computed(() => SORT_LABEL[sort.value] || '排序')

// 选择器使用共同分类，筛选器只列已保存的实际类型。
const filterTypeOptions = computed(() => existingServiceTypes(projects.value, domainFilter.value))
function changeDomainFilter() {
  if (typeFilter.value && !filterTypeOptions.value.includes(typeFilter.value)) typeFilter.value = ''
}
const formTypes = computed(() => serviceChoices(commonTypes.value, form.project_types))
const editFormTypes = computed(() => serviceChoices(commonTypes.value, editForm.project_types))

const filtered = computed(() => {
  let list = [...projects.value]
  if (domainFilter.value) list = list.filter(p => p.domain === domainFilter.value)
  if (typeFilter.value) list = list.filter(p => p.project_types?.includes(typeFilter.value))
  if (sort.value === 'name') list.sort((a, b) => (a.name || '').localeCompare(b.name || '', 'zh'))
  else if (sort.value === 'updated_asc') list.sort((a, b) => new Date(a.updated_at) - new Date(b.updated_at))
  else list.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
  return list
})

async function load() {
  loading.value = true
  try {
    const { data } = await listProjects(keyword.value)
    projects.value = data
  } finally {
    loading.value = false
  }
}

function applyFilter() { /* 由 computed filtered 处理 */ }
function sortBy(cmd) { sort.value = cmd }

function openCreate() {
  // 未明确行业先归通用，不继承低空示例配置或上个项目的业务选择。
  form.domain = defaultDomain.value
  form.project_types = []
  form.service_content = ''
  wizObjects.value = []; objectOptions.value = []; suggestionEvidence.value = []; classificationError.value = ''
  wizStep.value = 0
  wizProjectId.value = null
  wizTask.value = { active: false, progress: 0, detail: '' }
  lots.value = []
  selectedLotId.value = null
  showCreate.value = true
}

// ---------- 新建项目向导 ----------
const wizStep = ref(0)
const wizProjectId = ref(null)
const wizTask = ref({ active: false, progress: 0, detail: '' })
const lots = ref([])
const lotsLoading = ref(false)
const selectedLotId = ref(null)
let wizTimer = null

async function create() {
  if (!form.name.trim()) return ElMessage.warning('请填写项目名称')
  creating.value = true
  try {
    const { data } = await createProject({ ...form })
    wizProjectId.value = data.id
    wizDomain.value = form.domain
    wizTypes.value = [...form.project_types]
    wizStep.value = 1
    form.name = ''
    form.description = ''
    form.project_types = []
    form.service_content = ''
    form.technical_objects = []
    form.domain = defaultDomain.value  // 清表单但保留默认领域，下次新建仍是它
    ElMessage.success('项目已创建，请上传招标文件')
    load()
  } finally {
    creating.value = false
  }
}

async function wizUpload(file) {
  // 向导里明确是招标文件（不走工作台按文件名猜测的逻辑）
  wizTask.value = { active: true, progress: 0, detail: `上传中：${file.name}` }
  try {
    const { data } = await uploadFile(wizProjectId.value, file, 'tender', (p) => {
      wizTask.value = { active: true, progress: p, detail: `上传中：${file.name}` }
    })
    if (data.task_id) pollWizTask(data.task_id)
    else await finishParse()
  } catch (e) {
    wizTask.value.active = false
    ElMessage.error('上传失败：' + (e?.response?.data?.detail || e.message))
  }
  return false
}

function pollWizTask(taskId) {
  if (wizTimer) clearInterval(wizTimer)
  wizTimer = setInterval(async () => {
    try {
      const { data } = await taskStatus(taskId)
      wizTask.value = { active: data.status === 'running', progress: data.progress, detail: data.detail || '' }
      if (data.status === 'success') {
        clearInterval(wizTimer); wizTimer = null
        await finishParse()
      } else if (data.status === 'failed') {
        clearInterval(wizTimer); wizTimer = null
        wizTask.value.active = false
        ElMessage.error('解析失败：' + (data.detail || '未知错误'))
      }
    } catch { /* ignore */ }
  }, 3000)
}

async function finishParse() {
  wizTask.value.active = false
  lotsLoading.value = true
  wizStep.value = 2
  try {
    const { data } = await listLots(wizProjectId.value)
    lots.value = data.lots || []
    if (lots.value.length === 1) selectedLotId.value = lots.value[0].id
  } finally {
    lotsLoading.value = false
  }
}

async function confirmLot() {
  classifying.value = true
  try {
    await selectLot(wizProjectId.value, lots.value.length > 1 ? selectedLotId.value : (lots.value[0]?.id ?? null))
    enterWorkspace()
  } catch (e) { ElMessage.error('标段选择失败：' + (e?.response?.data?.detail || e.message)) }
  finally { classifying.value = false }
}

async function confirmClassification() {
  saving.value = true
  try {
    await updateProject(wizProjectId.value, { project_types: wizTypes.value, technical_objects: wizObjects.value })
    enterWorkspace()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || e.message) }
  finally { saving.value = false }
}

async function suggestForSettings() {
  const current = projects.value.find(p => p.id === editingId.value)
  if (current && current.domain !== editForm.domain) return ElMessage.info('请先保存新的应用行业，再获取该领域的业务建议')
  classifying.value = true
  try {
    const { data } = await classificationSuggestions(editingId.value)
    settingsSuggestion.value = data
    if (!editForm.service_content) editForm.service_content = data.service_content || ''
    editForm.project_types = [...new Set([...editForm.project_types, ...(data.project_types || [])])]
    editForm.technical_objects = [...new Set([...editForm.technical_objects, ...(data.technical_objects || [])])]
    ElMessage.info('建议已加入表单，可增删；保存后生效')
  } catch (e) { ElMessage.error(e?.response?.data?.detail || e.message) }
  finally { classifying.value = false }
}

function enterWorkspace() {
  cleanupWizard()
  router.push(`/workspace/${wizProjectId.value}`)
}

function closeWizard() {
  cleanupWizard()
  showCreate.value = false
}

function cleanupWizard() {
  if (wizTimer) { clearInterval(wizTimer); wizTimer = null }
}

function onCardMenu(cmd, p) {
  if (cmd === 'settings') {
    settingsSuggestion.value = null
    editingId.value = p.id
    editForm.name = p.name || ''
    editForm.project_types = [...(p.project_types || [])]
    editForm.technical_objects = [...(p.technical_objects || [])]
    editForm.service_content = p.service_content || ''
    editingLegacy.value = p.legacy_project_type || ''
    editForm.description = p.description || ''
    editForm.domain = p.domain || defaultDomain.value
    showSettings.value = true
  } else if (cmd === 'delete') {
    ElMessageBox.confirm(`确定删除项目「${p.name}」？该操作不可恢复。`, '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    }).then(async () => {
      await deleteProject(p.id)
      ElMessage.success('已删除')
      await load()
    }).catch(() => {})
  }
}

async function saveSettings() {
  if (!editForm.name.trim()) return ElMessage.warning('请填写项目名称')
  saving.value = true
  try {
    await updateProject(editingId.value, { ...editForm })
    ElMessage.info(classificationImpact(projects.value.find(p => p.id === editingId.value) || {}, editForm))
    showSettings.value = false
    await load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

const enter = (id) => router.push(`/workspace/${id}`)

const PALETTE = [
  ['#2f5fd0', '#e8eefa'], ['#0e9f6e', '#e3f5ee'], ['#d97706', '#fdf3e3'],
  ['#7c5cd0', '#f0eafa'], ['#c94f6d', '#fbe9ee'],
]
const badgeColor = (id) => PALETTE[id % PALETTE.length][0]
const badgeBg = (id) => PALETTE[id % PALETTE.length][1]

const formatTime = (t) => (t ? new Date(t).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '')
// 标段摘要：单标段显示代码；连续编号（XX-1~XX-10）显示范围；其余显示数量
function lotSummary(lots) {
  if (lots.length === 1) return lots[0].lot_code
  const codes = lots.map(l => l.lot_code)
  const parts = codes.map(c => c.match(/^(.*?)(\d+)$/))
  if (parts.every(x => x) && new Set(parts.map(x => x[1])).size === 1) {
    const nums = parts.map(x => parseInt(x[2]))
    return `${parts[0][1]}${Math.min(...nums)}~${Math.max(...nums)}（${lots.length} 个标段）`
  }
  return `共 ${lots.length} 个标段`
}

async function loadDomains() {
  try {
    const { data } = await listDomains()
    domains.value = data.items || []
    defaultDomain.value = domains.value.some(d => d.key === 'softtest') ? 'softtest' : ''
  } catch (e) { /* 领域清单取不到不影响项目列表 */ }
}

onMounted(async () => {
  try { commonTypes.value = (await serviceTypeOptions()).data.items } catch { ElMessage.warning('预设服务类型加载失败，仍可自行输入') }
  await loadDomains()
  form.domain = defaultDomain.value
  await load()
})
</script>

<style scoped>
.home { height: 100%; display: flex; flex-direction: column; padding: 28px 32px; }
.home { height: auto; min-height: 100%; background: #f5f7fb; padding: 32px 40px; }
.welcome-panel { display:flex; justify-content:space-between; gap:40px; padding:32px 36px; margin-bottom:32px; border:1px solid #dfe6f0; border-radius:18px; background:#fff; box-shadow:0 4px 24px #203a6010; }
.welcome-eyebrow { display:block; color:#526785; font-size:12px; font-weight:600; letter-spacing:.08em; }
.welcome-copy h1 { margin:16px 0 12px; font-size:30px; letter-spacing:-.03em; color:#172c4c; line-height:1.3; }
.welcome-copy p { max-width:490px; margin:0; font-size:13px; color:#68778c; line-height:1.9; }
.welcome-actions { display:flex; align-items:center; flex-wrap:wrap; gap:22px; margin-top:24px; }
.welcome-actions a { color:#526785; text-decoration:none; font-size:13px; }
.welcome-actions a:hover { color:var(--brand); }
.welcome-actions a span { margin-left:10px; }
.welcome-flow { list-style:none; margin:0; padding:0 0 0 30px; border-left:1px solid #e6ebf2; min-width:240px; display:flex; flex-direction:column; justify-content:center; gap:22px; }
.welcome-flow li { display:flex; align-items:center; gap:14px; }
.welcome-flow li>span { color:#466daa; border:1px solid #dce6f6; background:#f5f8fd; padding:8px; border-radius:10px; font-size:12px; font-family:var(--font-num); }
.welcome-flow strong { display:block; font-size:13px; color:#263d5e; font-weight:600; }
.welcome-flow small { display:block; color:#8993a4; font-size:11px; margin-top:5px; }

/* 页头 */
.page-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 22px; gap: 16px; }
.head-left { display: flex; align-items: baseline; gap: 14px; }
.page-title { font-size: 20px; font-weight: 700; margin: 0; letter-spacing: 0.01em; }
.page-count { font-size: 12px; color: var(--text-3); }
.head-tools { display: flex; align-items: center; gap: 10px; flex-wrap:wrap; }
.search { width: 200px; }
.type-select { width: 110px; }
.sort-btn {
  display: inline-flex; align-items: center; gap: 5px; padding: 8px 12px;
  border: 1px solid var(--line-strong); border-radius: var(--radius-s);
  background: var(--card); color: var(--text-2); font-size: 13px; cursor: pointer;
  transition: all 0.15s;
}
.sort-btn:hover { border-color: var(--brand); color: var(--brand); }
.btn-new { font-weight: 600; }
.plus { font-weight: 400; margin-right: 2px; }

/* 卡片墙 */
.card-area { flex: 1; }
.card-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 18px;
}
.project-card {
  position: relative; background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius-m); padding: 18px 20px 16px; cursor: pointer;
  transition: border-color 0.18s ease-out, box-shadow 0.18s ease-out, transform 0.18s ease-out;
  overflow: hidden;
}
.project-card:hover {
  border-color: var(--line-strong); box-shadow: var(--shadow-pop); transform: translateY(-2px);
}
.project-card { border-color:#e0e6ef; border-radius:14px; min-height:180px; display:flex; flex-direction:column; box-shadow:0 2px 5px #233c5b04; }
.project-card:hover,.project-card:focus-visible { border-color:#a8bfdf; box-shadow:0 8px 24px #233c5b0b; outline:2px solid transparent; }
.project-card:focus-visible { outline-color:var(--brand); outline-offset:3px; }
.project-card .card-accent { display:none; }
.project-card .card-foot { margin-top:auto; padding-top:14px; }
.project-card .card-title { color:#243855; font-size:16px; margin-bottom:12px; }
.project-card .card-desc { margin-bottom:14px; }
.project-card .card-arrow { opacity:1; font-size:11px; }
.project-card .card-lots { margin-bottom:16px; }
.project-card .card-badge { min-width:0; max-width:calc(100% - 78px); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.project-card .card-top-right { flex-shrink:0; }
.card-accent { position: absolute; left: 0; top: 0; bottom: 0; width: 3px; opacity: 0.85; }
.card-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.card-top-right { display: flex; align-items: center; gap: 8px; }
.card-badge {
  font-size: 11px; font-weight: 600; padding: 3px 9px; border-radius: 4px; letter-spacing: 0.04em;
}
.card-no { font-size: 11px; color: var(--text-3); }
.more-btn {
  border: none; background: none; color: var(--text-3); font-size: 16px; cursor: pointer;
  padding: 2px 6px; border-radius: 4px; line-height: 1;
}
.more-btn:hover { background: var(--paper-warm); color: var(--text); }
.card-title { font-size: 15px; font-weight: 600; line-height: 1.45; margin-bottom: 6px; }
.card-desc {
  font-size: 12px; color: var(--text-3); line-height: 1.6; margin-bottom: 10px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.card-lots {
  display: flex; align-items: center; gap: 6px; margin-bottom: 4px;
  font-size: 11px; color: var(--text-2);
}
.lot-icon { color: var(--brand); font-size: 12px; }
.lot-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lot-text.muted { color: var(--text-3); }
.lot-selected {
  flex-shrink: 0; font-size: 10px; padding: 1px 7px; border-radius: 999px;
  background: var(--brand-soft); color: var(--brand); font-weight: 600;
}
.card-foot {
  display: flex; align-items: center; justify-content: space-between;
  margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line);
}
.card-time { font-size: 11px; color: var(--text-3); }
.card-arrow { font-size: 12px; color: var(--brand); font-weight: 500; opacity: 0; transition: opacity 0.18s; }
.project-card:hover .card-arrow { opacity: 1; }

/* 空状态 */
.empty-wrap { display: flex; justify-content: center; padding: 72px 0; }
.empty-card {
  text-align: center; padding: 48px 64px; background: var(--card);
  border: 1px dashed var(--line-strong); border-radius: var(--radius-m);
}
.empty-icon { font-size: 32px; color: var(--brand); margin-bottom: 12px; }
.empty-title { font-size: 16px; font-weight: 600; margin: 0 0 6px; }
.empty-desc { font-size: 13px; color: var(--text-3); margin: 0 0 20px; }

/* 弹窗 */
.dlg-head { display: flex; align-items: center; justify-content: space-between; }
.dlg-title { font-size: 15px; font-weight: 700; }
.dlg-close {
  border: none; background: none; font-size: 14px; color: var(--text-3);
  cursor: pointer; padding: 4px 6px; border-radius: 4px;
}
.dlg-close:hover { background: var(--paper-warm); color: var(--text); }
.create-form :deep(.el-form-item__label) { font-weight: 600; font-size: 13px; padding-bottom: 6px; }
.domain-select { width: 100%; }
.domain-hint { font-size: 12px; color: var(--text-3); margin: 6px 0 0; line-height: 1.5; }
.card-domain {
  display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px;
  background: var(--brand-soft); color: var(--brand); font-size: 11px;
  font-weight: 500; vertical-align: 2px;
}
.type-picker { display: flex; gap: 8px; flex-wrap: wrap; }
.type-chip {
  padding: 7px 16px; border-radius: 999px; border: 1px solid var(--line-strong);
  background: var(--card); font-size: 13px; color: var(--text-2); cursor: pointer;
  transition: all 0.15s ease-out;
}
.type-chip:hover { border-color: var(--brand); color: var(--brand); }
.type-chip.on { background: var(--brand); border-color: var(--brand); color: #fff; font-weight: 600; }

/* 新建项目向导 */
.wiz-steps { margin-bottom: 22px; }
.wiz-body { min-height: 240px; }
.wiz-upload { width: 100%; }
.wiz-upload :deep(.el-upload-dragger) { padding: 36px 20px; }
.wiz-upload-icon { font-size: 30px; color: var(--brand); margin-bottom: 8px; }
.wiz-upload p { margin: 4px 0; font-size: 13px; }
.wiz-upload-sub { color: var(--text-3); font-size: 12px !important; }
.wiz-parsing { padding: 40px 16px; text-align: center; font-size: 13px; color: var(--text-2); }
.wiz-parsing .el-progress { margin: 14px 0 8px; }
.wiz-parsing .num { color: var(--brand); font-weight: 700; }
.ftask-head { display: flex; justify-content: space-between; font-size: 12px; }
.wiz-lot-tip { font-size: 13px; color: var(--text-2); margin: 0 0 12px; }
.lot-list { display: flex; flex-direction: column; gap: 10px; max-height: 320px; overflow-y: auto; }
.lot-card {
  border: 1px solid var(--line-strong); border-radius: var(--radius-m);
  padding: 12px 14px; cursor: pointer; transition: all 0.15s;
}
.lot-card:hover { border-color: var(--brand); }
.lot-card.on { border-color: var(--brand); background: var(--brand-soft); box-shadow: 0 0 0 1px var(--brand); }
.lot-code { font-size: 14px; font-weight: 700; }
.lot-name { font-size: 12px; color: var(--text-2); margin-top: 2px; }
.lot-scope { font-size: 12px; color: var(--text-3); margin-top: 6px; line-height: 1.6; }
.lot-price { font-size: 12px; color: var(--amber); margin-top: 6px; font-weight: 600; }
@media (max-width:1200px) {
  .home { padding:24px; }
  .page-head { align-items:flex-start; flex-direction:column; }
  .head-tools { width:100%; }
  .welcome-panel { padding:28px; gap:24px; }
  .welcome-copy h1 { font-size:26px; }
}
@media (max-width:800px) {
  .home { padding:20px 16px; }
  .welcome-panel { flex-direction:column; padding:24px; margin-bottom:24px; }
  .welcome-flow { border-left:0; border-top:1px solid #e6ebf2; padding:20px 0 0; min-width:0; }
  .search { width:100%; }
  .card-grid { grid-template-columns:1fr; }
}
</style>
