import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services import project_understanding as service


class Understanding(unittest.TestCase):
    def test_only_confirmed_company_facts_enter_generation(self):
        profile={'name':'测试公司','positioning':'软件服务','entries':[
            {'content':'已核实的软件服务','confirmed':True},
            {'content':'未核实的飞行资质','confirmed':False}]}
        with patch.object(service,'company',return_value=profile):
            context=service.company_context()
        self.assertIn('已核实的软件服务',context)
        self.assertNotIn('未核实的飞行资质',context)
        self.assertIn('招标要求不是我方能力证明',context)

    def test_atomic_persistence_and_confirmed_lot_notes(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(service,'root',return_value=Path(tmp)):
            current={'requirements':[{'id':1}],'fingerprint':'abc','lot':'A'}
            with patch.object(service,'snapshot',return_value=current):
                service.confirm(6,'abc','待确认现场服务')
            paths=list(Path(tmp).glob('*.json'))
            self.assertEqual(len(paths),1)
            self.assertEqual(service.read(paths[0].name,{})['notes'],'待确认现场服务')
            with patch.object(service,'snapshot',return_value=current):
                with self.assertRaises(ValueError):service.confirm(6,'stale','')
            self.assertEqual(len(list(Path(tmp).glob('*.tmp'))),0)

    def test_empty_requirements_cannot_be_confirmed(self):
        with patch.object(service,'snapshot',return_value={'requirements':[],'fingerprint':'a'}):
            with self.assertRaises(ValueError):service.confirm(6,'a','')

    def test_stale_notes_never_enter_generation(self):
        project={'project_types':[],'service_content':'','technical_objects':[]}
        with patch.object(service,'company_context',return_value='公司资料'),patch.object(service,'snapshot',return_value={'confirmed':False,'notes':'旧范围','project':project}):
            context=service.generation_context(6)
            self.assertIn('公司资料',context)
            self.assertNotIn('旧范围',context)
        with patch.object(service,'company_context',return_value='公司资料'),patch.object(service,'snapshot',return_value={'confirmed':True,'notes':'新范围','project':project}):
            self.assertIn('新范围',service.generation_context(6))

    def test_snapshot_invalidated_by_requirement_or_outline_change(self):
        project={'id':6,'domain':'lowalt','project_types':[],'technical_objects':[]}
        reqs=[{'id':1,'title':'测试方案','suggested_chapter':'测试方案','status':'pending','content':'接口测试'}]
        nodes=[{'id':7,'title':'测试方案'}]
        def query(sql, params):
            return nodes if 'chapter_node' in sql else []
        def one(sql, params):
            return project if 'FROM project ' in sql else None
        with tempfile.TemporaryDirectory() as tmp,patch.object(service,'root',return_value=Path(tmp)),patch('app.core.database.query_one',side_effect=one),patch('app.core.database.query',side_effect=query),patch('app.services.requirement_service.list_requirements',side_effect=lambda _: [dict(r) for r in reqs]),patch('app.services.lot_service.selected_lot_code',return_value='A'),patch('app.services.section_service.generation_node_ids',return_value={7}):
            first=service.snapshot(6)
            self.assertEqual(first['requirements'][0]['chapter_candidates'][0]['id'],7)
            service.confirm(6,first['fingerprint'],'范围确认')
            self.assertTrue(service.snapshot(6)['confirmed'])
            reqs[0]['content']='新增性能要求'
            self.assertFalse(service.snapshot(6)['confirmed'])
            reqs[0]['content']='接口测试'
            nodes[0]['title']='部署方案'
            self.assertFalse(service.snapshot(6)['confirmed'])


if __name__=='__main__':unittest.main()
