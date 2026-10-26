<template>
  <section class="planning-panel">
    <header class="planning-header"><div><h2>{{ mode === 'settings' ? '编写与排版设置' : '要求与章节对应' }}</h2><p>{{ mode === 'settings' ? '设置文档排版和目标正文页数，生成前统一核对。' : '检查有没有招标要求找不到对应章节；只用于发现目录漏项，不需要逐条安排。候选章节不代表正文已经响应。' }}</p></div></header>
    <div v-if="mode !== 'requirements'" class="planning-settings"><div><small>文档排版</small><strong>{{ styleSummary }}</strong><el-button link type="primary" @click="$emit('style')">修改排版</el-button></div><div><small>目标正文页数</small><strong>{{ targetSummary }}</strong><el-button link type="primary" @click="$emit('pages')">设置篇幅</el-button></div><div><small>配图偏好</small><strong>{{ policySummary }}</strong><el-switch v-model="policy.prefer_photorealistic_scene" :loading="savingPolicy" active-text="优先写实场景图" @change="savePolicy" /><p class="policy-hint">保存后需重新生成编写思路才生效。分区、航线、覆盖范围等空间信息仍用示意图。</p></div></div>
    <p v-if="mode !== 'requirements' && settingsError" class="settings-error">{{ settingsError }}</p>
    <slot name="outline" />
    <div v-if="mode !== 'settings'" v-loading="loading">
      <el-alert title="只检查有没有技术要求找不到对应章节：没有对应章节说明目录可能漏项，请补充或调整目录后再写正文。有候选章节也不代表正文已经响应。" type="info" :closable="false" />
      <p>当前标段：{{ data.lot || '未划分标段' }} <el-button :loading="loading" @click="load">刷新检查</el-button></p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <template v-else>
        <el-empty v-if="!requirements.length" description="尚未取得技术要求，请先完成招标要求提取并核对项目理解。" />
        <el-alert v-else-if="!data.has_directory" title="尚未生成目录。招标要求可在“项目理解”中提前查看；生成目录后在此检查覆盖情况。" type="warning" :closable="false" />
        <template v-else>
          <el-alert v-if="!unmatched.length" :title="`共 ${requirements.length} 条技术要求，都能找到对应章节。候选章节不代表正文已响应。`" type="success" :closable="false" />
          <p v-else>共 {{ requirements.length }} 条技术要求，<strong class="gap-count">{{ unmatched.length }} 条未找到对应章节</strong>。请补充或调整目录；候选章节不代表正文已响应。</p>
          <el-switch v-model="onlyUnmatched" active-text="只看未对应" />
          <el-table :data="visibleRequirements" row-key="id" :empty-text="onlyUnmatched ? '没有未对应的要求' : ''">
            <el-table-column label="招标要求与来源" min-width="320"><template #default="{row}"><strong>{{ row.title }}</strong><p class="requirement-content">{{ row.content }}</p><small>{{ row.source_file }} · {{ row.source_location || '未定位' }}</small></template></el-table-column>
            <el-table-column label="对应章节（仅供核对目录覆盖）" min-width="320"><template #default="{row}">
              <p v-if="!row.chapter_candidates?.length" class="missing-chapter">未找到对应章节，请补充或调整目录。</p>
              <template v-else><p v-for="n in row.chapter_candidates" :key="n.id"><el-button link type="primary" @click="locate(n.id)">{{ n.title }}</el-button></p><details v-if="row.chapter_candidates[0].basis"><summary>查看匹配依据</summary><p class="candidate-basis">{{ row.chapter_candidates[0].basis }}</p></details></template>
            </template></el-table-column>
          </el-table>
        </template>
      </template>
    </div>
  </section>
