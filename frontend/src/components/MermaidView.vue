<template>
  <node-view-wrapper class="mermaid-view">
    <div v-if="!showSource" class="mm-render" :class="{ failed: !!error }">
      <!-- 渲染成功：SVG 图 -->
      <div v-if="loading" class="mm-loading">正在绘制图表…</div>
      <div v-else-if="svg" class="mm-svg" v-html="svg" />
      <!-- 渲染失败：降级显示源码 + 错误提示（用户可直接改） -->
      <div v-else-if="error" class="mm-error">
        <div class="mm-error-tip">流程图语法有误（{{ error }}），双击图块修改源码</div>
        <pre class="mm-src-readonly">{{ node.attrs.code }}</pre>
      </div>
      <button class="mm-src-btn" title="编辑源码" @click.stop="showSource = true">⌨ 源码</button>
      <button class="mm-ai-btn" :disabled="!editor.isEditable" @click.stop="openRevision">AI修改本图</button>
    </div>
    <div v-else class="mm-edit">
      <textarea v-model="editingCode" class="mm-textarea" spellcheck="false" />
      <div class="mm-edit-actions">
        <button class="mm-btn" @click.stop="showSource = false">取消</button>
        <button class="mm-btn primary" @click.stop="applySource">应用</button>
      </div>
    </div>
    <el-dialog v-model="revisionOpen" title="修改本张Mermaid图" width="min(820px, 94vw)" append-to-body :close-on-click-modal="!revising" :show-close="!revising">
      <p>直接描述需要修改的内容，例如“改成从左到右，去掉重复步骤”。需要重画时可写“根据本章内容重新设计”。只生成本图候选，不重写章节。</p>
      <el-input v-model="revisionFeedback" type="textarea" :rows="3" maxlength="2000" :disabled="revising" placeholder="说明要修改、保留或重新设计的内容" />
      <p><el-button :loading="revising" :disabled="!revisionFeedback.trim() || revising" @click="requestRevision">生成单图候选</el-button><small>调用当前配置的文本模型，可能消耗额度。</small></p>
      <el-alert v-if="revisionError" :title="revisionError" type="error" :closable="false" />
      <div v-if="candidateSvg" class="mm-candidate"><h4>修改候选预览</h4><div v-html="candidateSvg" /><details><summary>查看候选源码</summary><pre>{{ candidateCode }}</pre></details></div>
      <template #footer><el-button :disabled="revising" @click="revisionOpen=false">关闭，保留原图</el-button><el-button type="primary" :disabled="!candidateSvg || revising || !editor.isEditable" @click="adoptRevision">采用修改</el-button></template>
    </el-dialog>
  </node-view-wrapper>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import {ElMessage} from 'element-plus'
import {reviseMermaid} from '../api'
import { nodeViewProps, NodeViewWrapper } from '@tiptap/vue-3'
import themeVariables from '../mermaidTheme.json'
import { splitMindmap } from '../mermaidLayout'

const props = defineProps(nodeViewProps)
const svg = ref('')
const error = ref('')
const loading = ref(false)
const showSource = ref(false)
const editingCode = ref('')
const revisionOpen=ref(false),revisionFeedback=ref(''),revising=ref(false),revisionError=ref(''),candidateCode=ref(''),candidateSvg=ref('')
let revisionSource='',revisionSeq=0
function openRevision(){revisionOpen.value=true;revisionError.value='';candidateCode.value='';candidateSvg.value='';revisionSource=props.node.attrs.code || ''}
async function requestRevision(){
  if(revising.value || !props.editor.isEditable)return
  const seq=++revisionSeq
  revisionSource=props.node.attrs.code || ''
  revising.value=true;revisionError.value='';candidateSvg.value='';candidateCode.value=''
  try{
    const opts=props.extension.options
    if(!opts.projectId || !opts.nodeId)throw Error('当前图未关联项目章节，无法生成候选')
    const {data}=await reviseMermaid(opts.projectId,{node_id:opts.nodeId,code:revisionSource,feedback:revisionFeedback.value})
    if(seq!==revisionSeq)return
    const {default:mermaid}=await import('mermaid')
    mermaid.initialize({startOnLoad:false,theme:'default',securityLevel:'strict',suppressErrorRendering:true,themeVariables})
    const pieces=splitMindmap(data.code),out=[]
    for(let i=0;i<pieces.length;i++)out.push((await mermaid.render(`mm-revision-${Date.now()}-${seq}-${i}`,pieces[i])).svg)
    if(seq!==revisionSeq)return
    candidateCode.value=data.code;candidateSvg.value=out.join('')
  }catch(e){if(seq===revisionSeq)revisionError.value=e?.response?.data?.detail || '候选生成或语法渲染失败，原图未修改；请调整要求后重试。'}
  finally{if(seq===revisionSeq)revising.value=false}
}
function adoptRevision(){
  if(!props.editor.isEditable || !candidateSvg.value)return
  if(props.node.attrs.code!==revisionSource){revisionError.value='原图源码已变化，请重新生成候选，未覆盖当前图';return}
  props.updateAttributes({code:candidateCode.value})
  props.extension.options.onChanged?.()
  revisionOpen.value=false
  ElMessage.success('已采用本图修改，可使用编辑器撤销恢复')
}
onBeforeUnmount(()=>{revisionSeq++})

