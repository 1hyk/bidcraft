<template>
  <el-drawer
    :model-value="modelValue"
    @update:model-value="(v) => emit('update:modelValue', v)"
    direction="rtl"
    size="min(560px, 92vw)"
    :with-header="false"
    class="st-drawer"
  >
    <div class="st-wrap">
      <!-- 头部 -->
      <div class="st-head">
        <div>
          <div class="st-title">排版设置</div>
          <div class="st-sub">导出 Word 时生效，只影响导出的文档，不改编辑器里的显示</div>
        </div>
        <button class="st-close" @click="emit('update:modelValue', false)">✕</button>
      </div>

      <div v-if="loading" class="st-empty">加载中…</div>

      <div v-else class="st-body">
        <div class="st-field">
          <label>正文字体</label>
          <el-select v-model="form.body_font" size="large" class="st-ctl">
            <el-option v-for="f in opt.fonts" :key="f" :label="f" :value="f" />
          </el-select>
          <p class="st-hint">只列本机肯定装了的中文字体——选没装的会静默回退，屏幕上看着对、打印出来是另一回事</p>
        </div>

        <div class="st-row">
          <div class="st-field">
            <label>正文字号</label>
            <el-select v-model="form.body_size_pt" size="large" class="st-ctl">
              <el-option v-for="s in opt.body_sizes" :key="s.pt" :label="`${s.name}（${s.pt}pt）`" :value="s.pt" />
            </el-select>
          </div>
          <div class="st-field">
            <label>行距</label>
            <el-select v-model="form.line_spacing" size="large" class="st-ctl">
              <el-option v-for="n in opt.line_spacings" :key="n" :label="`${n} 倍`" :value="n" />
            </el-select>
          </div>
        </div>

        <div class="st-field">
          <label>页边距</label>
          <el-select v-model="marginMode" size="large" class="st-ctl">
            <el-option
              v-for="m in opt.margin_presets"
              :key="m.key"
              :label="`${m.name}（上下 ${m.top} · 左右 ${m.left} 厘米）`"
              :value="m.key"
            />
            <el-option label="自定义" value="custom" />
          </el-select>
        </div>
        <div v-if="marginMode === 'custom'" class="st-margins">
          <div v-for="side in SIDES" :key="side.key" class="st-margin">
            <span>{{ side.label }}</span>
            <el-input-number
              v-model="form[side.key]" :min="0.5" :max="6" :step="0.1" :precision="2"
              :controls="false" size="small" class="st-num"
            />
          </div>
        </div>

        <div class="st-field">
          <label>页眉</label>
          <el-input v-model="form.header_text" size="large" placeholder="留空 = 用项目名" clearable />
        </div>

        <div class="st-field">
          <label>页脚</label>
          <el-input v-model="form.footer_format" size="large" placeholder="- {page} -" />
          <p class="st-hint">
            必须含 <code>{page}</code>，导出时落成 Word 的页码域；其余文字原样保留
          </p>
        </div>

        <div class="st-field">
          <label>封面文档名</label>
          <el-input v-model="form.cover_title" size="large" placeholder="响应文件" />
        </div>

        <!-- 不开放的量在这里说清楚，免得用户满面板找标题字号 -->
        <div class="st-derived">
          <div class="st-derived-title">以下按正文字号自动推导</div>
          <div class="st-derived-row">
            <span>各级标题</span>
            <b>{{ headSizes }}</b>
          </div>
          <div class="st-derived-row">
            <span>表格内文字</span>
            <b>{{ derivedTable }}pt</b>
          </div>
          <div class="st-derived-row">
            <span>首行缩进 / 目录层级</span>
            <b>2 字符 / 1–3 级</b>
          </div>
        </div>
      </div>

      <div class="st-foot">
        <button class="st-btn ghost" :disabled="saving" @click="resetDefault">恢复默认</button>
        <button class="st-btn primary" :disabled="saving || loading" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getDocxStyle, saveDocxStyle } from '../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  projectId: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const SIDES = [
  { key: 'margin_top_cm', label: '上' },
  { key: 'margin_bottom_cm', label: '下' },
  { key: 'margin_left_cm', label: '左' },
  { key: 'margin_right_cm', label: '右' },
]

const loading = ref(false)
const saving = ref(false)
const opt = ref({ fonts: [], body_sizes: [], line_spacings: [], margin_presets: [], default: {} })
const marginMode = ref('custom')
const form = reactive({
  body_font: '宋体', body_size_pt: 12, line_spacing: 1.5,
  margin_top_cm: 2.54, margin_bottom_cm: 2.54, margin_left_cm: 3.17, margin_right_cm: 3.17,
  header_text: '', footer_format: '- {page} -', cover_title: '响应文件',
})

// 与服务端推导规则同源（app/core/docx_style.py 的 head_specs / table_size_pt）：
// 面板上照写一遍，只为让用户看得见"不开放的那些项变成了什么"，不参与导出计算。
const headSizes = computed(() => {
  const b = Number(form.body_size_pt) || 12
  return [`${b + 4}`, `${b + 2}`, `${b}`, `${b}`].join(' / ') + 'pt'
})
const derivedTable = computed(() => Math.max((Number(form.body_size_pt) || 12) - 1.5, 8))

