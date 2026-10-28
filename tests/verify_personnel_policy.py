import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.contact_review import required_personnel_items,contact_review_excerpts
from app.services.contact_policy import CONTACT_POLICY

class PersonnelPolicy(unittest.TestCase):
    def detected(self,text):return list(required_personnel_items([{'id':1,'content':text}]))
    def test_roles_only_no_unnecessary_real_identity_request(self):
        self.assertFalse(self.detected('应提供售后团队配置、岗位职责、值守与替补机制。'))
        self.assertFalse(list(contact_review_excerpts('本项目拟配置售后负责人、测试工程师及二线技术支持。')))
    def test_explicit_personnel_evidence_required(self):
        for text in ['须提供项目经理姓名、简历与社保证明。','售后联系人应填写联系电话。','拟派技术负责人应提交证书。']:
            self.assertEqual(len(self.detected(text)),1)
    def test_procurement_contact_and_negative_requirement_not_staff_material(self):
        self.assertFalse(self.detected('采购联系人姓名及联系电话应填写完整。'))
        self.assertFalse(self.detected('项目团队无需提供姓名及简历。'))
    def test_actual_name_phone_still_reviewed(self):
        self.assertTrue(list(contact_review_excerpts('售后负责人：张三，联系电话：13800138000')))
    def test_prompt_distinguishes_actual_assignment_and_role(self):
        self.assertIn('岗位称谓不能替代',CONTACT_POLICY)
        self.assertIn('本次投入',CONTACT_POLICY)

if __name__=='__main__':unittest.main()
