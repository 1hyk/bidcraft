import {Node, mergeAttributes} from '@tiptap/core'
import {VueNodeViewRenderer} from '@tiptap/vue-3'
import PageReferenceView from './components/PageReferenceView.vue'

// 页码是章节引用，不把估算数字当成可编辑正文。
export const PageReference = Node.create({
  name:'pageReference', group:'inline', inline:true, atom:true,
  addOptions(){return {projectId:null}},
  addAttributes(){return {nodeId:{default:null,
    parseHTML:el=>el.getAttribute('data-page-reference'),
    renderHTML:attrs=>({'data-page-reference':attrs.nodeId})}}},
  parseHTML(){return [{tag:'span[data-page-reference]'}]},
  renderHTML({HTMLAttributes}){return ['span',mergeAttributes(HTMLAttributes,{class:'page-reference'}),'排版后生成']},
  addNodeView(){return VueNodeViewRenderer(PageReferenceView)},
})
