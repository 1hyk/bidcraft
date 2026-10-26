<template>
  <node-view-wrapper as="span" class="material-image" contenteditable="false">
    <span v-if="loading">正在读取图片…</span>
    <span v-else-if="!asset" class="image-error">图片暂不可用：{{ displayImageCaption(node.attrs.caption) }} <el-button link @click="load(true)">重试</el-button></span>
    <template v-else>
      <img :src="asset.url" :alt="node.attrs.caption" @click="zoom=true" @error="failed=true" />
      <span v-if="failed" class="image-error">图片加载失败，请重试或替换。</span>
      <span class="caption">{{ figureTitle(node.attrs.caption) }}</span>
      <span v-if="asset.generated" class="image-meta">AI生成示意<span v-if="asset.caption?.includes('数据仅作示例，非实测结果')"> · 数据仅作示例，非实测结果</span></span>
      <span class="image-meta">{{ asset.width }} × {{ asset.height }} 像素 <span v-if="asset.review_pending"> · 待人工核对</span></span>
      <span v-if="asset.width && asset.width<1600" class="image-meta">原图分辨率偏低，满页打印可能不清晰，可重新生成高清图。</span>
      <span class="image-actions" v-if="editor.isEditable">
        <el-button size="small" @click="zoom=true">放大查看</el-button>
        <el-button size="small" @click="rename">修改图名</el-button>
        <el-button size="small" data-image-action="replace_requested" @click="openReplace">替换 / 修改图片</el-button>
        <el-button size="small" :disabled="!history.length" @click="rollback">回退上一张</el-button>
        <el-button size="small" data-image-action="remove_requested" @click="remove">删除配图</el-button>
      </span>
    </template>
    <el-dialog v-model="zoom" append-to-body width="90%" :title="node.attrs.caption"><img class="zoom-image" :src="asset?.url" /></el-dialog>
    <el-dialog v-model="replaceOpen" append-to-body width="760px" title="替换本张图片" :close-on-click-modal="!busy" :show-close="!busy" :close-on-press-escape="!busy">
      <p>只替换这张图片，原图保留供回退。生成结果需点击“采用此图”才写入正文。</p>
      <el-tabs v-model="tab">
        <el-tab-pane label="AI重新设计" name="generate">
          <img :src="asset.url" alt="本次重新设计的参考原图" style="max-width:100%;max-height:240px;object-fit:contain" />
          <el-input v-model="feedback" type="textarea" :rows="3" placeholder="描述新图应保留的主题、希望改变的构图和不要出现的内容。" />
          <p><el-radio-group v-model="quality"><el-radio value="high">高清（保持原图比例）</el-radio><el-radio value="standard">标准（保持原图比例）</el-radio></el-radio-group></p>
          <p>先看当前图、结合你的意见整理新绘图要求，再设计新图；不保证保留原构图。会调用已配置的模型并计费。候选需确认后替换，原图保留；流程关系图请使用Mermaid单图修改。</p>
          <el-button :loading="busy" :disabled="busy || !feedback.trim()" @click="regenerate">生成重新设计候选</el-button>
        </el-tab-pane>
        <el-tab-pane label="素材库" name="library">
          <el-input v-model="search" placeholder="搜索图名" />
          <div class="asset-grid"><button v-for="a in filtered" :key="a.asset_id" @click="candidate=a"><img :src="a.url" loading="lazy" /><span>{{ a.caption }}</span></button></div>
        </el-tab-pane>
        <el-tab-pane label="上传图片" name="upload"><input type="file" accept="image/png,image/jpeg,image/webp" :disabled="busy" @change="upload" /></el-tab-pane>
      </el-tabs>
      <div v-if="candidate" class="candidate"><img :src="candidate.url" /><p>{{ candidate.caption }}</p><p>{{ candidate.review_reason }}</p><el-button type="primary" :disabled="busy" @click="adopt">采用此图</el-button></div>
      <template #footer><el-button :disabled="busy" @click="replaceOpen=false">关闭，保留原图</el-button></template>
    </el-dialog>
  </node-view-wrapper>
