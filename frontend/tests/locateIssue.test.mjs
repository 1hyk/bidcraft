import test from 'node:test'
import assert from 'node:assert/strict'
import { findIssueRange } from '../src/locateIssue.js'
const doc = {descendants(fn){fn({isTextblock:true},0);fn({isText:true,text:'系统至少'},1);fn({isText:true,text:'[待补充：数量]'},5);fn({isText:true,text:'架'},13)}}
test('跨格式文字可以定位，不改文档',()=>assert.deepEqual(findIssueRange(doc,'[待补充：数量]'),{from:5,to:13}))
test('不存在的片段不猜位置',()=>assert.equal(findIssueRange(doc,'不存在'),null))
import {findImageIssue} from '../src/locateIssue.js'
const images={descendants(fn){fn({type:{name:'materialImage'},attrs:{assetId:'IMG-a',caption:'新的图名'}},7);fn({type:{name:'materialImage'},attrs:{assetId:'IMG-b',caption:'现场图'}},19)}}
test('图片按ID定位，不受改名影响',()=>assert.deepEqual(findImageIssue(images,{asset_id:'IMG-a',excerpt:'旧图名'}),{from:7,image:true}))
test('不存在的ID不回退同名图片',()=>assert.equal(findImageIssue(images,{asset_id:'IMG-missing',excerpt:'现场图'}),null))
test('旧报告可以精确图名定位图片节点',()=>assert.deepEqual(findImageIssue(images,{excerpt:'现场图（AI生成示意）'}),{from:19,image:true}))
