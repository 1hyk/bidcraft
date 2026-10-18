<template>
  <el-drawer :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" title="成稿检查" size="min(860px, 95vw)">
    <div v-loading="busy">
      <el-button @click="load">刷新缺项检查</el-button>
      <el-button :loading="reviewing" @click="review">核验招标条款</el-button>
      <p v-if="report">检查 {{ report.chapters }} 节，待补资料 {{ report.pending_facts }} 处、待补图 {{ report.pending_images }} 处。{{ report.note }}</p>
      <el-table :data="report?.issues || []" max-height="360">
        <el-table-column prop="chapter" label="章节" width="180" />
        <el-table-column prop="message" label="问题" min-width="220" />
        <el-table-column prop="excerpt" label="对应内容" min-width="220" />
      </el-table>
      <template v-if="assessment">
        <p>{{ assessment.note }}</p>
        <el-collapse>
          <el-collapse-item v-for="item in assessment.items" :key="item.requirement_id" :title="`${labels[item.status] || item.status} · ${item.title}`">
            <p v-if="item.gap">{{ item.gap }}</p>
            <p v-if="!item.evidence_verified">未取得可回查证据，不能按已覆盖通过。</p>
            <blockquote v-for="e in item.evidence" :key="e.id"><strong>{{ e.chapter }}</strong><p style="white-space:pre-wrap">{{ e.text }}</p></blockquote>
          </el-collapse-item>
        </el-collapse>
      </template>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { checkProposal, reviewProposal } from '../api'
const props = defineProps({ modelValue: Boolean, projectId: String })
const emit = defineEmits(['update:modelValue'])
const busy = ref(false), reviewing = ref(false), report = ref(null), assessment = ref(null)
const labels = { covered: '有响应证据，待复核', partial: '部分响应', missing: '未找到响应', unverified: '未核实', not_applicable: '建议不适用，待复核' }
async function load() {
  busy.value = true
  assessment.value = null
  try { report.value = (await checkProposal(props.projectId)).data }
  catch (e) { ElMessage.error(e?.response?.data?.detail || e.message) }
  finally { busy.value = false }
}
async function review() {
  reviewing.value = true
  try { assessment.value = (await reviewProposal(props.projectId)).data }
  catch (e) { ElMessage.error(e?.response?.data?.detail || e.message) }
  finally { reviewing.value = false }
}
watch(() => props.modelValue, v => { if (v) load() })
</script>
