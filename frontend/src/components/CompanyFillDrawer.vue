<template>
  <el-drawer :model-value="modelValue" @update:model-value="$emit('update:modelValue',$event)" title="从公司资料补全待补项" size="80%" :close-on-click-modal="false">
    <p>这里只处理可由公司事实补入的待补项，不是全部问题的修复入口。核对来源后选择应用；本次人员投入或承诺需确定，非必要细节可以回正文改写。</p>
    <el-select v-model="node" filterable placeholder="选择有正文的章节" style="width:65%" @change="result={items:[]};selection=[]"><el-option v-for="c in chapters" :key="c.id" :value="c.id" :label="c.title" /></el-select>
    <el-button :disabled="!node || busy" :loading="searching" @click="search">查找补全建议</el-button>
    <el-alert v-if="result.truncated" type="warning" title="本章待补项超过80处，本次只处理前80处，处理后可再次查找" :closable="false" />
    <el-table :data="result.items || []" @selection-change="selection=$event" row-key="start" empty-text="选择章节后查找建议；缺少公司事实可返回检查面板管理公司资料">
      <el-table-column type="selection" :selectable="r=>r.status==='suggested' && !busy" />
      <el-table-column prop="placeholder" label="待补项" min-width="160" />
      <el-table-column label="建议与理由" min-width="220"><template #default="{row}"><p>{{ row.replacement || '仍需处理' }}</p><small>{{ row.message }}</small></template></el-table-column>
      <el-table-column label="事实依据" min-width="260"><template #default="{row}"><template v-if="row.fact"><p>{{ row.fact.subject }} · {{ row.fact.field_name }}</p><p>{{ row.fact.evidence }}</p><small>{{ row.fact.source_name || '手工核实资料' }} · {{ row.fact.location }}</small><p><a v-if="row.fact.source_url" :href="row.fact.source_url" target="_blank" rel="noopener">查看原件</a></p></template></template></el-table-column>
    </el-table>
    <el-button type="primary" :disabled="!selection.length || busy" :loading="applying" @click="apply">应用所选 {{ selection.length }} 项</el-button>
    <p>修改记录（撤回仅在正文未被后续编辑时可执行）</p>
    <el-table :data="revisions"><el-table-column prop="title" label="章节" /><el-table-column prop="created_at" label="时间" /><el-table-column label="依据"><template #default="{row}">{{ row.facts.map(f=>`${f.field_name || f.category}：${f.value}`).join('；') }}</template></el-table-column><el-table-column label="操作" width="120"><template #default="{row}"><el-button v-if="row.status==='applied'" link :disabled="busy" @click="undo(row)">撤回补全</el-button><span v-else>已撤回</span></template></el-table-column></el-table>
  </el-drawer>
</template>
<script setup>
import {ref,computed,watch} from 'vue'
import {ElMessage} from 'element-plus'
import {companyChapters,companySuggestions,companyApply,companyRevisions,companyUndo} from '../api'
const props=defineProps({modelValue:Boolean,projectId:[String,Number],nodeId:[String,Number]})
const emit=defineEmits(['update:modelValue','changed'])
const node=ref(),chapters=ref([]),result=ref({items:[]}),selection=ref([]),revisions=ref([]),searching=ref(false),applying=ref(false)
const busy=computed(()=>searching.value || applying.value)
const error=e=>ElMessage.error(e?.response?.data?.detail || e.message)
async function history(){revisions.value=(await companyRevisions(props.projectId)).data}
async function search(){searching.value=true;result.value={items:[]};selection.value=[];try{result.value=(await companySuggestions(props.projectId,node.value)).data;if(!result.value.total)ElMessage.info('本章未发现可识别的待补充或待确认标记')}catch(e){error(e)}finally{searching.value=false}}
async function apply(){applying.value=true;try{await companyApply(props.projectId,node.value,selection.value.map(r=>r.token));emit('changed',node.value);result.value={items:[]};selection.value=[];await history();ElMessage.success('所选事实已补入，修改依据已记录')}catch(e){error(e)}finally{applying.value=false}}
async function undo(row){applying.value=true;try{await companyUndo(props.projectId,row.id);emit('changed',row.node_id);result.value={items:[]};selection.value=[];await history();ElMessage.success('补全已撤回')}catch(e){error(e)}finally{applying.value=false}}
watch(()=>props.modelValue,async open=>{if(!open)return;result.value={items:[]};selection.value=[];try{chapters.value=(await companyChapters(props.projectId)).data;node.value=chapters.value.some(c=>c.id===Number(props.nodeId))?Number(props.nodeId):chapters.value[0]?.id;await history()}catch(e){error(e)}})
</script>
