import test from 'node:test'
import assert from 'node:assert/strict'
import {serviceChoices,existingServiceTypes,classificationImpact} from '../src/serviceTypes.js'
test('常用、已选、自定义建议合并；不丢失值或重复',()=>{
 assert.deepEqual(serviceChoices(['测试验证'],['古籍修复'],['测试验证','车辆维修']),['测试验证','古籍修复','车辆维修'])
})
test('筛选仅包含已保存项目，并按领域收窄',()=>{
 const projects=[{domain:'a',project_types:['自定义']},{domain:'b',project_types:['维修保养']}]
 assert.deepEqual(existingServiceTypes(projects,'a'),['自定义'])
 assert.deepEqual(existingServiceTypes(projects),['自定义','维修保养'])
})
test('范围、领域、分类修改分别提示影响，不要求一律重生成',()=>{
 const base={domain:'a',technical_objects:['车辆'],service_content:'维修'}
 assert.match(classificationImpact(base,{...base,project_types:['维修保养']}),/无需自动重生成/)
 assert.match(classificationImpact(base,{...base,service_content:'保养'}),/受影响章节/)
 assert.match(classificationImpact(base,{...base,domain:'b'}),/重新匹配素材/)
})
