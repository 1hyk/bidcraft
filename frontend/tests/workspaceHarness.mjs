import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {fileURLToPath} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import {build} from 'esbuild'
import {createRenderer,h,nextTick} from 'vue'
const require=createRequire(import.meta.url)
export async function mountWorkspace(overrides={}) {
 const source=await readFile(new URL('../src/views/WorkspaceView.vue',import.meta.url),'utf8')
 const script=compileScript(parse(source).descriptor,{id:'workspace-unit'})
 const imports=script.scriptSetupAst.filter(n=>n.type==='ImportDeclaration' && n.source.value==='../api').flatMap(n=>n.specifiers.map(s=>s.imported.name))
 const api=new Proxy(overrides,{get(target,key){return target[key] || (async()=>({data:key==='listDomains'?{items:[]}:['getChapterTree','listFiles','listFacts','listProjects'].includes(key)?[]:{}}))}})
 const envKey='__workspaceUnit'+Math.random().toString(36).slice(2)
 globalThis[envKey]={api,messages:[]}
 const bundled=await build({stdin:{contents:script.content,resolveDir:fileURLToPath(new URL('../src/views/',import.meta.url)),loader:'js'},bundle:true,write:false,format:'cjs',platform:'node',external:['vue'],plugins:[{name:'unit-boundaries',setup(b){
  b.onResolve({filter:/\.vue$/},a=>({path:a.path,namespace:'child'}))
  b.onLoad({filter:/.*/,namespace:'child'},()=>({contents:'export default {}'}))
  b.onResolve({filter:/^\.\.\/api$/},()=>({path:'api',namespace:'api'}))
  b.onLoad({filter:/.*/,namespace:'api'},()=>({contents:imports.map(k=>`export const ${k}=(...args)=>globalThis[${JSON.stringify(envKey)}].api.${k}(...args)`).join('\n')}))
  b.onResolve({filter:/^(element-plus|vue-router|marked)$/},a=>({path:a.path,namespace:'stub'}))
  b.onLoad({filter:/.*/,namespace:'stub'},a=>({contents:a.path==='vue-router'?'export const useRouter=()=>({push:async()=>{}});export const onBeforeRouteLeave=()=>{};export const onBeforeRouteUpdate=()=>{}':a.path==='marked'?'export const marked={parse:s=>s,setOptions:()=>{}}':`const m={};for(const k of ['error','warning','info','success'])m[k]=v=>globalThis[${JSON.stringify(envKey)}].messages.push([k,v]);export const ElMessage=m;export const ElMessageBox={confirm:async()=>{},prompt:async()=>({value:''})}` }))
 }}]})
 const module={exports:{}}
 new Function('require','module','exports',bundled.outputFiles[0].text)(require,module,module.exports)
 const component=module.exports.default
 component.render=()=>h('div')
 const renderer=createRenderer({createElement:tag=>({tag,children:[]}),insert:(el,parent)=>parent.children.push(el),remove:()=>{},createText:text=>({text}),createComment:text=>({text}),setText:()=>{},setElementText:()=>{},parentNode:()=>null,nextSibling:()=>null,patchProp:()=>{}})
 const app=renderer.createApp(component,{projectId:'23'})
 const instance=app.mount({children:[]})
 await nextTick();await new Promise(r=>setImmediate(r))
 return {state:instance.$.setupState,messages:globalThis[envKey].messages,close(){app.unmount();delete globalThis[envKey]}}
}
