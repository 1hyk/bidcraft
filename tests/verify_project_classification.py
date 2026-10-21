import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.project_classification import project_types,present,validate_types
from app.api.projects import update_project,ProjectUpdate,create_project,ProjectCreate
from app.core.domains import get_domain


class Classification(unittest.TestCase):
    def test_no_industry_or_service_does_not_default_to_lowalt(self):
        with patch('app.api.projects.execute',return_value=99) as write:
            result=create_project(ProjectCreate(name='行业未确认'))
            self.assertEqual(result['domain'],'softtest')
            self.assertNotIn('飞行测试验证',str(write.call_args.args[1]))


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

    def test_service_content_is_independent_and_optional(self):
        row={'id':4,'name':'原名','domain':'lowalt','project_types':'["自定义业务"]','service_content':'原业务'}
        with patch('app.api.projects.query_one',return_value=row),patch('app.api.projects.execute') as write:
            update_project(4,ProjectUpdate(service_content='实飞验证外协'))
            sql,params=write.call_args.args
            self.assertIn('service_content =',sql)
            self.assertNotIn('project_types =',sql)
            self.assertIn('实飞验证外协',params)
        self.assertEqual(present(row)['project_types'],['自定义业务'])

    def test_multiselect_roundtrip(self):
        types=['飞行测试验证','软件测试','飞行测试验证']
        result=validate_types(types,'lowalt')
        self.assertEqual(result,['飞行测试验证','软件测试'])
        self.assertEqual(project_types({'project_types':json.dumps(result)}),result)




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