function presetKeyOf(f) {
  const hit = (opt.value.margin_presets || []).find((m) =>
    SIDES.every((s) => Math.abs(Number(f[s.key]) - m[s.key]) < 0.01))
  return hit ? hit.key : 'custom'
}

async function load() {
  if (!props.projectId) return
  loading.value = true
  try {
    const { data } = await getDocxStyle(props.projectId)
    opt.value = data.options || opt.value
    Object.assign(form, data.style || {})
    marginMode.value = presetKeyOf(form)
  } catch (e) {
    ElMessage.error('排版设置读取失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// 选预设就套用它的四个边距；切到「自定义」保留当前值当起点（不重置）
watch(marginMode, (mode) => {
  if (mode === 'custom') return
  const p = (opt.value.margin_presets || []).find((m) => m.key === mode)
  if (!p) return
  SIDES.forEach((s) => { form[s.key] = p[s.key] })
})

watch(() => props.modelValue, (v) => { if (v) load() })
onMounted(() => { if (props.modelValue) load() })

// 只重置表单，不落库——保存是唯一的写入口
function resetDefault() {
  Object.assign(form, opt.value.default || {})
  marginMode.value = presetKeyOf(form)
  ElMessage.info('已恢复默认，点「保存」后生效')
}

async function save() {
  if (!String(form.footer_format || '').includes('{page}')) {
    return ElMessage.warning('页脚必须包含 {page}，否则导出稿就没有页码了')
  }
  saving.value = true
  try {
    // el-input-number 清空会给 null，兜成默认值再提交（服务端只会钳制，不会替你补）
    const payload = { ...form }
    SIDES.forEach((s) => { payload[s.key] = Number(form[s.key]) || opt.value.default?.[s.key] || 2.0 })
    await saveDocxStyle(props.projectId, payload)
    emit('saved')
    ElMessage.success('排版已保存，篇幅估算与后续生成额度将按新排版计算')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.st-wrap { display: flex; flex-direction: column; height: 100%; background: var(--paper); }

.st-head { display: flex; align-items: flex-start; justify-content: space-between; padding: 18px 20px 14px; background: var(--card); border-bottom: 1px solid var(--line); }
.st-title { font-size: 15px; font-weight: 700; }
.st-sub { font-size: 11px; color: var(--text-3); margin-top: 3px; }
.st-close { border: none; background: none; font-size: 14px; color: var(--text-3); cursor: pointer; padding: 4px 6px; border-radius: 4px; }
.st-close:hover { background: var(--paper-warm); color: var(--text); }

.st-body { flex: 1; overflow-y: auto; padding: 6px 20px 20px; }
.st-empty { flex: 1; display: flex; align-items: center; justify-content: center; font-size: 13px; color: var(--text-3); }

.st-field { margin-top: 16px; }
.st-field > label { display: block; font-size: 12px; font-weight: 600; color: var(--text-2); margin-bottom: 6px; }
.st-ctl { width: 100%; }
.st-hint { font-size: 11px; color: var(--text-3); margin-top: 5px; line-height: 1.6; }
.st-hint code { background: var(--paper-warm); padding: 1px 4px; border-radius: 3px; color: var(--text-2); }

.st-row { display: flex; gap: 12px; }
.st-row .st-field { flex: 1; }

.st-margins { display: flex; gap: 10px; margin-top: 10px; }
.st-margin { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.st-margin > span { font-size: 11px; color: var(--text-3); }
.st-num { width: 100%; }

.st-derived { margin-top: 20px; padding: 12px 14px; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-s); }
.st-derived-title { font-size: 11px; font-weight: 600; color: var(--text-3); margin-bottom: 8px; }
.st-derived-row { display: flex; justify-content: space-between; font-size: 12px; color: var(--text-2); padding: 3px 0; }
.st-derived-row b { color: var(--text); font-family: var(--font-num); font-weight: 600; }

.st-foot { display: flex; justify-content: flex-end; gap: 10px; padding: 12px 20px; background: var(--card); border-top: 1px solid var(--line); }
.st-btn { border-radius: var(--radius-s); font-size: 13px; padding: 7px 18px; cursor: pointer; transition: all 0.15s; }
.st-btn:disabled { opacity: 0.6; cursor: default; }
.st-btn.ghost { border: 1px solid var(--line-strong); background: var(--card); color: var(--text-2); }
.st-btn.ghost:hover:not(:disabled) { border-color: var(--brand); color: var(--brand); }
.st-btn.primary { border: 1px solid var(--brand); background: var(--brand); color: #fff; font-weight: 600; }
.st-btn.primary:hover:not(:disabled) { background: var(--brand-deep); border-color: var(--brand-deep); }
</style>

<style>
/* 抽屉 teleport 到 body，scoped 样式命中不了：去掉默认 header 与 body 内边距 */
.st-drawer .el-drawer__body { padding: 0; }
</style>
