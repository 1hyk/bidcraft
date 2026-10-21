import assert from 'node:assert/strict'
import fs from 'node:fs'
import {parse, compileScript} from '@vue/compiler-sfc'
import {reactive} from 'vue'
const read = name => fs.readFileSync(new URL('../src/' + name, import.meta.url), 'utf8')
const understanding = read('components/ProjectUnderstandingDrawer.vue')
assert.ok(!understanding.includes('v-if="data.has_directory"'))
assert.ok(!understanding.includes('label="建议写入章节"'))
assert.ok(understanding.includes(':data="technicalRequirements"'))
const {descriptor} = parse(read('components/DirectoryPlanningPanel.vue'))
let code = compileScript(descriptor, {id:'requirement-chapters-test'}).content
code = code.replace(/from ['"]vue['"]/g, `from ${JSON.stringify(new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url).href)}`)
code = code.replace("import {getUnderstanding, getDocxStyle, getPagePlan} from '../api'", 'const getUnderstanding = globalThis.__requirementsTestApi; const getDocxStyle = async()=>({data:{style:{body_size_pt:12,line_spacing:1.5}}}); const getPagePlan = async()=>({data:{plan:{target_pages:100,rewrite_tolerance:.15}}})')
let response = {has_directory:false, requirements:[{id:1, display_group:'technical', chapter_candidates:[]}]}, fail = false
globalThis.__requirementsTestApi = async () => {if (fail) throw Error('offline'); return {data:response}}
const {default:component} = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))
const events = []
const vm = component.setup(reactive({modelValue:false, projectId:20}), {expose:()=>{}, emit:(...args)=>events.push(args)})
await vm.load()
assert.equal(vm.requirements.value.length, 1, 'requirements exist before directory generation')
response = {has_directory:true, requirements:[{id:1, display_group:'technical', chapter_candidates:[{id:42, title:'方案'}]}, {id:2, display_group:'technical'}, {id:3, display_group:'bid_notices'}]}
await vm.load()
assert.equal(vm.requirements.value.length, 2, 'business notices are excluded')
assert.equal(vm.unmatched.value.length, 1)
vm.onlyUnmatched.value = true
assert.equal(vm.visibleRequirements.value[0].id, 2)
vm.locate(42)
assert.deepEqual(events, [['locate',42]])
fail = true
await vm.load()
assert.ok(vm.error.value)
assert.equal(vm.requirements.value.length, 0, 'failed reads do not show stale mappings')
console.log('Requirement workflow checks passed')

const workspace = read('views/WorkspaceView.vue')
assert.ok(!workspace.includes('showWorkspaceSettings'))
assert.ok(!workspace.includes('RequirementChaptersDrawer'))
assert.ok(workspace.includes('<DirectoryPlanningPanel v-if="isPlanning"'))
assert.ok(workspace.includes('@click="workspaceMode = \'writing\'"'))
