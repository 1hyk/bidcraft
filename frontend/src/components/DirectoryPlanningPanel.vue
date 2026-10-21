<template>
  <section class="planning-panel">
    <header class="planning-header"><div><h2>{{ mode === 'settings' ? '编写与排版设置' : '要求与章节对应' }}</h2><p>{{ mode === 'settings' ? '设置文档排版和目标正文页数，生成前统一核对。' : '核对招标要求由哪些章节承接；这里的关联不代表正文已经响应。' }}</p></div></header>
    <div v-if="mode !== 'requirements'" class="planning-settings"><div><small>文档排版</small><strong>{{ styleSummary }}</strong><el-button link type="primary" @click="$emit('style')">修改排版</el-button></div><div><small>目标正文页数</small><strong>{{ targetSummary }}</strong><el-button link type="primary" @click="$emit('pages')">设置篇幅</el-button></div></div>
    <p v-if="mode !== 'requirements' && settingsError" class="settings-error">{{ settingsError }}</p>
    <slot name="outline" />
    <div v-if="mode !== 'settings'" v-loading="loading">
      <el-alert title="这里核对目录是否安排技术要求。章节匹配仅供定位，不代表正文已经响应；正文覆盖情况在“检查与补全”中核验。" type="info" :closable="false" />
      <p>当前标段：{{ data.lot || '未划分标段' }} <el-button :loading="loading" @click="load">刷新对应</el-button></p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <template v-else>
        <el-empty v-if="!requirements.length" description="尚未取得技术要求，请先完成招标要求提取并核对项目理解。" />
        <el-alert v-else-if="!data.has_directory" title="尚未生成目录。招标要求可在“项目理解”中提前查看；生成目录后在此核对章节安排。" type="warning" :closable="false" />
        <template v-else>
          <p>共 {{ requirements.length }} 条技术要求，{{ unmatched.length }} 条尚未确认章节安排。推荐位置不代表正文已响应。</p>
          <el-switch v-model="onlyUnmatched" active-text="只看未安排" />
          <div class="arrangement-actions"><el-button :disabled="!selectedRows.length || batchSaving" @click="preview(selectedRows)">批量确认（{{ selectedRows.length }}）</el-button><el-button type="primary" :disabled="!eligible.length || batchSaving" @click="preview(eligible)">全部采用推荐（{{ eligible.length }}）</el-button><small>保留已确认安排；无推荐的要求单独处理。提交前可增删章节。</small></div>
          <el-alert v-if="saveMessage" :title="saveMessage" type="info" :closable="false" />
          <el-table :data="visibleRequirements" row-key="id" empty-text="没有尚未安排的要求" @selection-change="selectedRows=$event">
            <el-table-column type="selection" width="45" :selectable="canAdopt" />
            <el-table-column label="招标要求与来源" min-width="320"><template #default="{row}"><strong>{{ row.title }}</strong><p class="requirement-content">{{ row.content }}</p><small>{{ row.source_file }} · {{ row.source_location || '未定位' }}</small></template></el-table-column>
            <el-table-column label="章节安排（不代表正文已响应）" min-width="320"><template #default="{row}">
              <template v-if="row.arranged_chapters?.length && !row.arrangement_stale"><strong>已安排</strong><p v-for="n in row.arranged_chapters" :key="n.id"><el-button link type="primary" @click="locate(n.id)">{{ n.title }}</el-button></p></template>
              <template v-else><p v-if="row.arrangement_stale">要求或目录已变化，请重新核对关联。</p><p v-if="!row.chapter_candidates?.length">暂未找到可靠候选，可自行选择章节。</p><div v-if="row.chapter_candidates?.length" class="candidate"><el-button link type="primary" @click="locate(row.chapter_candidates[0].id)">{{ row.chapter_candidates[0].title }}</el-button><p class="candidate-basis">推荐依据：{{ row.chapter_candidates[0].basis }}</p><el-button size="small" @click="preview([row])">采用安排</el-button><details v-if="row.chapter_candidates.length > 1"><summary>查看其他候选（不自动全部关联）</summary><p v-for="n in row.chapter_candidates.slice(1)" :key="n.id"><el-button link @click="locate(n.id)">{{ n.title }}</el-button><small>{{ n.basis }}</small></p></details></div></template>
              <el-button link type="primary" @click="editRow=row.id; selection=(row.arranged_chapters || []).map(n=>n.id)">{{ row.arranged_chapters?.length ? '修改关联' : '自行选择章节' }}</el-button>
              <div v-if="editRow === row.id"><el-select v-model="selection" multiple filterable placeholder="搜索并选择一个或多个章节" style="width:100%"><el-option v-for="n in data.directory_nodes || []" :key="n.id" :label="n.title" :value="n.id" /></el-select><el-button size="small" :loading="saving === row.id" @click="save(row,selection)">保存安排</el-button><el-button size="small" @click="editRow=null">取消</el-button></div>
            </template></el-table-column>
          </el-table>
        </template>
      </template>
    </div>
    <el-dialog v-model="previewOpen" title="确认章节安排" width="min(860px, 94vw)" :close-on-click-modal="!batchSaving" :show-close="!batchSaving">
      <p>默认采用首选推荐。确需多个章节共同承接时可添加；其他候选并非必须同时关联。本操作不代表正文已响应。</p>
      <div v-for="item in batchItems" :key="item.id" class="batch-item"><strong>{{ item.title }}</strong><el-select v-model="item.nodeIds" multiple filterable :disabled="batchSaving" placeholder="选择一个或多个承接章节" style="width:100%"><el-option v-for="n in data.directory_nodes || []" :key="n.id" :label="n.title" :value="n.id" /></el-select></div>
      <template #footer><el-button :disabled="batchSaving" @click="previewOpen=false">取消</el-button><el-button type="primary" :loading="batchSaving" :disabled="!batchItems.length || batchItems.some(i=>!i.nodeIds.length)" @click="submitBatch">确认全部安排（{{ batchItems.length }}）</el-button></template>
    </el-dialog>
  </section>
