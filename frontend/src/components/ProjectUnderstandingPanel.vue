<template>
  <section class="understanding-page" aria-label="项目理解">
    <header class="page-heading"><div><span class="eyebrow">第一步 · 明确编制依据</span><h2>理解本次招标需求</h2><p>先核对服务范围和技术要求，再规划目录。分类信息可稍后完善。</p></div><el-button @click="emit('files')">管理招标文件</el-button></header>
    <div v-loading="loading">
      <el-alert title="这里核对系统是否读懂招标文件；确认不代表我方已具备全部能力，也不代表正文已经响应。" type="info" :closable="false" />
      <p><el-tag :type="data.confirmed ? 'success' : 'warning'">{{ data.confirmed ? '当前版本已确认' : '当前版本待确认' }}</el-tag>
        当前标段：{{ data.lot || '未划分标段' }} <el-button @click="refresh">刷新</el-button></p>
      <h3>项目概况</h3>
      <p>招标资料：<el-tag v-for="f in data.files || []" :key="f.id" :type="f.status === 'parsed' ? 'success' : 'info'" style="margin:4px">{{ f.file_name }} · {{ f.status === 'parsed' ? '原文已解析' : f.status }}</el-tag></p>
      <section class="classification" aria-label="服务分类与工作对象"><h3>服务分类与工作对象</h3><p class="hint">直接核对或修改以下信息，也可查看原文提取建议；保存后用于后续编制。未填写时以招标原文为准。</p>
      <el-form label-width="110px">
        <el-form-item label="应用行业"><el-select v-model="form.domain"><el-option v-for="d in domains" :key="d.key" :value="d.key" :label="d.name" /></el-select></el-form-item>
        <el-form-item label="服务类型"><el-select v-model="form.project_types" multiple filterable allow-create default-first-option placeholder="选预设分类或自行输入，可多选"><el-option v-for="t in typeOptions" :key="t" :value="t" :label="t" /></el-select></el-form-item>
        <el-form-item label="本次服务内容"><el-input v-model="form.service_content" maxlength="2000" placeholder="保留招标中的具体业务描述" /></el-form-item>
        <el-form-item label="工作对象"><el-select v-model="form.technical_objects" multiple filterable allow-create default-first-option placeholder="如车辆、软件系统、设备；不是采购单位" /></el-form-item>
      </el-form>
      <el-button @click="suggest(false)" :loading="suggesting">查看提取建议</el-button>
      <el-button @click="suggest(true)" :disabled="suggesting">重新提取</el-button>
      <el-button @click="saveClassification">保存分类修正</el-button>
      <template v-if="suggestionReady">
        <p class="hint">建议版本 {{ proposed.revision }} · {{ proposed.extracted_at }}。相同招标依据复用该版本；重新提取不会覆盖已保存的项目分类。</p>
        <p>建议服务类型：{{ proposed.project_types?.join('、') || '未能明确识别，可人工填写' }}</p>
        <p>建议本次服务内容：{{ proposed.service_content || '原文未明确' }}</p>
        <p v-for="w in proposed.warnings || []" :key="w" class="hint">{{ w }}</p>
        <p>建议工作对象：{{ proposed.technical_objects?.join('、') || '未能明确识别，可人工填写' }}</p>
        <el-button @click="adoptSuggestion">采用到表单</el-button>
      </template>
      <details v-for="e in suggestionEvidence" :key="(e.type ? 'service:' : 'object:') + e.name"><summary>{{ e.type ? '本次服务内容：' + e.type : '工作对象：' + e.name }} · 查看依据</summary><p class="source-quote">{{ e.excerpt }}</p><small>{{ e.source_file }} · {{ e.source_location }}</small></details>
      </section>
      <p class="hint">以下概况摘自已提取要求，供快速核对；完整工作范围以招标原文及下方要求清单为准。</p>
      <div v-for="r in overview" :key="r.id" class="overview-item"><strong>{{ r.title }}</strong><p>{{ r.content }}</p><details><summary>查看来源</summary>{{ r.source_file }} · {{ r.source_location || '未定位' }}</details></div>
      <p v-if="!overview.length" class="hint">尚未提取到可展示的项目概况，不能据此认定招标文件没有要求。</p>

      <h3>技术方案需要响应的要求</h3>
      <el-alert v-if="!data.requirements?.length" title="尚未取得招标要求：即使原文已解析，也可能是要求提取未完成或失败。请核对解析任务，空清单不能视为已覆盖。" type="warning" :closable="false" />
      <p class="hint">要求提取完成后即可核对，无需先生成目录。这里检查原文是否读对、要求是否遗漏；生成目录后，在第二步“目录规划”核对章节安排。</p>
      <el-table empty-text="尚无技术要求，请先完成招标文件解析" :data="technicalRequirements" max-height="420" row-key="id">
        <el-table-column prop="category" label="类别" width="100" />
        <el-table-column label="招标要求" min-width="300"><template #default="{row}"><strong>{{ row.title }}</strong><p style="white-space:pre-wrap">{{ row.content }}</p><small>来源：{{ row.source_file }} · {{ row.source_location || '未定位' }} <span v-if="row.weight != null"> · {{ row.weight }} 分</span></small></template></el-table-column>
        <el-table-column width="150"><template #default="{row}"><el-button link @click="viewSource(row)">查看原文</el-button><el-button link @click="editRequirement(row)">修正</el-button></template></el-table-column>
      </el-table>
      <el-collapse v-if="documentRequirements.length || otherRequirements.length">
        <el-collapse-item v-if="documentRequirements.length" :title="`编制与提交要求（${documentRequirements.length} 条）`"><p class="hint">用于核对文件格式、份数、签署与提交，不要求单独编写正文章节；具体排版仍需在排版设置中落实。</p><div v-for="r in documentRequirements" :key="r.id" class="overview-item"><strong>{{ r.title }}</strong><p>{{ r.content }}</p><small>{{ r.source_file }} · {{ r.source_location }}</small><el-button link @click="editRequirement(r)">修正</el-button></div></el-collapse-item>
        <el-collapse-item v-if="otherRequirements.length" :title="`其他投标事项（${otherRequirements.length} 条）`"><p class="hint">报价、评审方法及通用投标合规提醒，不单独关联技术正文章节。综合性实质条款提醒仍需结合各项技术要求逐条落实。</p><div v-for="r in otherRequirements" :key="r.id" class="overview-item"><strong>{{ r.title }}</strong><p>{{ r.content }}</p><small>{{ r.source_file }} · {{ r.source_location }}</small><el-button link @click="editRequirement(r)">修正</el-button></div></el-collapse-item>
      </el-collapse>
      <p>项目理解补充：记录范围纠正、交付安排，以及需要确认承接能力的工作；不要在此填写无依据的能力承诺。</p>
      <el-input v-model="notes" type="textarea" :rows="3" placeholder="可选：仅记录系统读错、遗漏或需要进一步确认的内容，不必重复抄写招标要求。" />
      <footer class="stage-footer"><span>{{ dirty ? '有未保存的修改；切换步骤会保留草稿，保存后才用于后续编制。' : '保存本次核对结果，下一步安排章节并设置排版、页数。' }}</span><el-button type="primary" :loading="saving" :disabled="loading || !data.requirements?.length" @click="confirm">保存并进入目录规划 →</el-button></footer>
    </div>
    <el-dialog v-model="editing" title="修正解析要求" append-to-body width="650px"><el-input v-model="req.title" placeholder="标题" /><el-input v-model="req.content" type="textarea" :rows="9" /><el-input v-model="req.suggested_chapter" placeholder="建议章节" /><template #footer><el-button @click="saveRequirement">保存修正</el-button></template></el-dialog>
  </section>