let renderSeq = 0

async function render(code) {
  const seq = ++renderSeq
  if (!code || !code.trim()) {
    svg.value = ''
    error.value = ''
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    // 动态引入：mermaid 体积大，按需加载不拖慢首屏
    const { default: mermaid } = await import('mermaid')
    mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'strict', suppressErrorRendering: true,
      flowchart: { htmlLabels: false, nodeSpacing: 25, rankSpacing: 20, padding: 8 }, themeVariables })
    const pieces = splitMindmap(code)
    const rendered = []
    for (let i = 0; i < pieces.length; i++) {
      const { svg: part } = await mermaid.render(`mm-${Date.now()}-${seq}-${i}`, pieces[i])
      rendered.push((pieces.length > 1 ? `<p class="mm-part">关系图分图 ${i + 1}/${pieces.length}</p>` : '') + part)
    }
    const out = rendered.join('')
    if (seq === renderSeq) {
      svg.value = out
      error.value = ''
    }
  } catch (e) {
    if (seq === renderSeq) {
      svg.value = ''
      error.value = String(e?.message || e).slice(0, 80)
    }
  } finally {
    if (seq === renderSeq) loading.value = false
  }
}

function applySource() {
  props.updateAttributes({ code: editingCode.value })
  props.extension.options.onChanged?.()
  showSource.value = false
}

watch(() => props.node.attrs.code, (c) => render(c), { immediate: false })
onMounted(() => render(props.node.attrs.code))

function onSourceOpen() {
  editingCode.value = props.node.attrs.code || ''
}
watch(showSource, (v) => { if (v) onSourceOpen() })
</script>

<style scoped>
.mm-ai-btn { position:absolute;right:84px;bottom:4px;border:1px solid #dbe3f0;border-radius:4px;background:white;color:#315bd6;padding:3px 8px;cursor:pointer;font-size:12px; }
.mm-candidate { overflow:auto;max-height:420px; }.mm-candidate :deep(svg){max-width:100%;height:auto}.mm-candidate pre{white-space:pre-wrap;font-size:12px}
.mermaid-view { margin: 14px 0; }
.mm-render {
  position: relative; border: 1px solid var(--line); border-radius: var(--radius-s);
  background: var(--paper-warm); padding: 16px; min-height: 60px; overflow-x: auto;
}
.mm-svg :deep(svg) { max-width: 100%; height: auto; display: block; margin: 0 auto; }
.mm-error-tip { font-size: 12px; color: var(--red); margin-bottom: 8px; }
.mm-loading { font-size: 12px; color: var(--text-3); }
.mm-src-readonly { font-size: 11px; color: var(--text-2); margin: 0; white-space: pre-wrap; }
.mm-src-btn {
  position: absolute; top: 6px; right: 6px; font-size: 11px; padding: 2px 8px;
  border: 1px solid var(--line-strong); border-radius: var(--radius-s);
  background: var(--card); color: var(--text-3); cursor: pointer; opacity: 0.4; transition: opacity 0.15s;
}
.mm-render:hover .mm-src-btn { opacity: 1; }
.mm-src-btn:hover { color: var(--brand); border-color: var(--brand); }
.mm-edit { border: 1px solid var(--brand); border-radius: var(--radius-s); padding: 10px; background: var(--card); }
.mm-textarea {
  width: 100%; min-height: 120px; font-family: var(--font-num); font-size: 12px;
  border: 1px solid var(--line); border-radius: var(--radius-s); padding: 8px; resize: vertical;
  outline: none;
}
.mm-edit-actions { display: flex; justify-content: flex-end; gap: 6px; margin-top: 8px; }
.mm-btn {
  font-size: 12px; padding: 4px 12px; border-radius: var(--radius-s);
  border: 1px solid var(--line-strong); background: var(--card); color: var(--text-2); cursor: pointer;
}
.mm-btn.primary { background: var(--brand); border-color: var(--brand); color: #fff; }
</style>
