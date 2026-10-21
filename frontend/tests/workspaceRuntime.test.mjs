import test from 'node:test'
import assert from 'node:assert/strict'
import {mountWorkspace} from './workspaceHarness.mjs'
test('工作台实际setup可挂载，并能从第一步切换到目录规划',async()=>{
 const app=await mountWorkspace()
 try {
  assert.equal(app.state.currentWorkflowStep,1)
  await app.state.navigateWorkflow('planning')
  assert.equal(app.state.currentWorkflowStep,2)
  assert.equal(app.state.showDirectoryPlanning,false)
  assert.equal(app.state.showProposalCheck,false)
 } finally {app.close()}
})
test('自动保存失败后，恢复服务可重新保存并切换步骤',async()=>{
 let fail=true
 const app=await mountWorkspace({saveSectionContent:async()=>{if(fail)throw Error('暂时断开');return {data:{saved_at:'now'}}},getSectionContent:async()=>({data:{found:true,content:{body:'新稿'}}})})
 try{
  app.state.activeNodeId=7;app.state.workspaceMode='writing'
  app.state.bodyEditor={prepareExternalEdit:()=>({dirty:true,html:'新稿'})}
  await app.state.saveChapter('新稿',true)
  fail=false
  await app.state.navigateWorkflow('planning')
  assert.equal(app.state.currentWorkflowStep,2)
  assert.equal(app.state.showDirectoryPlanning,false)
  assert.equal(app.state.showProposalCheck,false)
 }finally{app.close()}
})
test('快速切换章节，迟到的旧章节内容不覆盖当前章',async()=>{
 const pending={}
 const app=await mountWorkspace({getSectionContent:id=>new Promise(resolve=>pending[id]=resolve)})
 try{
  app.state.activeNodeId=1;const a=app.state.loadContent(1)
  app.state.activeNodeId=2;const b=app.state.loadContent(2)
  pending[2]({data:{found:true,content:{body:'第二章'}}});await b
  pending[1]({data:{found:true,content:{body:'第一章'}}});await a
  assert.equal(app.state.activeChapterContent,'第二章')
 }finally{app.close()}
})
test('正文读取失败不得清空已有草稿，应显示错误状态',async()=>{
 const app=await mountWorkspace({getSectionContent:async()=>{throw Error('读取失败')}})
 try{
  app.state.activeNodeId=7;app.state.activeChapterContent='未丢失的内容'
  await app.state.loadContent(7)
  assert.equal(app.state.activeChapterContent,'未丢失的内容')
  assert.ok(app.state.contentError)
 }finally{app.close()}
})
test('切换目录节点先保存旧章草稿，再读取新章',async()=>{
 const saved=[];const bodies={7:'旧正文',8:'第八章'}
 const app=await mountWorkspace({saveSectionContent:async(id,payload)=>{saved.push(id);bodies[id]=payload.body;return {data:{}}},getSectionContent:async id=>({data:{found:true,content:{body:bodies[id]}}})})
 try{
  app.state.activeNodeId=7;app.state.activeChapterContent='草稿'
  app.state.bodyEditor={prepareExternalEdit:()=>({dirty:true,html:'草稿'})}
  await app.state.onTocNodeClick({id:8,title:'第八章',status:'generated'})
  assert.deepEqual(saved,[7]);assert.equal(bodies[7],'草稿')
  assert.equal(app.state.activeNodeId,8);assert.equal(app.state.activeChapterContent,'第八章')
 }finally{app.close()}
})
test('切章保存失败时仍停留旧章，不销毁草稿',async()=>{
 const app=await mountWorkspace({saveSectionContent:async()=>{throw Error('断开')}})
 try{
  app.state.activeNodeId=7;app.state.activeChapterContent='草稿'
  app.state.bodyEditor={prepareExternalEdit:()=>({dirty:true,html:'草稿'})}
  await app.state.onTocNodeClick({id:8,title:'第八章'})
  assert.equal(app.state.activeNodeId,7);assert.equal(app.state.activeChapterContent,'草稿')
 }finally{app.close()}
})
test('读取失败状态禁止将旧章内容保存到新章',async()=>{
 let saves=0
 const app=await mountWorkspace({saveSectionContent:async()=>{saves++;return {data:{}}}})
 try{
  app.state.activeNodeId=8;app.state.contentError='读取失败'
  await app.state.saveChapter('旧章内容')
  assert.equal(saves,0)
 }finally{app.close()}
})

test('离开项目之前保存草稿，失败则阻止跳转',async()=>{
 const app=await mountWorkspace({saveSectionContent:async()=>{throw Error('网络故障')}})
 try{
  app.state.activeNodeId=7
  app.state.bodyEditor={prepareExternalEdit:()=>({dirty:true,html:'未保存'})}
  assert.equal(await app.state.guardLeavingWorkspace(),false)
 }finally{app.close()}
})

test('目录是独立整理模式，设置与检查互斥，关闭设置保留原工作区',async()=>{
 const app=await mountWorkspace()
 try{
  await app.state.navigateWorkflow('planning')
  assert.equal(app.state.currentWorkflowStep,2)
  assert.equal(app.state.showDirectoryPlanning,false)
  await app.state.openPlanningSettings()
  assert.equal(app.state.showDirectoryPlanning,true)
  await app.state.openProposalCheck()
  assert.equal(app.state.showDirectoryPlanning,false)
  assert.equal(app.state.showProposalCheck,true)
  await app.state.navigateWorkflow('planning')
  assert.equal(app.state.showProposalCheck,false)
  assert.equal(app.state.currentWorkflowStep,2)
  await app.state.openPlanningSettings()
  app.state.showDirectoryPlanning=false
  assert.equal(app.state.currentWorkflowStep,2)
 }finally{app.close()}
})

test('离开思路编辑时保存思路，失败则保留原模式和草稿',async()=>{
 const app=await mountWorkspace({putNodeOutline:async()=>{throw Error('offline')}})
 try{
  app.state.workspaceMode='planning';app.state.activeNodeId=7
  app.state.activeOutline={thinking:'原思路',key_points:[]}
  app.state.outlineEditing=true
  app.state.editOutline={thinking:'修改后的思路',need_table:false,need_image:false}
  app.state.editKpText='新要点'
  await app.state.navigateWorkflow('writing')
  assert.equal(app.state.workspaceMode,'planning')
  assert.equal(app.state.outlineEditing,true)
  assert.equal(app.state.editOutline.thinking,'修改后的思路')
 }finally{app.close()}
})
