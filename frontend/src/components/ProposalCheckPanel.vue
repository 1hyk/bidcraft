<template>
  <section class="proposal-check-page" aria-label="检查与导出">
    <header class="check-heading"><div><span>定稿准备</span><h2>检查、补全与导出</h2><p>核对技术响应和真实材料，修改后复查，最后导出排版文件。</p></div><el-button @click="emit('back')">收起检查</el-button></header>
    <p class="note">先查问题，再按来源补全或修改正文，处理后复查。条款核验需手动启动；补全不会自动把要求标为通过。</p>
    <div class="actions">
      <el-button :disabled="busy || reviewing || writing" @click="openFill()">从公司资料补全</el-button>
      <el-button @click="emit('company')">管理公司资料</el-button>
      <el-button @click="emit('pages')">测量与调整页数</el-button>
    </div>
    <p class="note" v-if="company">公司库：{{ usableFacts }} 条已确认有效资料 · {{ pendingFacts }} 条待审核。资料按需选用，此数量不代表本项目要求已满足。</p>
    <el-tabs v-model="tab">
      <el-tab-pane label="技术要求响应" name="requirements">
        <div class="actions">
          <el-button :disabled="reviewing" :loading="busy" @click="load">刷新结果</el-button>
          <el-button type="primary" :loading="reviewing" :disabled="busy || !assessment?.items?.length" @click="review">{{ assessment?.has_assessment ? '重新核验条款' : '开始核验条款' }}</el-button>
        </div>
        <p class="note">{{ assessment?.note }}</p>
        <details v-if="compilationItems.length" class="source-details">
          <summary>编制与排版提醒（{{ compilationItems.length }} 条，不计入正文缺项）</summary>
          <section v-for="item in compilationItems" :key="item.requirement_id">
            <strong>{{ item.title }}</strong>
            <p>{{ item.content }}</p>
            <p class="note">{{ item.gap }}</p>
            <a v-if="item.source_document" :href="item.source_document.url" :download="item.source_file">下载招标原文件核对附件</a>
          </section>
        </details>
        <el-alert v-if="assessment?.has_assessment && !assessment.fresh" type="warning" title="内容、编制设置或检查规则已变化，上次结果已过期。请重新核验；旧证据不作为当前依据。" :closable="false" />
        <p v-if="assessment?.checked_at" class="note">上次核验：{{ new Date(assessment.checked_at).toLocaleString() }} · {{ assessment.fresh ? '与当前保存版本一致' : '已过期' }}</p>
        <p v-if="assessment?.summary">共 {{ assessment.summary.total }} 条 · 正文义务覆盖 {{ assessment.summary.covered }} · 部分响应 {{ assessment.summary.partial }} · 未找到证据 {{ assessment.summary.missing }} · 待最终环节核对 {{ assessment.summary.pending_stage || 0 }} · 未完成核验 {{ assessment.summary.unverified }}（其中核验失败 {{ assessment.summary.review_errors || 0 }}）</p>
        <div class="filters">
          <el-select v-model="status" placeholder="全部状态" clearable aria-label="按响应状态筛选"><el-option v-for="(label,key) in labels" :key="key" :label="label" :value="key" /></el-select>
          <el-select v-model="category" placeholder="全部类别" clearable aria-label="按要求类别筛选"><el-option v-for="name in categories" :key="name" :label="name" :value="name" /></el-select>
          <el-input v-model="search" clearable placeholder="搜索要求或缺项" aria-label="搜索招标要求" />
        </div>
        <el-empty v-if="!busy && !assessment?.items?.length" description="当前标段没有可核验要求，请先解析招标文件并检查要求清单" />
        <el-empty v-else-if="!busy && !filtered.length" description="没有符合筛选条件的条款" />
        <el-collapse v-loading="busy">
          <el-collapse-item v-for="item in filtered" :key="item.requirement_id" :name="item.requirement_id">
            <template #title><span class="requirement-title"><el-tag :type="tagType(item.status)" size="small">{{ item.review_state === 'error' ? '核验未完成，请重试' : (labels[item.status] || '待核验') }}</el-tag><strong>{{ item.title }}</strong><span class="note">{{ item.category }}<template v-if="item.weight != null"> · {{ item.weight }} 分</template></span></span></template>
            <div class="requirement-content">
              <div class="comparison-grid">
                <section class="compare-pane tender-pane">
                  <header class="compare-head"><strong>招标要求</strong><span>左侧核对依据</span></header>
                  <div class="compare-scroll">
                    <p class="source">{{ item.source_file || '未记录来源文件' }} · {{ item.source_location || '未记录原文位置' }}</p>
                    <p class="note">以下为解析提取的要求，可能是摘要；需要时在下方展开解析原文。</p>
                    <div class="review-document" v-html="reviewMarkup(item.content)" />
                    <details v-if="item.source_document" class="source-details" @toggle="event => event.target.open && loadSource(item)">
                      <summary>在此展开招标解析原文</summary>
                      <p class="note">选择来源章节查看完整解析文本；此处不保证自动精确定位，原始版式请下载原件核对。</p>
                      <p v-if="sourceState(item).loading" class="note">加载原文中…</p>
                      <p v-if="sourceState(item).error" class="gap">{{ sourceState(item).error }}</p>
                      <p v-if="sourceState(item).locationNote" class="note">{{ sourceState(item).locationNote }}</p>
                      <el-button v-if="sourceState(item).excerpt" size="small" @click="sources[item.requirement_id].showExcerpt = !sourceState(item).showExcerpt">{{ sourceState(item).showExcerpt ? '查看完整章节' : '仅看命中段落及前后文' }}</el-button>
                      <el-select v-if="sourceState(item).sections?.length" :model-value="sourceState(item).selected" filterable placeholder="选择原文章节" style="width:100%" @change="id=>readSource(item,id)">
                        <el-option v-for="section in sourceState(item).sections" :key="section.id" :value="section.id" :label="section.sec_path || section.title || String(section.id)" />
                      </el-select>
                      <div v-if="sourceState(item).text" class="review-document source-text" v-html="reviewMarkup(sourceState(item).showExcerpt ? sourceState(item).excerpt : sourceState(item).text)" />
                    </details>
                    <a v-if="item.source_document" :href="item.source_document.url" :download="item.source_file || '招标原文件'">下载招标原文件</a>
                  </div>
                </section>
                <section class="compare-pane body-pane">
                  <header class="compare-head"><strong>{{ item.review_state === 'error' ? '检索到的相关正文' : '对应正文证据' }}</strong><span>右侧核对响应</span></header>
                  <div class="compare-scroll">
                    <p v-if="!item.evidence_verified" class="note">{{ item.evidence?.length ? '片段可回查，但整项证据尚未通过核验，不作为已响应结论。' : '尚无有效响应证据；未核验或核验失败不代表正文一定缺项。' }}</p>
                    <blockquote v-for="e in item.evidence" :key="e.id">
                      <el-button link type="primary" @click="go(e.node_id)">打开章节修改：{{ e.chapter }}</el-button>
                      <div class="review-document" v-html="reviewMarkup(e.text)" />
                    </blockquote>
                    <div v-if="!item.evidence?.length && item.target_nodes?.length">
                      <p class="note">按标题建议检查，非响应证据：</p>
                      <el-button v-for="n in item.target_nodes" :key="n.node_id" link type="primary" @click="go(n.node_id)">{{ n.chapter }}</el-button>
                    </div>
                    <p v-else-if="!item.evidence?.length && item.suggested_chapter" class="note">抽取建议章节：{{ item.suggested_chapter }}</p>
                  </div>
                </section>
              </div>
              <div class="comparison-result">
                <strong>{{ item.review_state === 'error' ? '核验情况' : '核对意见与下一步' }}</strong>
                <p>{{ item.gap || '未返回具体缺项；请结合左侧要求与右侧措施核对完整性。' }}</p>
                <p v-for="check in item.artifact_checks || []" :key="check.quote" class="note">{{check.status === 'verified' ? '当前导出页码已核对' : '最终文件待核对'}}：{{ check.quote }}。{{ check.note }}</p>
                <ul v-if="item.obligations?.length">
                  <li v-for="part in item.obligations" :key="part.obligation_id">
                    <strong>{{ stageLabels[part.stage] }} · {{ obligationStatus[part.status] || '待核对' }}</strong>：{{ part.quote }}
                    <p v-if="part.gap" class="note">{{ part.gap }}</p>
                  </li>
                </ul>
                <p v-if="item.compilation_notes?.length" class="note">本条另含编制或排版指引：{{ item.compilation_notes.join(' ') }} 此部分应核对附件与导出，不以正文声明判断。</p>
                <ul v-if="item.missing_points?.length"><li v-for="point in item.missing_points" :key="point">未充分响应的要求：{{ point }}</li></ul>
                <p v-if="relatedIssues(item).length" class="note">另有关联待处理事项 {{ relatedIssues(item).length }} 条，仅在“问题处理”中统计，不因此自动降低覆盖结论。</p>
                <el-button v-if="relatedIssues(item).length" size="small" @click="showRelated(item)">查看关联待处理事项</el-button>
                <p class="note">{{ item.review_state === 'error' ? '本条尚无有效核验结论。请先核对原文与正文，或重试核验；此状态不表示正文缺项。' : item.status === 'unverified' ? '请先核验或人工对照，再决定是否修改正文。' : item.status === 'pending_stage' ? '正文范围已核对，最终导出、递交或真实材料仍须按列出的义务检查；当前不能判为整项完全满足，无需为此重生成正文。' : item.status === 'missing' ? '本次候选中未找到证据，请先核对全文；不要直接重生成。' : item.status === 'partial' ? '请核对列出的具体不足，优先局部修改；真实证明缺失不能靠重生成解决。' : '正文义务已逐项取得证据，请人工确认措施充分且承诺准确；这不等于外部事实或最终文件已经验收。' }}</p>
                <el-button size="small" :disabled="busy || reviewing" :loading="reviewing" @click="review">重新核验技术要求</el-button>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-tab-pane>
      <el-tab-pane label="问题处理" name="issues">
        <p v-if="linkedIssues !== null" class="note">当前仅显示所选技术要求关联的事项。<el-button link @click="linkedIssues=null">显示全部问题</el-button></p>
        <el-button :loading="busy" :disabled="reviewing" @click="load">刷新缺项</el-button>
        <el-switch v-model="showHandled" active-text="查看已处理事项" aria-label="查看已处理事项" />
        <p class="note">待处理 {{ report?.issues?.length || 0 }} 条，已处理 {{ report?.handled_issues?.length || 0 }} 条。定位修改、保存后刷新；需人工核对的事项可确认通过，不改变技术条款核验结论。</p>
        <p v-if="report">检查 {{ report.chapters }} 节，当前待处理 {{ report.issues?.length || 0 }} 条：{{ issueSummary }}。此统计包含图片核对等事项，不仅统计待补占位。</p>
        <div class="filters"><el-select v-model="issueKind" clearable placeholder="全部问题类型"><el-option v-for="(label,key) in issueGroups" :key="key" :label="label" :value="key" /></el-select></div>
        <el-table :data="filteredIssues" empty-text="当前筛选未发现规则问题，仍须人工审核">
          <el-table-column prop="chapter" label="章节" width="170" />
          <el-table-column prop="message" label="问题" min-width="200" />
          <el-table-column label="对应内容" min-width="220"><template #default="{row}"><strong>{{ row.excerpt }}</strong><p v-if="row.context" class="note">{{ row.context }}</p></template></el-table-column>
          <el-table-column label="处理方式" min-width="220"><template #default="{row}">
            <p class="note">{{ issueAdvice(row) }}</p>
            <p v-if="row.workflow_note" class="note">{{ row.workflow_note }}</p>
            <p v-if="row.resolution" class="note">{{ actionLabel(row.resolution.action) }}：{{ row.resolution.note }} · {{ new Date(row.resolution.checked_at).toLocaleString() }}</p>
            <template v-if="!row.resolution">
              <el-button v-if="reviewKinds.has(row.kind)" link type="primary" :disabled="busy || writing" @click="handleIssue(row,'confirmed')">人工确认通过</el-button>
              <span v-else class="note">实际缺项请定位修改后刷新检查，无需手动点通过。</span>
            </template>
            <el-button v-else link :disabled="busy || writing" @click="handleIssue(row,'reopen')">重新打开</el-button>
            <el-button v-if="row.kind === 'pagination'" link type="primary" @click="emit('pages')">查看页数</el-button>
          </template></el-table-column>
          <el-table-column label="定位" width="110"><template #default="{row}"><el-button v-if="row.node_id" link type="primary" @click="go(row.node_id,row.excerpt,row.asset_id)">定位正文</el-button></template></el-table-column>
        </el-table>
        <details v-if="report?.resolution_history?.length">
          <summary>查看人工处理记录（{{ report.resolution_history.length }} 条，含历史版本）</summary>
          <el-table :data="report.resolution_history.slice().reverse()">
            <el-table-column prop="chapter" label="章节" width="170" />
            <el-table-column prop="excerpt" label="当时内容" min-width="180" />
            <el-table-column label="操作" width="170"><template #default="{row}">{{ actionLabel(row.action) }}<span v-if="row.workflow_status"> · {{ row.workflow_status==='completed' ? '当前正文已完成操作' : '待操作后复查' }}</span></template></el-table-column>
            <el-table-column prop="note" label="处理说明" min-width="200" />
            <el-table-column label="配图变化" min-width="180"><template #default="{row}"><p v-if="row.asset_id">原素材：{{ row.asset_id }}</p><p v-if="row.replacement_asset_ids?.length">新引用：{{ row.replacement_asset_ids.join('、') }}</p></template></el-table-column>
            <el-table-column label="时间" width="170"><template #default="{row}">{{ new Date(row.checked_at).toLocaleString() }}</template></el-table-column>
            <el-table-column label="返回问题处理" width="160"><template #default="{row}">
              <el-button v-if="currentHandled(row)" link type="primary" :disabled="busy || writing" @click="restoreIssue(currentHandled(row))">恢复待处理</el-button>
              <span v-else class="note">{{ currentActive(row) ? '已在待处理清单' : '历史版本，当前未检出同项' }}</span>
            </template></el-table-column>
          </el-table>
        </details>
      </el-tab-pane>
    </el-tabs>
    <footer class="check-footer"><span>核验结果辅助人工审查；导出文件仍须核对页码、图表和承诺。</span><el-button type="primary" :disabled="!canExport || writing || busy || reviewing || showFill" :loading="exporting" @click="emit('export')">导出 Word</el-button></footer>
  <CompanyFillDrawer v-model="showFill" :project-id="projectId" :node-id="fillNode" @changed="filled" />
  </section>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { checkProposal, resolveProposalIssue, reviewProposal, proposalRequirements, getCompanyProfile, fileSections, fileSectionContent, locateRequirementSource } from '../api'
