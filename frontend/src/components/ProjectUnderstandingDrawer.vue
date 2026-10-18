<template>
  <el-drawer :model-value="modelValue" @update:model-value="$emit('update:modelValue',$event)" title="项目理解与响应目录" size="80%">
    <div v-loading="loading">
      <el-alert title="先核对招标理解，再确认目录与排版，最后生成和检查技术方案。章节关联只是候选位置，不代表正文已经响应。" type="info" :closable="false" />
      <p><el-tag :type="data.confirmed ? 'success' : 'warning'">{{ data.confirmed ? '当前版本已确认' : '当前版本待确认' }}</el-tag>
        当前标段：{{ data.lot || '未划分标段' }} <el-button @click="load">刷新</el-button></p>
      <el-form label-width="110px">
        <el-form-item label="应用行业"><el-select v-model="form.domain" @change="form.project_types=[]"><el-option v-for="d in domains" :key="d.key" :value="d.key" :label="d.name" /></el-select></el-form-item>
        <el-form-item label="服务类型"><el-select v-model="form.project_types" multiple><el-option v-for="t in typeOptions" :key="t" :value="t" :label="t" /></el-select></el-form-item>
        <el-form-item label="测试或服务对象"><el-select v-model="form.technical_objects" multiple filterable allow-create default-first-option /></el-form-item>
      </el-form>
      <el-button @click="suggest" :loading="suggesting">从招标内容获取分类建议</el-button>
      <el-button @click="saveClassification">保存分类修正</el-button>
      <p v-if="suggestionText">{{ suggestionText }}（建议需保存；不会自动修改应用行业）</p>
      <p>招标资料：<el-tag v-for="f in data.files || []" :key="f.id" :type="f.status === 'parsed' ? 'success' : 'info'" style="margin:4px">{{ f.file_name }} · {{ f.status }}</el-tag></p>
      <p v-if="!data.requirements?.length">尚无解析要求，请先上传并完成招标文件解析。不能将空清单视为要求已覆盖。</p>
      <el-table :data="data.requirements || []" max-height="420" row-key="id">
        <el-table-column prop="category" label="类别" width="100" />
        <el-table-column label="招标要求" min-width="300"><template #default="{row}"><strong>{{ row.title }}</strong><p style="white-space:pre-wrap">{{ row.content }}</p><small>来源：{{ row.source_file }} · {{ row.source_location || '未定位' }} <span v-if="row.weight != null"> · {{ row.weight }} 分</span></small></template></el-table-column>
        <el-table-column label="候选响应章节" min-width="160"><template #default="{row}"><p v-for="n in row.chapter_candidates" :key="n.id">{{ n.title }}</p><el-tag v-if="!row.chapter_candidates?.length" type="warning">尚未关联，请核对目录</el-tag></template></el-table-column>
        <el-table-column width="80"><template #default="{row}"><el-button link @click="editRequirement(row)">修正</el-button></template></el-table-column>
      </el-table>
      <p>项目理解补充：记录范围纠正、交付安排，以及需要确认承接能力的工作；不要在此填写无依据的能力承诺。</p>
      <el-input v-model="notes" type="textarea" :rows="5" placeholder="例如：现场实飞操作由谁负责尚待确认；应保留该要求并标记待确认。" />
      <p><el-button type="primary" :loading="saving" :disabled="!data.requirements?.length" @click="confirm">确认当前项目理解</el-button></p>
      <p>确认后：核对目录 → 设置排版与目标页数 → 全文编制 → 成稿检查 → 导出。</p>
    </div>
    <el-dialog v-model="editing" title="修正解析要求" append-to-body width="650px"><el-input v-model="req.title" placeholder="标题" /><el-input v-model="req.content" type="textarea" :rows="9" /><el-input v-model="req.suggested_chapter" placeholder="建议章节" /><template #footer><el-button @click="saveRequirement">保存修正</el-button></template></el-dialog>
  </el-drawer>
</template>
<script setup>
import {ref,computed,watch} from 'vue'
import {ElMessage} from 'element-plus'
import {getUnderstanding,confirmUnderstanding,updateProject,classificationSuggestions,updateRequirement,listDomains} from '../api'
const props=defineProps({modelValue:Boolean,projectId:[String,Number]})
const emit=defineEmits(['update:modelValue','confirmed'])
const data=ref({}),domains=ref([]),form=ref({}),notes=ref(''),loading=ref(false),saving=ref(false),suggesting=ref(false),suggestionText=ref(''),editing=ref(false),req=ref({})
const typeOptions=computed(()=>domains.value.find(d=>d.key===form.value.domain)?.project_types || [])
const error=e=>ElMessage.error(e?.response?.data?.detail || e.message)
async function load(){loading.value=true;try{const [a,b]=await Promise.all([getUnderstanding(props.projectId),listDomains()]);data.value=a.data;domains.value=b.data.items;form.value={domain:a.data.project.domain,project_types:a.data.project.project_types || [],technical_objects:a.data.project.technical_objects || []};notes.value=a.data.notes}catch(e){error(e)}finally{loading.value=false}}
async function suggest(){suggesting.value=true;try{const {data:d}=await classificationSuggestions(props.projectId);form.value.project_types=d.project_types;form.value.technical_objects=d.technical_objects;suggestionText.value=(d.evidence || []).map(e=>e.excerpt).join('；')}catch(e){error(e)}finally{suggesting.value=false}}
async function saveClassification(){try{await updateProject(props.projectId,form.value);await load();ElMessage.success('分类已保存，请核对后确认')}catch(e){error(e)}}
async function confirm(){saving.value=true;try{await updateProject(props.projectId,form.value);const {data:current}=await getUnderstanding(props.projectId);if(current.fingerprint!==data.value.fingerprint){await load();return ElMessage.warning('分类或招标内容已变化，请核对刷新后的内容再确认')}await confirmUnderstanding(props.projectId,{fingerprint:data.value.fingerprint,notes:notes.value});await load();emit('confirmed');ElMessage.success('项目理解已确认')}catch(e){error(e)}finally{saving.value=false}}
function editRequirement(row){req.value={...row};editing.value=true}
async function saveRequirement(){try{await updateRequirement(req.value.id,{title:req.value.title,content:req.value.content,suggested_chapter:req.value.suggested_chapter});editing.value=false;await load()}catch(e){error(e)}}
watch(()=>props.modelValue,v=>{if(v)load()})
</script>

