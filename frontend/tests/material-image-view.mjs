import assert from 'node:assert/strict'
import fs from 'node:fs'
import {parse,compileScript} from '@vue/compiler-sfc'
import {reactive} from 'vue'
const {descriptor}=parse(fs.readFileSync(new URL('../src/components/MaterialImageView.vue',import.meta.url),'utf8'))
let code=compileScript(descriptor,{id:'material-image-test'}).content
code=code.replace(/from ['"]vue['"]/g,`from ${JSON.stringify(new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js',import.meta.url).href)}`)
code=code.replace("import {NodeViewWrapper,nodeViewProps} from '@tiptap/vue-3'",'const NodeViewWrapper={},nodeViewProps={}')
code=code.replace("import {ElMessage,ElMessageBox} from 'element-plus'",'const ElMessage={error:()=>{}},ElMessageBox={confirm:async()=>{},prompt:async()=>({value:"修改后的图名"})}')
code=code.replace("import {displayImageCaption,figureTitle} from '../imageCaption'",'const displayImageCaption=v=>v,figureTitle=v=>v')
code=code.replace("import {loadImageAssets} from '../materialImages'",'const loadImageAssets=async()=>[]')
code=code.replace("import {regenerateImage,uploadImageAsset} from '../api'",'const regenerateImage=()=>{throw Error("must not call model")},uploadImageAsset=()=>{}')
const {default:component}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
let saved=0,removed=0
const node=reactive({attrs:{assetId:'old',caption:'原图',history:'[]'}})
const vm=component.setup({node,extension:{options:{onChanged:()=>saved++,projectId:20,nodeId:706}},editor:{isEditable:true},updateAttributes:a=>Object.assign(node.attrs,a),deleteNode:()=>removed++},{expose:()=>{}})
vm.candidate.value={asset_id:'new',caption:'新图'}
assert.equal(node.attrs.assetId,'old','candidate does not replace current image')
vm.adopt();assert.equal(node.attrs.assetId,'new');assert.equal(saved,1)
vm.rollback();assert.equal(node.attrs.assetId,'old');assert.equal(saved,2)
await vm.remove();assert.equal(removed,1)
console.log('Material image view: candidate adoption, history rollback, deletion passed')

vm.assets.value=[{asset_id:'old',generated:true,caption:'旧名（数据仅作示例，非实测结果）'}]
await vm.rename()
assert.equal(node.attrs.caption,'修改后的图名','rename must not reappend metadata')
assert.equal(node.attrs.customCaption,true)
console.log('Rename preserves exact user title without automatic suffixes')
