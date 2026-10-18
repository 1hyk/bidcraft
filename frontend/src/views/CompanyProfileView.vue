<template>
  <main class="company" v-loading="loading">
    <h2>本公司资料 <small>可选，不填写也能生成方案</small></h2>
    <p>上传资料，系统提取候选事实。对照原件核实后启用，供生成和待补项检索；历史标书仍放在历史参考素材中。</p>
    <el-collapse><el-collapse-item title="公司名称与业务定位（可选）"><el-input v-model="profile.name" placeholder="公司名称" /><el-input v-model="profile.positioning" type="textarea" placeholder="业务定位，不代表具体资质或本次承诺" /><el-button @click="saveBasic">保存基本资料</el-button></el-collapse-item></el-collapse>
    <section class="tools"><label class="upload">{{ uploading ? '上传中…' : '上传并提取资料' }}<input type="file" multiple accept=".docx,.pdf,.xlsx,.png,.jpg,.jpeg" :disabled="uploading" @change="upload" /></label><el-button @click="manual">手工补录</el-button><el-button @click="refresh">刷新</el-button></section>
    <p>支持 Word、文字型 PDF、Excel 和单张图片。扫描 PDF 暂需转为文字版或按图片上传。每份最多25MB；重复文件不重复提取。</p>
    <el-table :data="documents" max-height="230"><el-table-column prop="name" label="原始资料" /><el-table-column prop="status" label="提取状态" width="120" /><el-table-column prop="error" label="提示" /><el-table-column width="160"><template #default="{row}"><a :href="`/api/company/documents/${row.id}/file`" target="_blank" rel="noopener">原件</a><el-button v-if="row.status === 'failed'" link @click="retry(row.id)">重试</el-button></template></el-table-column></el-table>
    <h3>事实候选与已核实资料</h3>
    <el-radio-group v-model="filter"><el-radio-button label="pending">待核实</el-radio-button><el-radio-button label="confirmed">已核实</el-radio-button><el-radio-button label="all">全部</el-radio-button></el-radio-group>
    <el-table :data="visible" empty-text="暂无资料，上传文件或手工补录即可" row-key="id">
      <el-table-column label="事实" min-width="300"><template #default="{row}"><strong>{{ row.field_name || row.category }}</strong><p>{{ row.subject }}</p><p>{{ row.value }}</p></template></el-table-column>
      <el-table-column label="依据" min-width="250"><template #default="{row}"><p>{{ row.evidence }}</p><small>{{ row.source_name || '手工录入' }} · {{ row.location }}</small><p><a v-if="row.source_url" :href="row.source_url" target="_blank" rel="noopener">对照原件</a></p></template></el-table-column>
      <el-table-column label="状态及有效期" width="170"><template #default="{row}"><el-tag>{{ statusName(row.status) }}</el-tag><p>{{ row.valid_until || '有效期未填写' }}</p><el-tag v-if="row.expired" type="danger">已过期</el-tag><el-tag v-if="row.conflict" type="danger">存在冲突，不参与生成</el-tag></template></el-table-column>
      <el-table-column label="操作" width="130"><template #default="{row}"><el-button link @click="edit(row)">核对 / 修改</el-button><el-button v-if="row.status === 'confirmed'" link @click="disable(row)">停用</el-button></template></el-table-column>
    </el-table>
    <el-dialog v-model="editing" title="核对资料事实" width="720px">
      <el-form label-width="90px"><el-form-item label="类别"><el-select v-model="draft.category"><el-option v-for="c in categories" :key="c" :value="c" :label="c" /></el-select></el-form-item><el-form-item label="归属主体"><el-input v-model="draft.subject" placeholder="证书持有人、业绩承担单位等" /></el-form-item><el-form-item label="字段名称"><el-input v-model="draft.field_name" placeholder="资质名称、证书编号等" /></el-form-item><el-form-item label="事实值"><el-input v-model="draft.value" type="textarea" :rows="3" /></el-form-item><el-form-item label="适用限制"><el-input v-model="draft.applicability" type="textarea" /></el-form-item><el-form-item label="有效期"><el-date-picker v-model="draft.valid_until" value-format="YYYY-MM-DD" placeholder="无明确期限可留空" /></el-form-item></el-form>
      <p>原文证据：{{ draft.evidence || '手工录入，无原件证据' }}</p><a v-if="draft.source_url" :href="draft.source_url" target="_blank" rel="noopener">查看原件</a>
      <template #footer><el-button :loading="saving" @click="saveFact('rejected')">否决</el-button><el-button :loading="saving" @click="saveFact('pending')">保存待核实</el-button><el-button type="primary" :loading="saving" @click="saveFact('confirmed')">已核实，启用此事实</el-button></template>
    </el-dialog>
  </main>
