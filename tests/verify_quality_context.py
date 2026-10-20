"""成稿检查必须区分采购沟通、任命及拟配置，不能靠相邻关键词判定。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services.proposal_quality import person_role_excerpts, claim_excerpts, validate_review


class QualityContext(unittest.TestCase):
    def test_procurement_communication_is_not_appointment(self):
        samples = [
            '项目经理需在1小时内向采购人指定联系人（薛老师、赵老师等拟任角色对应的实际对接人）提交书面情况说明。',
            '投诉与异议受理联系赵老师（13900000000）。我方内部指定专职客户经理统筹对接。',
            '我方项目经理向采购方联系人张三提交说明；我方团队负责整改。',
        ]
        for text in samples:
            for name in ['薛老师', '赵老师', '张三']:
                self.assertEqual(list(person_role_excerpts(text, name)), [], text)

    def test_actual_appointment_still_reported(self):
        for text in [
            '我方项目经理：张三。', '项目经理张三负责交付。',
            '拟派张三担任本项目技术负责人。',
            '采购方联系人张三担任我方项目经理。',
            '向采购方联系人张三提交说明，并指定张三担任我方项目经理。',
        ]:
            self.assertTrue(list(person_role_excerpts(text, '张三')), text)

    def test_planned_configuration_is_not_existing_capability(self):
        for text in ['我方拟配置具备无人机系统调试、软件测试及航空安全管理经验的人员。',
                     '我方拟配置具备相应能力的技术工程师团队，人员资质[待补充]。']:
            self.assertEqual(list(claim_excerpts(text)), [])

    def test_existing_capability_still_reported(self):
        for text in ['我方拥有资质证书。', '本公司技术团队具备丰富项目经验。',
                     '我方已取得相关资质。', '经核查，本项目不存在零负偏离以外的情况。',
                     '我方拟派工程师，本公司已拥有相关资质。']:
            self.assertTrue(list(claim_excerpts(text)), text)

    def test_employment_assertion_requires_verification(self):
        from app.services.evidence_roles import flag_capability_claims
        self.assertTrue(list(claim_excerpts('团队成员均为本单位正式在职员工，劳动关系真实有效。')))
        text='所有拟派人员均为本单位正式员工，劳动关系清晰，社保缴纳记录完整可查。'
        self.assertTrue(list(claim_excerpts(text)))
        flagged=flag_capability_claims(text)
        self.assertIn('待确认：拟派人员劳动关系',flagged)
        self.assertEqual(flag_capability_claims(flagged),flagged)
        self.assertFalse(list(claim_excerpts('我方将按要求选派人员，劳动关系材料待确认。')))
        self.assertFalse(list(claim_excerpts('所有成员须为本单位员工，并提交证明。')))

    def test_pending_material_does_not_override_coverage_but_required_gap_does(self):
        for marker in ['[待确认：实际人员]', '[待补充：设备清单]']:
            result = validate_review([{'requirement_id': 1, 'status': 'covered', 'evidence_ids': ['a']}],
                                     [{'id': 1, 'title': '流程', 'content': '提交报告并复核'}], {'a': {'text': '我方提交报告并复核。另有'+marker}})
            self.assertEqual(result[0]['status'], 'covered')
        partial = validate_review([{'requirement_id': 1, 'status': 'partial', 'evidence_ids': ['a'], 'missing_points': ['提供人员姓名']}],
                                  [{'id': 1, 'title': '人员', 'content': '提供人员姓名'}], {'a': {'text': '[待补充：人员姓名]'}})
        self.assertEqual(partial[0]['status'], 'partial')
        result = validate_review([{'requirement_id': 1, 'status': 'covered', 'evidence_ids': ['a']}],
                                 [{'id': 1, 'title': '流程'}], {'a': {'text': '已描述完整流程'}})
        self.assertEqual(result[0]['status'], 'covered')


if __name__ == '__main__':
    unittest.main()