import CompanyFillDrawer from './CompanyFillDrawer.vue'
import { reviewMarkup } from '../reviewMarkup'
const props = defineProps({ modelValue: Boolean, projectId: [String, Number], nodeId: [String, Number], writing: Boolean, canExport:Boolean, exporting:Boolean })
const emit = defineEmits(['update:modelValue', 'locate', 'changed', 'company', 'pages', 'source', 'back', 'export'])
const showFill=ref(false), fillNode=ref(null), company=ref(null), issueKind=ref('')
const linkedIssues=ref(null)
const showHandled=ref(false)
const currentHandled=row=>(report.value?.handled_issues || []).find(i=>i.issue_id===row.issue_id && i.fingerprint===row.fingerprint && i.resolution?.checked_at===row.checked_at)
const currentActive=row=>(report.value?.issues || []).some(i=>i.issue_id===row.issue_id && i.fingerprint===row.fingerprint)
async function restoreIssue(row){
  await handleIssue(row,'reopen')
  if(currentActive(row)){showHandled.value=false;issueKind.value='';linkedIssues.value=null;tab.value='issues'}
}
const reviewKinds=new Set(['image_review','claim_review','person_role_review','structure_review'])
const actionLabel=a=>({confirmed:'人工确认可用／依据充分',false_positive:'人工确认误报',not_applicable:'旧版不适用（需重新处理）',reopen:'重新打开',replace_requested:'替换图片',remove_requested:'移除图片',edit_requested:'修改正文后复查'})[a] || a
async function handleIssue(row,action){
  const project=props.projectId
  try {
    const {value}=await ElMessageBox.prompt('请填写核对依据或处理说明。人工确认不会修改正文，也不会改变条款核验结论。','记录处理结果',{inputType:'textarea',inputValidator:v=>!!v?.trim() || '请填写说明'})
    if(project!==props.projectId)return
    busy.value=true
    const {data}=await resolveProposalIssue(project,{issue_id:row.issue_id,fingerprint:row.fingerprint,action,note:value})
    if(project===props.projectId){report.value=data;linkedIssues.value=null;ElMessage.success('处理记录已保存')}
  } catch(e){if(e!=='cancel' && e!=='close')error(e)}
  finally{if(project===props.projectId)busy.value=false}
}
const usableFacts=computed(()=>(company.value?.entries || []).filter(f=>f.confirmed && !f.expired && !f.conflict && (!f.doc_id || f.source_status==='extracted')).length)
const pendingFacts=computed(()=>(company.value?.entries || []).filter(f=>f.status==='pending').length)
const sources = ref({})
const sourceState = item => sources.value[item.requirement_id] || {}
async function loadSource(item) {
  if (sourceState(item).sections || sourceState(item).loading) return
  const project=props.projectId, state={loading:true,sections:null,text:'',selected:null,error:''}
  sources.value[item.requirement_id]=state
  try {
    const {data}=await fileSections(project,item.source_document.file_id)
    if(project!==props.projectId)return
    sources.value[item.requirement_id]={...state,loading:false,sections:data,error:data.length?'':'暂无可读取章节，请下载原件核对。'}
    if(data.length){
      const {data:location}=await locateRequirementSource(project,item.source_document.file_id,item.requirement_id)
      if(project!==props.projectId)return
      sources.value[item.requirement_id]={...sourceState(item),locationNote:location.message}
      if(location.matched){
        await readSource(item,location.section_id)
        if(project===props.projectId && sourceState(item).selected===location.section_id)
          sources.value[item.requirement_id]={...sourceState(item),locationNote:location.message,excerpt:location.excerpt,showExcerpt:!!location.excerpt}
      }
    }
  } catch(e){ if(project===props.projectId)sources.value[item.requirement_id]={...state,loading:false,error:'解析原文读取失败，请重试或下载原件。'} }
}
async function readSource(item,id) {
  const project=props.projectId
  sources.value[item.requirement_id]={...sourceState(item),selected:id,loading:true,text:'',error:'',excerpt:'',showExcerpt:false,locationNote:''}
  try {
    const {data}=await fileSectionContent(project,item.source_document.file_id,id)
    if(project===props.projectId && sourceState(item).selected===id)sources.value[item.requirement_id]={...sourceState(item),loading:false,text:data.text || '该章节暂无文本'}
  } catch(e){if(project===props.projectId && sourceState(item).selected===id)sources.value[item.requirement_id]={...sourceState(item),loading:false,error:'原文读取失败，可重新选择章节重试。'} }
}