</template>
<script setup>
import {ref,computed,onMounted,onUnmounted} from 'vue'
import {ElMessage} from 'element-plus'
import {getCompanyProfile,companyDocuments,companyFacts,companyUpload,companyRetry,companySaveBasic,companyCreateFact,companyReviseFact} from '../api'
const profile=ref({}),documents=ref([]),facts=ref([]),loading=ref(false),uploading=ref(false),saving=ref(false),filter=ref('pending'),editing=ref(false),draft=ref({})
const categories=['公司介绍','服务能力','资质','人员','设备','业绩','产品','其他']
const visible=computed(()=>facts.value.filter(f=>filter.value==='all' || f.status===filter.value))
const error=e=>ElMessage.error(e?.response?.data?.detail || e.message)
const statusName=s=>({pending:'待核实',confirmed:'已核实',rejected:'已否决',disabled:'已停用'}[s] || s)
async function refresh(){const [d,f]=await Promise.all([companyDocuments(),companyFacts()]);documents.value=d.data;facts.value=f.data}
async function saveBasic(){try{await companySaveBasic({name:profile.value.name,positioning:profile.value.positioning});ElMessage.success('基本资料已保存')}catch(e){error(e)}}
async function upload(event){uploading.value=true;try{for(const file of [...(event.target.files || [])]){const {data}=await companyUpload(file);if(data.duplicate)ElMessage.info(`${file.name} 已存在，未重复提取`)}await refresh()}catch(e){error(e)}finally{uploading.value=false;event.target.value=''}}
async function retry(id){try{await companyRetry(id);await refresh()}catch(e){error(e)}}
function manual(){draft.value={category:'其他',subject:'',field_name:'',value:'',valid_until:null,applicability:'',revision:1};editing.value=true}
function edit(row){draft.value={...row};editing.value=true}
async function saveFact(status){saving.value=true;try{const data={...draft.value,status};if(data.id)await companyReviseFact(data.id,data);else await companyCreateFact(data);editing.value=false;await refresh();ElMessage.success('资料状态已保存')}catch(e){error(e)}finally{saving.value=false}}
async function disable(row){try{await companyReviseFact(row.id,{...row,status:'disabled'});await refresh()}catch(e){error(e)}}
let timer,closed=false,polling=false
onMounted(async()=>{loading.value=true;try{profile.value=(await getCompanyProfile()).data;await refresh()}catch(e){error(e)}finally{loading.value=false}timer=setInterval(async()=>{if(closed || polling || !documents.value.some(d=>d.status==='extracting'))return;polling=true;try{await refresh()}catch{}finally{polling=false}},4000)})
onUnmounted(()=>{closed=true;clearInterval(timer)})
</script>
<style scoped>
.company{padding:26px;max-width:1200px;margin:auto}.company small{font-size:12px;color:#6b7280}.company p{line-height:1.7}.tools{display:flex;gap:12px;margin:20px 0}.upload{background:#2563eb;color:white;padding:8px 16px;cursor:pointer;border-radius:4px}.upload input{display:none}
</style>