</template>
<script setup>
import {ref,computed,watch} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {getUnderstanding,confirmUnderstanding,updateProject,classificationSuggestions,refreshClassificationSuggestions,updateRequirement,listDomains,serviceTypeOptions} from '../api'
import {serviceChoices,classificationImpact} from '../serviceTypes'
import {understandingBasis} from '../workflowStep'
const props=defineProps({modelValue:Boolean,projectId:[String,Number]})
const emit=defineEmits(['confirmed','files','source'])
const data=ref({}),domains=ref([]),form=ref({}),notes=ref(''),loading=ref(false),saving=ref(false),suggesting=ref(false),suggestionReady=ref(false),suggestionEvidence=ref([]),editing=ref(false),req=ref({})
const technicalRequirements=computed(()=>(data.value.requirements || []).filter(r=>r.display_group==='technical'))
const documentRequirements=computed(()=>(data.value.requirements || []).filter(r=>r.display_group==='document_rules'))
const otherRequirements=computed(()=>(data.value.requirements || []).filter(r=>r.display_group==='bid_notices'))
const overview=computed(()=>technicalRequirements.value.filter(r=>/范围|内容|期限|工期|地点|交付|成果|验收/.test(r.title)).slice(0,5))
const commonTypes=ref([])
const typeOptions=computed(()=>serviceChoices(commonTypes.value,form.value.project_types,proposed.value.project_types))
const proposed=ref({})
const savedDraft=ref('')
const draft=()=>JSON.stringify({form:form.value,notes:notes.value})
const dirty=computed(()=>Boolean(savedDraft.value) && draft()!==savedDraft.value)
async function refresh(){if(dirty.value){try{await ElMessageBox.confirm('刷新会放弃当前未保存的项目理解修改。是否继续？','刷新项目理解',{confirmButtonText:'放弃修改并刷新',cancelButtonText:'保留修改'})}catch{return}}await load()}
const error=e=>ElMessage.error(e?.response?.data?.detail || e.message)
let loadVersion=0
async function load(){
  const version=++loadVersion, id=props.projectId
  loading.value=true
  try{
    const [a,b,c]=await Promise.all([getUnderstanding(id),listDomains(),serviceTypeOptions()])
    if(version!==loadVersion || id!==props.projectId)return
    data.value=a.data;domains.value=b.data.items;commonTypes.value=c.data.items
    form.value={service_content:a.data.project.service_content || '',domain:a.data.project.domain,project_types:a.data.project.project_types || [],technical_objects:a.data.project.technical_objects || []}
    notes.value=a.data.notes;savedDraft.value=draft()
  }catch(e){if(version===loadVersion)error(e)}finally{if(version===loadVersion)loading.value=false}
}
async function suggest(refresh=false){suggesting.value=true;try{const {data:d}=await (refresh ? refreshClassificationSuggestions : classificationSuggestions)(props.projectId);proposed.value=d;suggestionEvidence.value=[...(d.object_evidence || []),...(d.evidence || [])];suggestionReady.value=true}catch(e){error(e)}finally{suggesting.value=false}}
function adoptSuggestion(){form.value.service_content=proposed.value.service_content || '';form.value.project_types=[...(proposed.value.project_types || [])];form.value.technical_objects=[...(proposed.value.technical_objects || [])];ElMessage.info('建议已填入表单，核对并保存后生效')}
async function saveClassification(){try{const message=classificationImpact(data.value.project,form.value),draftNotes=notes.value;await updateProject(props.projectId,form.value);await load();notes.value=draftNotes;ElMessage.info(message)}catch(e){error(e)}}
async function confirm(){
  saving.value=true
  try{
    const basis=understandingBasis(data.value)
    await updateProject(props.projectId,form.value)
    const {data:current}=await getUnderstanding(props.projectId)
    if(understandingBasis(current)!==basis){await load();return ElMessage.warning('招标依据已变化，请核对刷新后的内容再继续')}
    await confirmUnderstanding(props.projectId,{fingerprint:current.fingerprint,notes:notes.value})
    await load();emit('confirmed');ElMessage.success('已保存核对结果，进入目录规划')
  }catch(e){error(e)}finally{saving.value=false}
}
function viewSource(row){const file=(data.value.files || []).find(f=>f.file_name===row.source_file);if(file)emit('source',file);else ElMessage.info('未找到对应文件，请从项目文件中核对来源')}
function editRequirement(row){req.value={...row};editing.value=true}
async function saveRequirement(){try{const draftForm=JSON.parse(JSON.stringify(form.value)),draftNotes=notes.value;await updateRequirement(req.value.id,{title:req.value.title,content:req.value.content,suggested_chapter:req.value.suggested_chapter});editing.value=false;await load();form.value=draftForm;notes.value=draftNotes}catch(e){error(e)}}
watch(()=>[props.modelValue,props.projectId],([open,id],previous)=>{if(previous && id!==previous[1]){loadVersion++;savedDraft.value='';data.value={};form.value={};notes.value='';suggestionReady.value=false;suggestionEvidence.value=[]}if(open && !dirty.value)load()},{immediate:true})
</script>

<style scoped>
.understanding-page { flex:1;min-width:0;overflow:auto;padding:28px;background:#fff; }
.page-heading { display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:24px; }
.page-heading h2 { margin:6px 0 10px;font-size:24px;color:#24334b; }
.page-heading p { margin:0;color:#64748b;line-height:1.7; }
.eyebrow { color:#526d9e;font-size:12px; }
.stage-footer { position:sticky;bottom:-28px;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 0;margin-top:24px;border-top:1px solid #e5eaf1;background:#fff;z-index:2; }
.stage-footer span { color:#64748b;font-size:13px; }
@media(max-width:900px){.understanding-page{padding:18px}.stage-footer{bottom:-18px;flex-wrap:wrap}.page-heading{flex-wrap:wrap}}

h3 { margin: 24px 0 12px; }
.hint, details { color: #64748b; font-size: 13px; line-height: 1.7; }
.overview-item { padding: 12px 16px; margin: 8px 0; background: #f8fafc; border-radius: 8px; }
.overview-item p, .source-quote { white-space: pre-wrap; line-height: 1.8; }
.classification { margin: 18px 0; }
summary { cursor: pointer; }
</style>
