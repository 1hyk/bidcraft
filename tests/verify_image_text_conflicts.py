"""无文字提示不能被场景中的表面标识要求抵消；不调用生图。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.image_prompt import build_prompt

class Tests(unittest.TestCase):
    def test_preserves_objects_removes_surface_conflicts(self):
        prompt=build_prompt({'name':'现场保障布置示意','prompt':'两辆带有标识的保障车辆，三名工作人员，印有编号的设备，写着字样的警戒带。'},'qwen-image-2.0')
        for phrase in ['两辆','保障车辆','三名工作人员','设备','警戒带','Plain unmarked']:
            self.assertIn(phrase,prompt)
        for phrase in ['带有标识','印有编号','写着字样','的的']:
            self.assertNotIn(phrase,prompt)

    def test_no_scene_specific_object_is_invented(self):
        prompt=build_prompt({'name':'室内场景','prompt':'机柜与工作台，线缆连接。'},'qwen-image-2.0')
        self.assertIn('机柜与工作台',prompt)
        self.assertNotIn('机场',prompt)
        self.assertNotIn('无人机',prompt)

if __name__=='__main__':unittest.main()
