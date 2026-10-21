import {Node,mergeAttributes} from '@tiptap/core'
import {reactive} from 'vue'
import {VueNodeViewRenderer} from '@tiptap/vue-3'
import MaterialImageView from './components/MaterialImageView.vue'
import {imageAssets} from './api'
import {displayImageCaption} from './imageCaption'

const requests=new Map()
export function loadImageAssets(id,refresh=false) {
  if(refresh)requests.delete(id)
  if(!requests.has(id))requests.set(id,imageAssets(id).then(r=>r.data.items).catch(e=>{requests.delete(id);throw e}))
  return requests.get(id)
}
export function materializeImages(html) {
  const root=document.createElement('div');root.innerHTML=html
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT)
  const texts=[];while(walker.nextNode())texts.push(walker.currentNode)
  for(const text of texts) {
    if(text.parentElement?.closest('pre,code,[data-material-image]'))continue
    const regex=/\[图片占位[：:]\s*([^\]|]+)\|素材ID=(IMG-[a-f0-9]{24})(\|自定义图注=1)?\]/g
    let match,last=0;const fragment=document.createDocumentFragment()
    while((match=regex.exec(text.textContent))) {
      fragment.append(text.textContent.slice(last,match.index))
      const span=document.createElement('span');span.dataset.materialImage='true';span.dataset.assetId=match[2];span.dataset.caption=displayImageCaption(match[1]);span.dataset.customCaption=match[3] ? 'true' : 'false';span.textContent=match[0];fragment.append(span);last=regex.lastIndex
    }
    if(last){fragment.append(text.textContent.slice(last));text.replaceWith(fragment)}
  }
  return root.innerHTML
}
export const MaterialImage=Node.create({
  name:'materialImage',group:'inline',inline:true,atom:true,
  addOptions(){return {projectId:null,nodeId:null,onChanged:()=>{},pendingActions:reactive(new Map())}},
  addAttributes(){return {
    assetId:{default:'',parseHTML:el=>el.dataset.assetId,renderHTML:a=>({'data-asset-id':a.assetId})},
    caption:{default:'',parseHTML:el=>displayImageCaption(el.dataset.caption),renderHTML:a=>({'data-caption':a.caption})},
    history:{default:'[]',parseHTML:el=>el.dataset.history || '[]',renderHTML:a=>({'data-history':a.history})},
    customCaption:{default:false,parseHTML:el=>el.dataset.customCaption==='true',renderHTML:a=>({'data-custom-caption':String(a.customCaption)})},
  }},
  parseHTML(){return [{tag:'span[data-material-image]'}]},
  renderHTML({node,HTMLAttributes}){return ['span',mergeAttributes(HTMLAttributes,{'data-material-image':'true'}),`[图片占位：${displayImageCaption(node.attrs.caption)}|素材ID=${node.attrs.assetId}${node.attrs.customCaption?'|自定义图注=1':''}]`]},
  addNodeView(){return VueNodeViewRenderer(MaterialImageView)},
})