const issueGroups={missing_evidence:'待补资料与项目安排',claim_review:'事实与承诺核对',person_role_review:'姓名、电话与人员核对',images:'图片',pagination:'页数',other:'正文与其他'}
const groupOf=k=>k?.includes('image')?'images':issueGroups[k]?k:'other'
const issueSummary=computed(()=>Object.entries(issueGroups).map(([key,label])=>[label,(report.value?.issues || []).filter(i=>groupOf(i.kind)===key).length]).filter(([,count])=>count).map(([label,count])=>`${label} ${count} 条`).join('，') || '未检出待处理规则问题，仍须人工审核')
function relatedIssues(item){
  if(!assessment.value?.fresh)return []
  return (report.value?.issues || []).filter(row=>(item.evidence || []).some(e=>{
    if(Number(e.node_id)!==Number(row.node_id))return false
    if(!row.excerpt)return false
    if(row.kind==='missing_evidence'){
      const markers=row.excerpt.match(/\[(?:待补充|待确认)[^\]]*\]|【(?:待补充|待确认)[^】]*】/g) || []
      return markers.some(marker=>e.text.includes(marker))
    }
    return e.text.includes(row.excerpt)
  }))
}
function showRelated(item){linkedIssues.value=relatedIssues(item);issueKind.value='';tab.value='issues'}
const filteredIssues=computed(()=>(showHandled.value ? report.value?.handled_issues ?? [] : linkedIssues.value ?? report.value?.issues ?? []).filter(r=>!issueKind.value || groupOf(r.kind)===issueKind.value))
function canUseCompany(row){return row.kind==='missing_evidence' && /资质|证书|姓名|业绩|公司|本单位|设备型号|真实材料/.test(row.excerpt || '')}
function issueAdvice(row){
  const kind=row.kind
  if(canUseCompany(row))return '包含可能需要真实材料的内容，可查公司库；如还包含本次安排，请分开核对，不自动当作公司事实。'
  if(kind==='missing_evidence' && /人数|岗位|投入|排班|指派|天数|周期/.test(row.excerpt || ''))return '主要涉及本次拟实施安排：打开章节完善合理配置，并对照招标要求；不一定需要上传公司文件。'
  if(kind==='missing_evidence' && /阈值|指标|百分比|Hz|ms|毫秒|具体数值/.test(row.excerpt || ''))return '先查本项目招标原文与关键参数；若为方案设计值，应明确拟采用条件，不能冒充实测结果。'
  if(kind==='missing_evidence')return '需人工区分真实材料、本次安排或不必要占位；打开章节处理，不默认要求补公司资料。'
  if(kind==='pagination')return '先测量当前版本的实际页数，再决定是否调整。'
  if(groupOf(kind)==='images')return '核对图名、来源和内容；真实证明需补原件，方案关系图可用 Mermaid。'
  return '打开章节核对依据或修改方案，处理后刷新；不能靠补充公司资料解决所有问题。'
}
function openFill(id){ if(props.writing)return; fillNode.value=id || props.nodeId || null; showFill.value=true }
async function filled(id){emit('changed',id);await load();ElMessage.info('正文已更新，问题清单已刷新；条款核验需重新运行')}
const busy = ref(false), reviewing = ref(false), report = ref(null), assessment = ref(null)
const tab = ref('requirements'), status = ref(''), category = ref(''), search = ref('')
const labels = { covered: '正文义务覆盖，待复核', partial: '部分响应', missing: '未找到证据', pending_stage:'待最终环节核对', unverified: '尚未取得核验结论', not_applicable: '需在其他文件核查' }
const stageLabels={body:'正文',export:'最终导出',submission:'递交前',external_evidence:'真实证明'}
const obligationStatus={covered:'有响应证据',partial:'部分响应',missing:'未找到证据',unverified:'核验未完成',pending_stage:'待该环节核对'}
const tagType = s => ({ covered: 'primary', partial: 'warning', missing: 'danger', unverified: 'info', not_applicable: 'info' }[s] || 'info')
const technicalItems = computed(() => (assessment.value?.items || []).filter(i=>i.response_scope === 'technical'))
const compilationItems = computed(() => (assessment.value?.items || []).filter(i=>i.response_scope === 'compilation_check'))
const categories = computed(() => [...new Set(technicalItems.value.map(i => i.category))])
const filtered = computed(() => technicalItems.value.filter(i =>
  (!status.value || i.status === status.value) && (!category.value || i.category === category.value) &&
  (!search.value.trim() || `${i.title} ${i.content} ${i.gap}`.includes(search.value.trim()))))