</template>
<script setup>
import {ref, computed, watch} from 'vue'
import {ElMessage} from 'element-plus'
import {getUnderstanding, getDocxStyle, getPagePlan, getImagePolicy, saveImagePolicy} from '../api'
const props = defineProps({projectId:[String, Number], refreshKey:Number, mode:{type:String,default:'all'}})
const emit = defineEmits(['locate', 'style', 'pages'])
const style = ref({}), plan = ref({}), policy = ref({prefer_photorealistic_scene: false}), savingPolicy = ref(false), settingsError = ref('')
const styleSummary = computed(() => style.value.body_size_pt ? `A4 · 正文 ${style.value.body_size_pt} 磅 · ${style.value.line_spacing || '默认'} 倍行距` : '尚未读取排版设置')
const targetSummary = computed(() => plan.value.target_pages ? `${plan.value.target_pages} 页（允许上下浮动 ${Math.round((plan.value.rewrite_tolerance ?? .15) * 100)}%）` : '未指定目标页数')
const policySummary = computed(() => policy.value.prefer_photorealistic_scene ? '涉及现场/实物时优先写实场景图' : '按信息内容自动判断画风')
const data = ref({}), loading = ref(false), error = ref(''), onlyUnmatched = ref(false)
const requirements = computed(() => (data.value.requirements || []).filter(r => r.display_group === 'technical'))
const unmatched = computed(() => requirements.value.filter(r => !r.chapter_candidates?.length))
const visibleRequirements = computed(() => onlyUnmatched.value ? unmatched.value : requirements.value)
let request = 0
async function load() {
  const current = ++request
  loading.value = true
  error.value = ''
  data.value = {}
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
  try {
    const [a,b,c] = await Promise.all([getDocxStyle(props.projectId), getPagePlan(props.projectId), getImagePolicy(props.projectId)])
    style.value = a.data.style; plan.value = b.data.plan || {}; policy.value = c.data.policy || {prefer_photorealistic_scene: false}
  }
  catch { style.value = {}; plan.value = {}; settingsError.value = '编写设置读取失败，请在开始编写前重新核对。' }
}
async function savePolicy(value) {
  savingPolicy.value = true
  try {
    const {data} = await saveImagePolicy(props.projectId, policy.value)
    policy.value = data.policy
    ElMessage.success('配图偏好已保存；重新生成编写思路后生效。')
  } catch (e) {
    policy.value.prefer_photorealistic_scene = !value
    ElMessage.error(e?.response?.data?.detail || '配图偏好保存失败，请重试。')
  } finally { savingPolicy.value = false }
}
watch(() => props.projectId, () => { onlyUnmatched.value = false; if(props.mode !== 'settings')load(); if(props.mode !== 'requirements')loadSettings() }, {immediate:true})
watch(() => props.refreshKey, () => { if(props.mode !== 'settings')load(); if(props.mode !== 'requirements')loadSettings() })
</script>
<style scoped>
.planning-panel { flex:1; min-width:0; overflow:auto; padding:28px; background:#fafbfe; }
.planning-header { display:flex; justify-content:space-between; gap:16px; align-items:center; }
h2 { margin:0; font-size:21px; color:#20314e; }
.planning-header p { color:#64748b; line-height:1.8; font-size:13px; }
.planning-settings { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin:18px 0 26px; }
.planning-settings > div { background:white; border:1px solid #e1e7f0; border-radius:10px; padding:18px; }
.planning-settings small,.planning-settings strong { display:block; margin-bottom:10px; }
.planning-settings .policy-hint { margin:10px 0 0; color:#64748b; font-size:12px; line-height:1.7; }
.settings-error { color:#b45309; }
h3 { font-size:16px; }
@media(max-width:900px) { .planning-settings { grid-template-columns:1fr; } .planning-panel { padding:16px; } }
.gap-count { color:#b45309; }
.missing-chapter { color:#b45309; }
.candidate-basis { color:#64748b; font-size:12px; line-height:1.7; }
.requirement-content { white-space: pre-wrap; line-height: 1.8; }
small { color: #64748b; }
</style>
