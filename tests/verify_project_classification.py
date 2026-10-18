import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.project_classification import project_types,present,suggest_from_text,validate_types
from app.api.projects import update_project,ProjectUpdate,create_project,ProjectCreate
from app.core.domains import get_domain


class Classification(unittest.TestCase):
    def test_application_domains_are_complete_and_independent(self):
        from app.core.domains import DOMAINS, list_domains
        expected={'government','finance','healthcare','education','manufacturing',
                  'transport','energy','telecom','retail','enterprise'}
        self.assertTrue(expected.issubset(DOMAINS))
        self.assertEqual(len({d['key'] for d in list_domains()}),len(DOMAINS))
        for key in expected:
            with self.subTest(domain=key):
                domain=get_domain(key)
                self.assertEqual(domain.key,key)
                self.assertIn('软件测试',domain.project_types)
                self.assertIn('功能测试',domain.tags)
                self.assertEqual(domain.chapters,())
                self.assertEqual(domain.lot_fields,())
                self.assertEqual(domain.genre.full,'技术方案')
                self.assertEqual(validate_types(['软件测试'],key),['软件测试'])

    def test_domain_is_industry_not_test_method(self):
        low=get_domain('lowalt');general=get_domain('softtest')
        self.assertEqual(general.key,'softtest')
        self.assertIn('通用',general.name)
        self.assertIn('软件测试',low.project_types)
        self.assertIn('软件测试',general.project_types)
        self.assertNotIn('无人机',low.project_types)
        self.assertNotIn('低空',low.project_types)

    def test_legacy_and_empty_selection(self):
        self.assertEqual(project_types({'project_type':'无人机'}),[])
        self.assertEqual(project_types({'project_type':'软件测试'}),['软件测试'])
        self.assertEqual(project_types({'project_type':'软件测试','project_types':'[]'}),[])
        value=present({'project_type':'铁路','project_types':None,'domain':'lowalt'})
        self.assertEqual(value['legacy_project_type'],'铁路')
        self.assertEqual(value['domain'],'lowalt')

    def test_multiselect_roundtrip(self):
        types=['飞行测试验证','软件测试','飞行测试验证']
        result=validate_types(types,'lowalt')
        self.assertEqual(result,['飞行测试验证','软件测试'])
        self.assertEqual(project_types({'project_types':json.dumps(result)}),result)

    def test_real_tender_shape_not_software_only(self):
        text='实飞测试、演示平台验证、机巢部署与平台调试，售后服务与质保。'
        out=suggest_from_text(text,'lowalt')
        self.assertEqual(out['project_types'],['飞行测试验证','软件测试','系统集成与部署','运维与技术保障'])
        self.assertNotIn('平台软件开发',out['project_types'])
        self.assertTrue(all(x['excerpt'] in text for x in out['evidence']))
        self.assertEqual(suggest_from_text('性能测试和接口测试','softtest')['project_types'],['软件测试'])

    def test_non_tender_reference_and_other_lots_excluded(self):
        from app.services.project_classification import suggestions
        with patch('app.core.database.query_one',side_effect=[{'domain':'lowalt'}, {'id':2}]),patch('app.services.lot_service.selected_lot_code',return_value='A'),patch('app.services.requirement_service.list_requirements',return_value=[]),patch('app.core.database.query',return_value=[{'content':'飞行测试','applicable_lots':'["B"]'},{'content':'软件测试','applicable_lots':'["A"]'}]) as read:
            out=suggestions(6)
            self.assertEqual(out['project_types'],['软件测试'])
            self.assertIn("f.category IN ('tender','clarification')",read.call_args.args[0])
            self.assertEqual(read.call_args.args[1],(6,2))

    def test_patch_name_preserves_domain_and_multiselect(self):
        row={'id':4,'name':'原名','domain':'lowalt','project_types':'["软件测试","飞行测试验证"]'}
        with patch('app.api.projects.query_one',return_value=row),patch('app.api.projects.execute') as write:
            update_project(4,ProjectUpdate(name='新名'))
            sql=write.call_args.args[0]
            self.assertNotIn('project_types =',sql)
            self.assertNotIn('domain =',sql)

    def test_patch_types_does_not_require_name_or_change_legacy(self):
        row={'id':4,'name':'原名','domain':'lowalt','project_type':'无人机'}
        with patch('app.api.projects.query_one',return_value=row),patch('app.api.projects.execute') as write:
            update_project(4,ProjectUpdate(project_types=['软件测试','运维与技术保障']))
            sql,params=write.call_args.args
            self.assertEqual(json.loads(params[0]),['软件测试','运维与技术保障'])
            self.assertNotIn('project_type =',sql)
            self.assertNotIn('name =',sql)

    def test_create_writes_multiselect_json(self):
        with patch('app.api.projects.execute',return_value=99) as write:
            result=create_project(ProjectCreate(name='测试',domain='lowalt',project_types=['飞行测试验证','软件测试'],technical_objects=['机巢']))
            self.assertEqual(result['domain'],'lowalt')
            params=write.call_args.args[1]
            self.assertEqual(json.loads(params[4]),['飞行测试验证','软件测试'])
            self.assertEqual(json.loads(params[5]),['机巢'])


if __name__=='__main__':unittest.main()