let requestVersion = 0
async function go(id,excerpt='',asset_id='') {
  try {
    const { data } = await proposalRequirements(props.projectId)
    if (assessment.value?.fresh && (!data.fresh || data.signature !== assessment.value.signature || data.requirements_signature !== assessment.value.requirements_signature)) {
      assessment.value = data
      ElMessage.warning('正文或要求已变化，请重新核验后查看当前证据')
      return
    }
    emit('locate', excerpt || asset_id ? {node_id:Number(id),excerpt,asset_id} : Number(id))
  } catch (e) { error(e) }
}
const error = e => ElMessage.error(e?.response?.data?.detail || e.message)
async function load() {
  const version = ++requestVersion, project = props.projectId
  busy.value = true
  linkedIssues.value=null
  try {
    const results = await Promise.allSettled([proposalRequirements(project), checkProposal(project), getCompanyProfile()])
    if (version !== requestVersion || project !== props.projectId) return
    if (results[0].status === 'fulfilled') assessment.value = results[0].value.data
    else { assessment.value = null; error(results[0].reason) }
    if (results[1].status === 'fulfilled') report.value = results[1].value.data
    else { report.value = null; error(results[1].reason) }
    if(results[2].status==='fulfilled')company.value=results[2].value.data
    else {company.value=null;error(results[2].reason)}
  } finally { if (version === requestVersion) busy.value = false }
}
async function review() {
  const version = ++requestVersion, project = props.projectId
  reviewing.value = true
  try {
    const { data } = await reviewProposal(project)
    if (version === requestVersion && project === props.projectId) assessment.value = data
  } catch (e) { if (version === requestVersion) { error(e); await load() } }
  finally {
    reviewing.value = false
    if (version !== requestVersion && project === props.projectId && props.modelValue) await load()
  }
}
watch(() => [props.modelValue, props.projectId], ([open, project], previous) => {
  if (previous && project !== previous[1]) { sources.value={};showFill.value=false; company.value=null; assessment.value = null; report.value = null; status.value = ''; category.value = ''; search.value = '';issueKind.value='' }
  if (open) load()
}, { immediate: true })
</script>

