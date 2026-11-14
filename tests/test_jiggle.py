import importlib.util,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('jiggle',Path(__file__).resolve().parents[1]/'addon/hero_swap_tools/jiggle.py');j=importlib.util.module_from_spec(spec);spec.loader.exec_module(j)
class JiggleTests(unittest.TestCase):
 def test_refresh_adds_cape_and_excludes_anchor(self):
  names=['CV_Hair_root','CV_Hair_tip','CV_Skirt_02','head']
  self.assertEqual(j.select(names,{'keep_patterns':['Hair*']},'*Skirt*','*root'),['CV_Hair_tip','CV_Skirt_02'])
 def test_skip_and_prefix_and_first_rule(self):
  cfg={'presets':[{'pattern':'Hair_root','skip':True},{'pattern':'Hair*','fields':{'yaw_stiffness':100,'yaw_damping':4,'angle_limit':10,'has_base_spring':0}}]}
  r=j.rules(['CV_Hair_root','CV_Hair_tip'],cfg,2,1.5,.5)
  self.assertEqual(len(r),1);self.assertEqual(r[0]['fields']['yaw_stiffness'],200);self.assertEqual(r[0]['fields']['yaw_damping'],6);self.assertEqual(r[0]['fields']['angle_limit'],5)
  self.assertEqual(cfg['presets'][1]['fields']['angle_limit'],10)
