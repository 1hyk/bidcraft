import test from 'node:test'
import assert from 'node:assert/strict'
import {workflowStep,workflowMode,openUnderstandingOnEntry,understandingBasis,saveBeforeWorkflow} from '../src/workflowStep.js'
test('无目录时仍在项目理解，不因招标已解析自动跳到目录设置',()=>{
  assert.equal(workflowStep({hasDirectory:false,stage:'idle'}),1)
  assert.equal(openUnderstandingOnEntry({directoryLoaded:true,hasDirectory:false,stage:'idle'}),true)
})
test('已有目录项目不自动弹项目理解',()=>{
  assert.equal(openUnderstandingOnEntry({directoryLoaded:true,hasDirectory:true,stage:'done'}),false)
  assert.equal(workflowStep({hasDirectory:true,stage:'done'}),3)
})
test('用户主动选第二步与进行中的任务不被首次引导打断',()=>{
  assert.equal(openUnderstandingOnEntry({directoryLoaded:true,hasDirectory:false,mode:'planning'}),false)
  assert.equal(openUnderstandingOnEntry({directoryLoaded:true,hasDirectory:false,stage:'generating_toc'}),false)
  assert.equal(workflowStep({hasDirectory:false,mode:'planning'}),2)
})
test('加载失败时不按空目录误弹新项目引导',()=>assert.equal(openUnderstandingOnEntry({directoryLoaded:false,hasDirectory:false}),false))

test('四步显式页面状态不被生成完成或旧报告覆盖',()=>{
 for (const [mode,step] of [['understanding',1],['planning',2],['writing',3],['checking',4]]) {
  assert.equal(workflowStep({mode,hasDirectory:true,stage:'done'}),step)
  assert.equal(workflowMode({mode,hasDirectory:true,stage:'writing'}),mode)
 }
})
test('恢复正在编写的项目优先显示正文，不误落项目理解',()=>{
 assert.equal(workflowMode({hasDirectory:false,stage:'writing'}),'writing')
 assert.equal(workflowMode({hasDirectory:true,stage:'idle'}),'writing')
})
test('自身分类保存不应要求重复确认，招标依据变化必须重新核对',()=>{
 const a={lot:'A',requirements:[{id:1,content:'原要求'}],files:[{id:2,status:'parsed'}],project:{domain:'a'},fingerprint:'old'}
 const b={...a,project:{domain:'b'},fingerprint:'new'}
 assert.equal(understandingBasis(a),understandingBasis(b))
 assert.notEqual(understandingBasis(a),understandingBasis({...b,requirements:[{id:1,content:'新要求'}]}))
 assert.notEqual(understandingBasis(a),understandingBasis({...b,lot:'B'}))
})

test('恢复目录生成任务时显示规划页面，即使目录尚未保存',()=>{
 assert.equal(workflowMode({hasDirectory:false,stage:'generating_toc'}),'planning')
})
test('步骤切换先等待自动保存，再保存编辑器草稿并回读',async()=>{
 const events=[]
 let release
 const pendingSave=new Promise(resolve=>{release=resolve})
 const task=saveBeforeWorkflow({prepare:()=>({dirty:true,html:'草稿'}),pendingSave,nodeId:7,
 save:async(id,payload)=>{events.push(['save',id,payload.body])},reload:async id=>events.push(['reload',id])})
 assert.deepEqual(events,[])
 release();await task
 assert.deepEqual(events,[['save',7,'草稿'],['reload',7]])
})
test('保存失败应中止导航，不读取旧内容覆盖草稿',async()=>{
 let reloaded=false
 await assert.rejects(saveBeforeWorkflow({prepare:()=>({dirty:true,html:'草稿'}),pendingSave:Promise.resolve(),nodeId:7,
 save:async()=>{throw new Error('保存失败')},reload:async()=>{reloaded=true}}),/保存失败/)
 assert.equal(reloaded,false)
})

test('目录候选位置变化不冒充招标依据变化',()=>{
 const base={requirements:[{id:1,content:'工期30天',chapter_candidates:[]}]}
 assert.equal(understandingBasis(base),understandingBasis({requirements:[{id:1,content:'工期30天',chapter_candidates:[{id:2,title:'实施安排'}]}]}))
})
