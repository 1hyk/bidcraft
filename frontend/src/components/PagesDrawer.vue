<template>
  <el-drawer
    :model-value="modelValue"
    @update:model-value="(v) => emit('update:modelValue', v)"
    direction="rtl"
    size="min(780px, 94vw)"
    :with-header="false"
    class="pg-drawer"
  >
    <div class="pg-wrap">
      <div class="pg-head">
        <div>
          <div class="pg-title">篇幅控制</div>
          <div class="pg-sub">
            按目标<strong>正文页数</strong>（不含封面与目录，与导出稿页脚的数字同口径）
            反推全书该写多少字，再按章节权重分摊到各章，生成时写进该章的篇幅要求
          </div>
        </div>
        <button class="pg-close" @click="emit('update:modelValue', false)">✕</button>
      </div>

      <div v-if="loading" class="pg-empty">加载中…</div>

      <div v-else class="pg-body">
        <p class="pg-hint">预估按已保存的正文字号、行距和页边距计算；修改排版后请先保存。仅更换字体名称、标题或页眉时，估算值可能不变，实际页数以测量为准。</p>
        <div class="pg-top">
          <div class="pg-field">
            <label>目标正文页数</label>
            <el-input-number
              v-model="form.target_pages" :min="range[0]" :max="range[1]" :step="10"
              :controls="false" size="large" class="pg-num" placeholder="不限"
            />
            <p class="pg-hint">先保存排版，再设目标页数。允许约 ±15% 浮动；留空则不限篇幅。</p>
          </div>

          <div class="pg-card">
            <div class="pg-card-row">
              <span>当前排版的「字/页」</span>
              <b class="num">{{ coeff.value }}</b>
            </div>
            <div class="pg-card-row">
              <span>系数来源</span>
              <b>
                <el-tag :type="coeff.source === 'measured' ? 'success' : 'warning'" size="small" effect="plain">
                  {{ coeff.source === 'measured' ? '实测' : '估算' }}
                </el-tag>
              </b>
            </div>
            <div class="pg-card-row">
              <span>全书目标字数</span>
              <b class="num">{{ total ? fmt(total) : '—' }}</b>
            </div>
            <div class="pg-card-row">
              <span>已写 / 目标</span>
              <b class="num">
                {{ fmt(stats.words) }}<template v-if="total"> / {{ fmt(total) }}</template>
              </b>
            </div>
            <div class="pg-card-row">
              <span>当前正文估算页数</span>
              <b class="num">{{ estimatedPages }} 页</b>
            </div>
            <div v-if="form.target_pages" class="pg-card-row">
              <span>期望页数范围（±15%）</span>
              <b class="num">{{ Math.ceil(form.target_pages * 0.85) }}–{{ Math.floor(form.target_pages * 1.15) }} 页</b>
            </div>
          </div>
        </div>

        <div class="pg-hint">
          <button class="pg-btn ghost" :disabled="measuring || saving" @click="measure">{{ measuring ? '正在更新目录并测量…' : '测量实际页数并校准' }}</button>
          <button v-if="measurement?.fresh && measurement.within_tolerance === false" class="pg-btn ghost" :disabled="adjusting || measuring || saving" @click="adjust">{{ adjusting ? '正在调整篇幅…' : '按实测重写超差章节' }}</button>
          <p v-if="adjusting">将重写超差章节并复测，原正文自动保留为上一版。{{ adjustDetail }}</p>
          <p v-if="measurement">实际正文 {{ measurement.body_pages }} 页（{{ measurement.engine }}）；{{ measurement.fresh ? (measurement.within_tolerance === false ? '超出目标范围' : '当前版本测量结果') : '正文或排版已变动，需重新测量' }}。</p>
          <p v-if="measurement?.fresh"><a :href="measurement.docx_url" target="_blank">下载已更新目录的 Word</a> · <a :href="measurement.pdf_url" target="_blank">查看分页 PDF</a></p>
        </div>
        <p class="pg-note">
          <span v-if="coeff.source !== 'measured'">
            系数为<strong>估算</strong>：由版心尺寸与字号算出满版密度，再乘文档结构填充率
            （{{ coeff.fill_factor }}，实测于本机，随排版变化极小）。
            目前只能用理论满版密度 {{ coeff.density }} 字/页折算出 {{ coeff.value }} 字/页——
            生成后会核验字数，超差时最多自动调整两次。图片、表格和分页会影响最终页数，
            这里的估算不能替代实际分页；生成完成后可测量并校准。
          </span>
          <span v-else>系数取自本机实测（{{ coeff.engine }}，{{ coeff.measured_at }}）。</span>
        </p>

        <div class="pg-table-head">
          <span class="pg-table-title">各章额度</span>
          <span class="pg-table-tip">
            「目标字数」留空 = 按权重自动分摊；填了就是这个章的硬额度（不随目标页数变化）
          </span>
        </div>

        <el-table :data="rows" size="small" height="360" class="pg-table" :row-class-name="rowClass">
          <el-table-column label="章节" min-width="240">
            <template #default="{ row }">
              <span :style="{ paddingLeft: (row.level - 1) * 14 + 'px' }" :title="row.title">
                <span class="pg-lv">{{ row.level }}</span>{{ row.title }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="权重" width="96" align="right">
            <template #default="{ row }">
              <span
                class="num pg-dim" :title="row.inherited
                  ? '本章标题命不中关键词，随父章的重要度（父章倍率 × 0.9）'
                  : '按本章标题命中的关键词判定'"
              >
                {{ row.weight.toFixed(2) }}<em v-if="row.inherited" class="pg-inh">随父</em>
              </span>
            </template>
          </el-table-column>
          <el-table-column label="目标字数" width="130" align="right">
            <template #default="{ row }">
              <el-input-number
                v-model="row.override" :min="1" :max="100000" :step="100" :controls="false"
                size="small" class="pg-ovr"
                :disabled="!form.target_pages"
                :placeholder="row.target_words ? String(row.target_words) : '—'"
              />
            </template>
          </el-table-column>
          <el-table-column label="当前字数" width="100" align="right">
            <template #default="{ row }"><span class="num">{{ row.words ? fmt(row.words) : '—' }}</span></template>
          </el-table-column>
          <el-table-column label="偏差" width="90" align="right">
            <template #default="{ row }">
              <span v-if="deviation(row) === null" class="pg-dim">—</span>
              <span v-else class="num" :class="devClass(row)">{{ pct(deviation(row)) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="pg-foot">
        <span class="pg-foot-tip">改动在<strong>下一次生成该章</strong>时生效</span>
        <button class="pg-btn ghost" :disabled="saving" @click="resetDefault">恢复默认</button>
        <button class="pg-btn primary" :disabled="saving || loading" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getPagePlan, savePagePlan, measurePagePlan, adjustPagePlan, taskStatus } from '../api'

const props = defineProps({
  styleRevision: { type: Number, default: 0 },
  modelValue: { type: Boolean, default: false },
  projectId: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const loading = ref(false)
const saving = ref(false)
const measuring = ref(false)
const measurement = ref(null)
const adjusting = ref(false), adjustDetail = ref('')
let adjustTimer = null
onUnmounted(() => { if (adjustTimer) clearTimeout(adjustTimer) })
const rows = ref([])
const range = ref([1, 2000])
const coeff = ref({ value: 0, source: 'estimated', density: 0, fill_factor: 0, engine: '', measured_at: '' })
const total = ref(null)
const stats = ref({ nodes: 0, written: 0, words: 0 })
// 重写阈值本面板没有控件（属导出后回收那部分，见下步），但要原样带回服务端，
// 否则每次保存都会把它冲回默认值。
const tolerance = ref(0.15)
// 目标页数留空的语义是「不设目标」，所以不能给 0——用 null。
const form = reactive({ target_pages: null })
const estimatedPages = computed(() => coeff.value.value
  ? (stats.value.words / coeff.value.value).toFixed(1) : '—')
watch([() => form.target_pages, () => coeff.value.value], ([pages]) => {
  total.value = pages && coeff.value.value ? Math.trunc(pages * coeff.value.value) : null
})

function fmt(n) { return Number(n || 0).toLocaleString('zh-CN') }
function pct(v) { return (v >= 0 ? '+' : '') + Math.round(v * 100) + '%' }
// 生效额度：填了自定义就用自定义，否则用自动分摊出来的
function eff(row) { return row.override || row.target_words || null }
function deviation(row) {
  const t = eff(row)
  if (!t || !row.words) return null
  return (row.words - t) / t
}
function devClass(row) {
  const d = deviation(row)
  if (d === null) return ''
  return Math.abs(d) <= 0.15 ? 'pg-ok' : (d > 0 ? 'pg-over' : 'pg-under')
}
function rowClass({ row }) { return row.override ? 'pg-row-custom' : '' }

async function load(preserve = false) {
  if (!props.projectId) return
  loading.value = true
  const draft = preserve ? { pages: form.target_pages, rows: new Map(rows.value.map(r => [r.id, r.override])) } : null
  try {
    const { data } = await getPagePlan(props.projectId)
    form.target_pages = data.plan?.target_pages ?? null
    tolerance.value = data.plan?.rewrite_tolerance ?? 0.15
    coeff.value = data.coefficient || coeff.value
    total.value = data.target_words_total
    stats.value = data.stats || stats.value
    measurement.value = data.measurement || null
    rows.value = data.nodes || []
    if(draft){
      form.target_pages=draft.pages
      rows.value.forEach(r=>{if(draft.rows.has(r.id))r.override=draft.rows.get(r.id)})
      total.value=form.target_pages && coeff.value.value ? Math.trunc(form.target_pages*coeff.value.value) : null
    }
    if (data.options?.target_pages_range) range.value = data.options.target_pages_range
  } catch (e) {
    ElMessage.error('篇幅设置读取失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

watch(() => props.modelValue, (v) => { if (v) load() })
watch(() => props.styleRevision, () => { if(props.modelValue) load(true) })
onMounted(() => { if (props.modelValue) load() })

function resetDefault() {
  form.target_pages = null
  rows.value.forEach((r) => { r.override = null })
  ElMessage.info('已清空目标页数与各章额度，点「保存」后生效')
}

async function save() {
  saving.value = true
  try {
    // 覆盖项全量提交：值为 null 表示回到按权重自动分摊。
    // 服务端对 overrides 是按条目更新（稀疏补丁），全量提交正好能把清空的额度落成 NULL。
    await persistControls()
    ElMessage.success('篇幅设置已保存，下次生成该章时生效')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

function persistControls() {
  const overrides = {}
  rows.value.forEach((r) => { overrides[r.id] = r.override || null })
  return savePagePlan(props.projectId, { target_pages: form.target_pages ?? null, rewrite_tolerance: tolerance.value, overrides })
}

async function measure() {
  measuring.value = true
  try {
    await persistControls()
    await measurePagePlan(props.projectId)
    await load()
    ElMessage.success('测量完成，下一次生成将使用当前排版的实测系数')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally { measuring.value = false }
}

async function adjust() {
  adjusting.value = true
  try {
    await persistControls()
    const { data } = await adjustPagePlan(props.projectId)
    const poll = async () => {
      try {
        const { data: task } = await taskStatus(data.task_id)
        adjustDetail.value = task.detail || ''
        if (task.status === 'running' || task.status === 'pending') { adjustTimer = setTimeout(poll, 3000); return }
        adjusting.value = false
        await load()
        if (task.status === 'success') ElMessage.success(task.detail || '篇幅调整完成')
        else ElMessage.warning(task.detail || '篇幅仍需调整，请查看实际测量结果')
      } catch (e) { adjusting.value = false; ElMessage.error(e?.response?.data?.detail || e.message) }
    }
    await poll()
  } catch (e) { adjusting.value = false; ElMessage.error(e?.response?.data?.detail || e.message) }
}
</script>

<style scoped>
.pg-wrap { display: flex; flex-direction: column; height: 100%; background: var(--paper); }

.pg-head { display: flex; align-items: flex-start; justify-content: space-between; padding: 18px 20px 14px; background: var(--card); border-bottom: 1px solid var(--line); }
.pg-title { font-size: 15px; font-weight: 700; }
.pg-sub { font-size: 11px; color: var(--text-3); margin-top: 4px; line-height: 1.7; max-width: 600px; }
.pg-close { border: none; background: none; font-size: 14px; color: var(--text-3); cursor: pointer; padding: 4px 6px; border-radius: 4px; }
.pg-close:hover { background: var(--paper-warm); color: var(--text); }

.pg-body { flex: 1; overflow-y: auto; padding: 6px 20px 20px; }
.pg-empty { flex: 1; display: flex; align-items: center; justify-content: center; font-size: 13px; color: var(--text-3); }

.pg-top { display: flex; gap: 16px; align-items: flex-start; margin-top: 14px; }
.pg-field { flex: 1; }
.pg-field > label { display: block; font-size: 12px; font-weight: 600; color: var(--text-2); margin-bottom: 6px; }
.pg-num { width: 100%; }
.pg-hint { font-size: 11px; color: var(--text-3); margin-top: 6px; line-height: 1.6; }

.pg-card { flex: 1; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-s); padding: 10px 14px; }
.pg-card-row { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--text-2); padding: 4px 0; }
.pg-card-row b { color: var(--text); font-weight: 600; }

.pg-note { font-size: 11px; color: var(--text-3); line-height: 1.7; margin-top: 12px; padding: 10px 12px; background: var(--paper-warm); border-radius: var(--radius-s); }
.pg-note strong { color: var(--text-2); }

.pg-table-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin: 20px 0 8px; }
.pg-table-title { font-size: 12px; font-weight: 600; color: var(--text-2); }
.pg-table-tip { font-size: 11px; color: var(--text-3); }
.pg-table { border: 1px solid var(--line); border-radius: var(--radius-s); }

/* 填了人工额度的行加底色：一眼看出哪几章不再跟权重走。
   行是 el-table 内部渲染的，scoped 打不到，必须 :deep。 */
:deep(.pg-row-custom td) { background: var(--paper-warm); }

.pg-lv { display: inline-block; min-width: 16px; margin-right: 4px; font-size: 10px; color: var(--text-3); font-family: var(--font-num); }
/* 「随父」：本章标题没有自己的关键词，权重是从父章继承来的（悬停有说明） */
.pg-inh { font-style: normal; font-size: 9px; margin-left: 3px; padding: 0 3px; border-radius: 2px; background: var(--paper-warm); color: var(--text-3); }
.pg-dim { color: var(--text-3); }
.pg-ovr { width: 100%; }
.pg-ok { color: var(--green); }
.pg-over { color: var(--red); }
.pg-under { color: var(--amber); }

.pg-foot { display: flex; align-items: center; justify-content: flex-end; gap: 10px; padding: 12px 20px; background: var(--card); border-top: 1px solid var(--line); }
.pg-foot-tip { flex: 1; font-size: 11px; color: var(--text-3); }
.pg-btn { border-radius: var(--radius-s); font-size: 13px; padding: 7px 18px; cursor: pointer; transition: all 0.15s; }
.pg-btn:disabled { opacity: 0.6; cursor: default; }
.pg-btn.ghost { border: 1px solid var(--line-strong); background: var(--card); color: var(--text-2); }
.pg-btn.ghost:hover:not(:disabled) { border-color: var(--brand); color: var(--brand); }
.pg-btn.primary { border: 1px solid var(--brand); background: var(--brand); color: #fff; font-weight: 600; }
.pg-btn.primary:hover:not(:disabled) { background: var(--brand-deep); border-color: var(--brand-deep); }
</style>

<style>
/* 抽屉 teleport 到 body，scoped 样式命中不了：去掉默认 header 与 body 内边距 */
.pg-drawer .el-drawer__body { padding: 0; }
</style>
