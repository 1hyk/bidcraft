<template>
  <el-dialog class="generation-setup" :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" title="确认编写设置" width="min(760px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" append-to-body>
    <p class="intro">先确认版式和篇幅，系统再按当前设置编写待生成章节。</p>
    <div v-loading="loading" class="setup-content">
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <template v-if="ready">
        <section class="setup-section">
          <div class="section-title"><h3>1 · 排版方案</h3><el-button :disabled="saving" @click="styleOpen = true">调整排版</el-button></div>
          <p class="style-description">A4 · {{ style.body_font }} {{ style.body_size_pt }} 磅 · {{ style.line_spacing }} 倍行距</p>
          <p class="muted">页边距：上 {{ style.margin_top_cm }}、下 {{ style.margin_bottom_cm }}、左 {{ style.margin_left_cm }}、右 {{ style.margin_right_cm }} 厘米</p>
          <p class="muted">以上为当前生效版式，未自定义时使用系统默认值。调整后保存即可更新下方估算。</p>
        </section>
        <section class="setup-section">
          <h3>2 · 正文篇幅</h3>
          <el-radio-group v-model="mode" :disabled="saving" aria-label="正文篇幅控制方式">
            <el-radio value="target">控制目标页数</el-radio><el-radio value="unlimited">不限制篇幅</el-radio>
          </el-radio-group>
          <div v-if="mode === 'target'" class="target-input">
            <label for="generation-target-pages">目标正文页数</label>
            <el-input-number id="generation-target-pages" v-model="target" :min="range[0]" :max="range[1]" :precision="0" :disabled="saving" aria-label="目标正文页数" />
            <span>页（不含封面、目录）</span>
          </div>
          <p v-if="mode === 'unlimited'" class="muted">按技术内容组织篇幅，不设置总页数目标。</p>
          <p v-if="!mode" class="selection-warning">请选择是否控制页数，避免使用未经确认的篇幅设置。</p>
          <div v-if="mode === 'target' && validTarget" class="estimate-grid">
            <div><small>允许范围 · ±{{ Math.round(tolerance * 100) }}%</small><strong>{{ Math.ceil(target*(1-tolerance)-1e-8) }}–{{ Math.floor(target*(1+tolerance)+1e-8) }} 页</strong></div>
            <div><small>当前版式参考</small><strong>约 {{ Math.round(coefficient) }} 字 / 页</strong></div>
            <div><small>总字数预算参考</small><strong>约 {{ Math.round(target*coefficient).toLocaleString() }} 字</strong></div>
          </div>
          <p class="muted">图表、表格和分页会影响实际页数；以上为预算参考，成稿后可测量核对。</p>
          <el-button link :disabled="saving" @click="pagesOpen = true">查看章节字数分配与页数测量</el-button>
          <p v-if="overrides" class="selection-warning">有 {{ overrides }} 个章节使用手动字数额度；确认前请检查是否仍适合本次目标页数。</p>
        </section>
        <section class="setup-section final-note">
          <h3>3 · 本次编写范围</h3>
          <el-alert v-if="!chapterTitle && pendingCount === 0" title="当前没有待写章节，可关闭面板继续检查或编辑正文。修改设置不会自动重写已有内容。" type="info" :closable="false" />
          <p v-if="chapterTitle">生成当前章节「{{ chapterTitle }}」。如本章已有正文，将被本次结果替换，请先保存手动修改。</p>
          <p v-else>仅生成已有编写安排、尚待生成的章节；已生成和已确认的正文保留。</p>
          <p class="muted">修改设置对后续生成生效，已有正文不会自动缩写或扩写。篇幅超出目标时，可在检查与补全中测量和调整。</p>
        </section>
      </template>
    </div>
    <template #footer>
      <el-button :disabled="saving" @click="$emit('update:modelValue', false)">暂不生成</el-button>
      <el-button v-if="error" :disabled="saving" @click="load(false)">重新加载</el-button>
      <el-button type="primary" :disabled="!canStart" :loading="saving" @click="confirm">确认设置并开始生成</el-button>
    </template>
  </el-dialog>
  <StyleDrawer v-model="styleOpen" :project-id="projectId" @saved="$emit('style-saved')" />
  <PagesDrawer v-model="pagesOpen" :project-id="projectId" />
</template>

<script setup>
import {ref, computed, watch} from 'vue'
import {ElMessage} from 'element-plus'
import {getDocxStyle, getPagePlan, savePagePlan} from '../api'
import StyleDrawer from './StyleDrawer.vue'
import PagesDrawer from './PagesDrawer.vue'
const props=defineProps({modelValue:Boolean, projectId:[Number,String],chapterTitle:{type:String,default:''},pendingCount:{type:Number,default:0}})
const emit=defineEmits(['update:modelValue','confirmed','style-saved'])
const loading=ref(false),saving=ref(false),ready=ref(false),error=ref('')
const style=ref({}),mode=ref(''),target=ref(null),range=ref([1,2000]),coefficient=ref(0),tolerance=ref(.15),overrides=ref(0)
const styleOpen=ref(false),pagesOpen=ref(false)
const validTarget=computed(()=>Number.isInteger(target.value) && target.value>=range.value[0] && target.value<=range.value[1])
const canStart=computed(()=>ready.value && !loading.value && !saving.value && !styleOpen.value && !pagesOpen.value && (props.chapterTitle || props.pendingCount>0) && (mode.value==='unlimited' || (mode.value==='target' && validTarget.value && coefficient.value>0)))
async function load(preserve=false){
  loading.value=true;ready.value=false;error.value=''
  try{
    const [a,b]=await Promise.all([getDocxStyle(props.projectId),getPagePlan(props.projectId)])
    style.value=a.data.style;coefficient.value=Number(b.data.coefficient?.value || 0)
    tolerance.value=b.data.plan?.rewrite_tolerance ?? .15
    range.value=b.data.options?.target_pages_range || [1,2000]
    overrides.value=(b.data.nodes || []).filter(n=>n.override!=null).length
    if(!preserve){target.value=b.data.plan?.target_pages ?? null;mode.value=target.value ? 'target' : ''}
    ready.value=true
  }catch(e){error.value=e?.response?.data?.detail || '设置加载失败，请重试后再生成'}
  finally{loading.value=false}
}
async function confirm(){
  if(!canStart.value)return
  saving.value=true
  try{
    // 稀疏提交：保留用户在篇幅面板设置的各章手动额度。
    await savePagePlan(props.projectId,{target_pages:mode.value==='target'?target.value:null,rewrite_tolerance:tolerance.value})
    emit('confirmed');emit('update:modelValue',false)
  }catch(e){ElMessage.error(e?.response?.data?.detail || '设置保存失败，尚未启动生成')}
  finally{saving.value=false}
}
watch(()=>props.modelValue,v=>{if(v)load(false)},{immediate:true})
watch(styleOpen,(v,old)=>{if(old && !v && props.modelValue)load(true)})
watch(pagesOpen,(v,old)=>{if(old && !v && props.modelValue)load(false)})
</script>

<style scoped>
.intro{margin-top:0;color:#64748b;line-height:1.7}.setup-content{min-height:160px}.setup-section{padding:16px 0;border-bottom:1px solid #e5e7eb}.setup-section h3{margin:0 0 12px;font-size:15px}.section-title{display:flex;align-items:center;justify-content:space-between}.section-title h3{margin:0}.style-description{font-size:16px;font-weight:600}.muted{font-size:13px;line-height:1.7;color:#64748b}.target-input{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:16px 0}.estimate-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0}.estimate-grid>div{padding:14px;background:#f0f6ff;border-radius:8px}.estimate-grid small{display:block;color:#64748b;margin-bottom:8px}.estimate-grid strong{color:#2556a4;font-size:17px}.selection-warning{font-size:13px;color:#9a6700;line-height:1.7}.final-note{border:0}.final-note p{line-height:1.7}@media(max-width:600px){.estimate-grid{grid-template-columns:1fr}.target-input{gap:8px}}
</style>

<style>
.generation-setup.el-dialog{display:flex;flex-direction:column;max-height:90vh;margin-top:5vh;margin-bottom:0}
.generation-setup .el-dialog__body{overflow-y:auto;min-height:0;padding-right:8px}
.generation-setup .el-dialog__header,.generation-setup .el-dialog__footer{flex-shrink:0}
</style>