</template>
<script setup>
import {ref,computed,onMounted,watch} from 'vue'
import {NodeViewWrapper,nodeViewProps} from '@tiptap/vue-3'
import {ElMessage,ElMessageBox} from 'element-plus'
import {loadImageAssets} from '../materialImages'
import {displayImageCaption,figureTitle} from '../imageCaption'
import {regenerateImage,uploadImageAsset} from '../api'
const props=defineProps(nodeViewProps)
const assets=ref([]),loading=ref(false),failed=ref(false),zoom=ref(false),replaceOpen=ref(false),busy=ref(false),candidate=ref(null),feedback=ref(''),quality=ref('high'),tab=ref('generate'),search=ref('')
const options=()=>props.extension.options
const asset=computed(()=>assets.value.find(a=>a.asset_id===props.node.attrs.assetId))
const history=computed(()=>{try{return JSON.parse(props.node.attrs.history || '[]')}catch{return []}})
const filtered=computed(()=>assets.value.filter(a=>a.asset_id!==props.node.attrs.assetId && (a.caption || '').includes(search.value)))
const error=e=>ElMessage.error(e?.response?.data?.detail || e.message || '图片操作失败')
async function load(refresh=false){loading.value=true;try{assets.value=await loadImageAssets(options().projectId,refresh);failed.value=false}catch(e){error(e)}finally{loading.value=false}}
onMounted(()=>load())
watch(()=>[loading.value,options().pendingActions?.get(props.getPos())],([waiting,action])=>{
  if(waiting || !action || !props.editor.isEditable)return
  options().pendingActions.delete(props.getPos())
  if(action==='replace_requested')openReplace()
  if(action==='remove_requested')remove()
},{flush:'post'})
function changed(){options().onChanged()}
async function rename(){
  if(!props.editor.isEditable)return
  try {
    const {value}=await ElMessageBox.prompt('只修改本处图片下方的独立图注；来源说明单独显示，不会自动追加到图名。','修改图名',{inputValue:figureTitle(props.node.attrs.caption),inputValidator:v=>!!v?.trim() && v.trim().length<=150 && !/[\[\]|\n\r]/.test(v) || '请输入1至150字，不含方括号、竖线或换行'})
    let caption=displayImageCaption(value)
    props.updateAttributes({caption,customCaption:true});changed()
  }catch{}
}
function select(a,h){props.updateAttributes({assetId:a.asset_id,caption:a.caption,customCaption:!!a.customCaption,history:JSON.stringify(h)});changed()}
function openReplace(){candidate.value=null;replaceOpen.value=true}
function adopt(){if(!props.editor.isEditable || !candidate.value)return;select(candidate.value,[...history.value,{asset_id:props.node.attrs.assetId,caption:props.node.attrs.caption,customCaption:props.node.attrs.customCaption}].slice(-20));replaceOpen.value=false}
function rollback(){if(!props.editor.isEditable)return;const h=[...history.value],a=h.pop();if(a)select(a,h)}
async function remove(){if(!props.editor.isEditable)return;try{await ElMessageBox.confirm('从正文移除这张图？原文件仍保留，可用编辑器撤销恢复。','删除配图');props.deleteNode();changed()}catch{}}
// crypto.randomUUID 只在安全上下文（HTTPS 或 localhost）可用；用 IP + HTTP 部署时它是 undefined，
// 直接调用会在发请求前抛错，表现为「按钮点了没反应」。这里降级到时间戳+随机串。
function newRequestId(){try{if(globalThis.crypto?.randomUUID)return crypto.randomUUID()}catch{}return 'r'+Date.now().toString(36)+Math.random().toString(36).slice(2,10)}
async function regenerate(){if(busy.value)return;busy.value=true;try{const {data}=await regenerateImage(options().projectId,{asset_id:props.node.attrs.assetId,node_id:options().nodeId,feedback:feedback.value,quality:quality.value,mode:'redesign',request_id:newRequestId()});candidate.value=data;await load(true)}catch(e){error(e)}finally{busy.value=false}}
async function upload(e){const f=e.target.files?.[0];if(!f)return;busy.value=true;try{const {data}=await uploadImageAsset(options().projectId,f,props.node.attrs.caption);candidate.value=data;await load(true)}catch(err){error(err)}finally{busy.value=false;e.target.value=''}}
</script>
<style scoped>
.material-image{display:inline-block;width:100%;margin:16px 0;padding:14px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;text-align:center;box-sizing:border-box;white-space:normal}
.material-image>img{display:block;max-width:100%;max-height:480px;margin:auto;cursor:zoom-in}.caption,.image-meta,.image-actions{display:block;margin-top:10px}.caption{color:#334155;font-weight:500}.image-meta{font-size:12px;color:#8b6508}.image-actions{white-space:normal}.image-error{color:#b45309}.zoom-image{display:block;max-width:100%;height:auto;margin:auto}.asset-grid{max-height:300px;overflow:auto;display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}.asset-grid button{border:1px solid #ddd;background:white;padding:8px;cursor:pointer}.asset-grid img{width:100%;height:100px;object-fit:contain}.asset-grid span{font-size:12px;display:block}.candidate{text-align:center;margin-top:18px}.candidate img{max-width:100%;max-height:320px}
</style>
