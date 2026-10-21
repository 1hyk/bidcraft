import test from 'node:test'
import assert from 'node:assert/strict'
import {arrangementPreview,persistArrangements} from '../src/utils/arrangementBatch.js'

test('批量保留已确认安排，跳过无候选，默认只采用首选',()=>{
  const rows=[{id:1,title:'A',chapter_candidates:[{id:10},{id:11}]},
    {id:2,title:'B',chapter_candidates:[{id:20}],arranged_chapters:[{id:21}]},
    {id:3,title:'C',chapter_candidates:[]},
    {id:4,title:'D',chapter_candidates:[{id:40}],arranged_chapters:[{id:41}],arrangement_stale:true}]
  assert.deepEqual(arrangementPreview(rows),[{id:1,title:'A',nodeIds:[10]},{id:4,title:'D',nodeIds:[40]}])
  assert.equal(rows[0].chapter_candidates.length,2)
})
test('一条多个章节一次保存，失败继续并保留成功统计',async()=>{
  const calls=[]
  const result=await persistArrangements([{id:1,title:'A',nodeIds:[10,11]},{id:2,title:'B',nodeIds:[20]},{id:3,title:'C',nodeIds:[30]}],async item=>{
    calls.push(item); if(item.id===2) throw Error('failed')
  })
  assert.deepEqual(calls[0].nodeIds,[10,11])
  assert.equal(calls.length,3)
  assert.deepEqual(result,{completed:2,failed:['B']})
})
test('离开项目后不继续提交后续条目',async()=>{
  let current=true, calls=0
  const result=await persistArrangements([{title:'A'},{title:'B'}],async()=>{calls++;current=false},()=>current)
  assert.equal(calls,1)
  assert.equal(result.completed,1)
})