<style scoped>
.proposal-check-page { flex:1;min-width:0;overflow:auto;padding:28px;background:#fff; }
.check-heading { display:flex;justify-content:space-between;gap:20px;margin-bottom:22px; }
.check-heading h2 { margin:6px 0 10px;color:#24334b;font-size:24px; }
.check-heading span { font-size:12px;color:#526d9e; }.check-heading p { color:#64748b;margin:0;line-height:1.7; }
.check-footer { display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px 0;border-top:1px solid #e5eaf1;margin-top:24px; }
.check-footer span { font-size:13px;color:#64748b; }
@media(max-width:900px){.proposal-check-page{padding:18px}.check-footer,.check-heading{flex-wrap:wrap}}

.actions,.filters { display:flex;gap:10px;margin:12px 0;flex-wrap:wrap }
.filters .el-select { width:200px }.filters .el-input { flex:1;min-width:180px }
.note,.source { color:var(--text-2);font-size:12px;line-height:1.7 }
.requirement-title { display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:8px 0;line-height:1.6 }
.requirement-content { padding:0 8px }.requirement-content h4 { margin:16px 0 6px }
.original { white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8 }
.gap { padding:10px;background:#fff7e8;border-left:3px solid #d9a441 }
blockquote { margin:10px 0;padding:12px;background:var(--paper-warm);border-left:3px solid #8da9c4 }

.comparison-grid { display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr);gap:16px;margin:12px 0 }
.compare-pane { min-width:0;border:1px solid #dfe6f0;border-radius:9px;overflow:hidden;background:#fff }
.compare-head { display:flex;justify-content:space-between;gap:12px;padding:12px 16px;background:#f2f6fc;border-bottom:1px solid #dfe6f0 }
.compare-head span { color:#8190a6;font-size:12px }
.compare-scroll { padding:12px 16px;max-height:58vh;overflow:auto;overscroll-behavior:contain }
.compare-scroll blockquote { margin:0 0 14px }.compare-scroll .original { font-size:13px;line-height:1.9 }
.source-details { margin:16px 0;padding:12px;background:#f7f9fd;border-radius:6px }.source-details summary { color:#315bd6;cursor:pointer }
.source-text { margin-top:14px;border-top:1px solid #dfe6f0;padding-top:12px }
.comparison-result { padding:14px 18px;background:#fff9ec;border:1px solid #f1e2bc;border-radius:8px;margin-bottom:16px }.comparison-result p { line-height:1.8 }
.review-document { font-size:14px;line-height:1.85;color:#334155;overflow-wrap:anywhere; }
.review-document :deep(p) { margin:10px 0;white-space:pre-wrap; }
.review-document :deep(h1),.review-document :deep(h2) { font-size:18px;line-height:1.6;margin:18px 0 12px;color:#1e293b; }
.review-document :deep(h3) { font-size:16px;margin:16px 0 10px;color:#26364e; }
.review-document :deep(h4),.review-document :deep(h5),.review-document :deep(h6) { font-size:14px;margin:14px 0 8px;color:#334155; }
.review-document :deep(ul),.review-document :deep(ol) { padding-left:24px;margin:10px 0; }.review-document :deep(li) { margin:6px 0; }
.review-document :deep(table) { display:block;width:100%;overflow-x:auto;border-collapse:collapse;margin:14px 0;font-size:12px; }
.review-document :deep(th),.review-document :deep(td) { border:1px solid #dce3ed;padding:8px 10px;min-width:70px;vertical-align:top; }
.review-document :deep(th) { background:#edf2fa;font-weight:600;color:#334155; }
.review-document :deep(pre) { white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fb;padding:10px;border-radius:6px;font-size:12px; }
.review-document :deep(blockquote) { padding:8px 12px;border-left:3px solid #b8c7df;background:#f7f9fc;margin:10px 0; }
@media(max-width:850px) { .comparison-grid { grid-template-columns:1fr }.compare-scroll { max-height:45vh } }
</style>
