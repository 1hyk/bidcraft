import assert from 'node:assert/strict'
import fs from 'node:fs'
const source = fs.readFileSync(new URL('../src/views/WorkspaceView.vue',import.meta.url),'utf8')
const body = source.slice(source.indexOf('async function generateOne(data) {'),source.indexOf('\nfunction launchGenerateOne'))
assert.ok(body && !body.includes('showGenerationSetup'), 'single chapter must not open book setup')
let found=false, cancel=false, fail=false, dirty=false, calls=[]
const deps={genNodeId:{value:null},stage:{value:'done'},bodyEditor:{value:{prepareExternalEdit:()=>({dirty,html:'人工修改'})}},pendingBodySave:Promise.resolve(),activeNodeId:{value:7},saveSectionContent:async(id,b)=>calls.push(['save',id,b.body]),getSectionContent:async()=>{if(fail)throw Error('read failed');return {data:{found}}},ElMessageBox:{confirm:async()=>{calls.push(['confirm']);if(cancel)throw 'cancel'}},ElMessage:{warning:m=>calls.push(['warning']),error:m=>calls.push(['error'])},launchGenerateOne:n=>calls.push(['launch',n.id])}
const generate=new Function(...Object.keys(deps),body+';return generateOne')(...Object.values(deps))
await generate({id:8,title:'章节'})
assert.deepEqual(calls,[['launch',8]])
calls=[];found=true;cancel=true
await generate({id:8,title:'章节'})
assert.deepEqual(calls,[['confirm']], 'cancel preserves existing body')
calls=[];cancel=false;dirty=true
await generate({id:8,title:'章节'})
assert.deepEqual(calls,[['save',7,'人工修改'],['confirm'],['launch',8]], 'flush edits before replacement')
calls=[];fail=true
await generate({id:8,title:'章节'})
assert.ok(!calls.some(c=>c[0]==='launch'), 'failed read blocks generation')
calls=[];deps.stage.value='writing'
await generate({id:8,title:'章节'})
assert.deepEqual(calls,[['warning']], 'concurrent batch blocks single generation')
console.log('Single chapter checks passed: reuse settings, preserve edits, cancel, errors, busy state')
