// Exercise the real compiled Vue setup with mocked local APIs; no model calls or business writes.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {parse,compileScript} from '@vue/compiler-sfc'
import {reactive,nextTick} from 'vue'

const path=new URL('../src/components/GenerationSetupDialog.vue',import.meta.url)
const {descriptor}=parse(fs.readFileSync(path,'utf8'))
let code=compileScript(descriptor,{id:'generation-setup-test'}).content
const vueUrl=new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js',import.meta.url).href
code=code.replace(/from ['"]vue['"]/g,`from ${JSON.stringify(vueUrl)}`)
code=code.replace(/import \{ElMessage\} from 'element-plus'/,"const ElMessage={error:()=>{}}")
code=code.replace(/import \{getDocxStyle, getPagePlan, savePagePlan\} from '..\/api'/,
  'const {getDocxStyle,getPagePlan,savePagePlan}=globalThis.__generationSetupTestApi')
code=code.replace(/import (StyleDrawer|PagesDrawer) from '[^']+'/g,'const $1={} ')

let plan={target_pages:null,rewrite_tolerance:.15},failLoad=false,failSave=false,writes=[]
globalThis.__generationSetupTestApi={
  getDocxStyle:async()=>{if(failLoad)throw Error('unavailable');return {data:{style:{body_size_pt:12,line_spacing:1.5}}}},
  getPagePlan:async()=>({data:{plan,coefficient:{value:600},nodes:[{override:800}],options:{target_pages_range:[1,2000]}}}),
  savePagePlan:async(id,value)=>{if(failSave)throw Error('save failed');writes.push({id,value})},
}
const {default:component}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
const events=[],props=reactive({modelValue:false,projectId:19,chapterTitle:'',pendingCount:2})
const vm=component.setup(props,{expose:()=>{},emit:(...args)=>events.push(args)})
await vm.load(false)
assert.equal(vm.canStart.value,false,'unset target needs an explicit choice')
vm.mode.value='target';vm.target.value=null
assert.equal(vm.canStart.value,false,'missing page count cannot generate')
vm.target.value=100
assert.equal(vm.canStart.value,true)
assert.equal(vm.overrides.value,1)
vm.styleOpen.value=true
assert.equal(vm.canStart.value,false,'must finish editing style before generation')
vm.styleOpen.value=false
await nextTick()
failSave=true
await vm.confirm()
assert.equal(events.length,0,'failed saving must not start generation')
assert.equal(vm.saving.value,false)
failSave=false
await vm.confirm()
assert.deepEqual(writes[0].value,{target_pages:100,rewrite_tolerance:.15},'no chapter overrides are overwritten')
assert.equal(events[0][0],'confirmed')
events.length=0
vm.mode.value='unlimited'
await vm.confirm()
assert.equal(writes.at(-1).value.target_pages,null,'explicit unlimited selection clears the target')
props.pendingCount=0
assert.equal(vm.canStart.value,false,'completed projects cannot accidentally launch a batch')
props.chapterTitle='质量控制'
assert.equal(vm.canStart.value,true,'single chapter remains available with its own warning')
failLoad=true
await vm.load(false)
assert.equal(vm.canStart.value,false,'failed loading blocks generation')
assert.ok(vm.error.value)
failLoad=false;plan={target_pages:150,rewrite_tolerance:.15}
await vm.load(false)
assert.equal(vm.target.value,150)
vm.target.value=200
await vm.load(true)
assert.equal(vm.target.value,200,'style refresh preserves the user draft page target')
delete globalThis.__generationSetupTestApi
console.log('Generation setup: all behavior checks passed (no model calls).')
