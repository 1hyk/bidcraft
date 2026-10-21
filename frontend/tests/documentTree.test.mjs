import test from 'node:test'
import assert from 'node:assert/strict'
import {splitDocumentTree} from '../src/documentTree.js'
const tree=[{id:1,title:'方案',children:[
  {id:2,title:'响应索引',outline:{_document_role:'scoring_index'}},
  {id:3,title:'实施安排',children:[{id:4,title:'步骤'}]},
]}]
test('明确需要索引时前置分组，原树不变且正文顺序保留',()=>{
  const original=JSON.stringify(tree)
  const result=splitDocumentTree(tree,true)
  assert.deepEqual(result.frontMatter.map(n=>n.id),[2])
  assert.deepEqual(result.body[0].children.map(n=>n.id),[3])
  assert.equal(result.body[0].children[0].children[0].id,4)
  assert.equal(JSON.stringify(tree),original)
})
test('没有要求时不产生前置分组，也不隐藏已有内容',()=>{
  const result=splitDocumentTree(tree,false)
  assert.deepEqual(result.frontMatter,[])
  assert.deepEqual(result.body,tree)
})
test('普通章节仅同名也不作为前置索引',()=>{
  const result=splitDocumentTree([{id:8,title:'响应索引'}],true)
  assert.equal(result.frontMatter.length,0)
  assert.equal(result.body[0].id,8)
})