</template>
<script setup>
import {ref, computed, watch} from 'vue'
import {canAdoptArrangement, arrangementPreview, persistArrangements} from '../utils/arrangementBatch'
import {getUnderstanding, getDocxStyle, getPagePlan, saveRequirementArrangement} from '../api'
const props = defineProps({projectId:[String, Number], refreshKey:Number, mode:{type:String,default:'all'}})
const emit = defineEmits(['locate', 'style', 'pages'])
const style = ref({}), plan = ref({}), settingsError = ref('')
const styleSummary = computed(() => style.value.body_size_pt ? `A4 · 正文 ${style.value.body_size_pt} 磅 · ${style.value.line_spacing || '默认'} 倍行距` : '尚未读取排版设置')
const targetSummary = computed(() => plan.value.target_pages ? `${plan.value.target_pages} 页（允许上下浮动 ${Math.round((plan.value.rewrite_tolerance ?? .15) * 100)}%）` : '未指定目标页数')
const data = ref({}), loading = ref(false), error = ref(''), onlyUnmatched = ref(false)
const requirements = computed(() => (data.value.requirements || []).filter(r => r.display_group === 'technical'))
const unmatched = computed(() => requirements.value.filter(r => !r.arranged_chapters?.length || r.arrangement_stale))
const editRow = ref(null), selection = ref([]), saving = ref(null)
const selectedRows = ref([]), previewOpen = ref(false), batchItems = ref([]), batchSaving = ref(false), saveMessage = ref('')
const canAdopt = canAdoptArrangement
const eligible = computed(() => requirements.value.filter(canAdopt))
function preview(rows) {
  batchItems.value = arrangementPreview(rows)
  previewOpen.value = !!batchItems.value.length
}
async function submitBatch() {
  const projectId = props.projectId
  const items = batchItems.value.map(i=>({...i,nodeIds:[...i.nodeIds]}))
  batchSaving.value = true
  const {completed, failed} = await persistArrangements(items,
    item => saveRequirementArrangement(projectId,item.id,item.nodeIds),
    () => projectId === props.projectId)
  batchSaving.value = false
  if (projectId !== props.projectId) return
  previewOpen.value = false
  await load()
  saveMessage.value = `已保存 ${completed} 条章节安排。` + (failed.length ? `保存失败：${failed.join('、')}，请重试；已成功的安排保留。` : '正文是否响应仍需成稿核验。')
}

async function save(row, ids) {
  const projectId = props.projectId
  saving.value = row.id
  try { await saveRequirementArrangement(projectId,row.id,ids); if (projectId === props.projectId) {editRow.value=null; await load()} }
  catch(e) { error.value=e?.response?.data?.detail || '保存章节安排失败，请重试。' }
  finally { saving.value=null }
}
const visibleRequirements = computed(() => onlyUnmatched.value ? unmatched.value : requirements.value)
let request = 0
async function load() {
  const current = ++request
  loading.value = true
  error.value = ''
  data.value = {}
  selectedRows.value = []
  try {
    const result = await getUnderstanding(props.projectId)
    if (current === request) data.value = result.data
  } catch (e) {
    if (current === request) error.value = e?.response?.data?.detail || '读取章节对应失败，请重试。'
  } finally {
    if (current === request) loading.value = false
  }
}
function locate(id) { emit('locate', id) }
async function loadSettings() {
  settingsError.value = ''
  try { const [a,b] = await Promise.all([getDocxStyle(props.projectId), getPagePlan(props.projectId)]); style.value = a.data.style; plan.value = b.data.plan || {} }
  catch { style.value = {}; plan.value = {}; settingsError.value = '编写设置读取失败，请在开始编写前重新核对。' }
}
watch(() => props.projectId, () => { onlyUnmatched.value = false; editRow.value=null; previewOpen.value=false; saveMessage.value=""; if(props.mode !== 'settings')load(); if(props.mode !== 'requirements')loadSettings() }, {immediate:true})
watch(() => props.refreshKey, () => { if(props.mode !== 'settings')load(); if(props.mode !== 'requirements')loadSettings() })
</script>
<style scoped>
.planning-panel { flex:1; min-width:0; overflow:auto; padding:28px; background:#fafbfe; }
.planning-header { display:flex; justify-content:space-between; gap:16px; align-items:center; }
h2 { margin:0; font-size:21px; color:#20314e; }
.planning-header p { color:#64748b; line-height:1.8; font-size:13px; }
.planning-settings { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin:18px 0 26px; }
.planning-settings > div { background:white; border:1px solid #e1e7f0; border-radius:10px; padding:18px; }
.planning-settings small,.planning-settings strong { display:block; margin-bottom:10px; }
.settings-error { color:#b45309; }
h3 { font-size:16px; }
@media(max-width:900px) { .planning-settings { grid-template-columns:1fr; } .planning-panel { padding:16px; } }
.arrangement-actions { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin:14px 0; }
.batch-item { margin:16px 0; }
.batch-item strong { display:block; margin-bottom:8px; }
.candidate { margin-bottom:16px; padding-bottom:12px; border-bottom:1px solid #e5e7eb; }
.candidate-basis { color:#64748b; font-size:12px; line-height:1.7; }
.requirement-content { white-space: pre-wrap; line-height: 1.8; }
small { color: #64748b; }
</style>
