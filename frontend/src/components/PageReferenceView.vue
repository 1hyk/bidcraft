<template>
  <NodeViewWrapper as="span" contenteditable="false" class="page-reference">
    <select :value="node.attrs.nodeId" aria-label="页码对应章节" title="选择页码对应章节，实际页码在导出排版后生成" @focus="load" @change="updateAttributes({nodeId:$event.target.value})" @mousedown.stop>
      <option v-if="!options.some(n=>String(n.id)===String(node.attrs.nodeId))" :value="node.attrs.nodeId">章节待核对</option>
      <option v-for="n in options" :key="n.id" :value="n.id">{{n.title}}</option>
    </select>
    <small>排版后生成</small>
    <span v-if="error" role="status">章节读取失败，请重试</span>
  </NodeViewWrapper>
</template>
<script setup>
import {ref,onMounted} from 'vue'
import {NodeViewWrapper,nodeViewProps} from '@tiptap/vue-3'
import {getChapterTree} from '../api'
const props=defineProps(nodeViewProps)
const options=ref([]),error=ref(false)
async function load(){
  error.value=false
  try {
    const {data}=await getChapterTree(props.extension.options.projectId)
    const rows=[]
    function walk(nodes,path=[]){for(const n of nodes){if(n.outline?._document_role==='scoring_index')continue;rows.push({id:n.id,title:[...path,n.title].join(' / ')});walk(n.children || [],[...path,n.title])}}
    walk(data);options.value=rows
  }catch{error.value=true}
}
onMounted(load)
</script>
<style scoped>
select{width:100%;max-width:10rem;font:inherit;font-size:12px;color:#476384;border:0;border-bottom:1px dashed #a7b8cc;background:transparent;padding:2px;}
small{display:block;font-size:11px;color:#7b8797;margin-top:2px;}
</style>
