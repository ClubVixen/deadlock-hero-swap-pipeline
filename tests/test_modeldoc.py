"""Regression cases for runtime driver constraints and combined replacement meshes."""
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.modules.setdefault('hero_swap_tools', types.ModuleType('hero_swap_tools'))
for name in ('kv3', 'build'):
    spec = importlib.util.spec_from_file_location('hero_swap_tools.' + name,
        ROOT / 'addon' / 'hero_swap_tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
kv3 = sys.modules['hero_swap_tools.kv3']
build = sys.modules['hero_swap_tools.build']


class ModelDocRegression(unittest.TestCase):
    def test_driver_constraints_preserve_originals_in_one_list(self):
        original = {'_class': kv3.q('AnimConstraintOrient'), 'name': kv3.q('original')}
        added = {'_class': kv3.q('AnimConstraintOrient'), 'name': kv3.q('driver')}
        document = {'rootNode': {'children': [
            {'_class': kv3.q('AnimConstraintList'), 'children': [original]}]}}
        build.merge_constraints(document, {'_class': kv3.q('AnimConstraintList'), 'children': [added]})
        lists = kv3.find(document, 'AnimConstraintList')
        self.assertEqual(len(lists), 1)
        self.assertEqual(lists[0]['children'], [original, added])

    def test_first_driver_list_is_added(self):
        document = {'rootNode': {'children': []}}
        build.merge_constraints(document, {'_class': kv3.q('AnimConstraintList'), 'children': []})
        self.assertEqual(len(kv3.find(document, 'AnimConstraintList')), 1)

    def test_body_and_face_replacement_has_one_owner_and_keeps_wand(self):
        def node(cls, **kw):
            return {'_class': kv3.q(cls), **kw}
        root = {'rootNode': {'children': [
            node('RenderMeshList', children=[node('RenderMeshFile', name=kv3.q(n), filename=kv3.q(n+'.dmx'))
                for n in ('body', 'face', 'wand')]),
            node('BodyGroupList', children=[node('BodyGroup', name=kv3.q(n), children=[
                node('BodyGroupChoice', meshes=[kv3.q(n)])]) for n in ('body','face','wand')])]}}
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td)/'src.vmdl', Path(td)/'out.vmdl'
            src.write_text(kv3.dumps('', root))
            build.rewrite_vmdl(str(src), str(dst), 'swap', 'swap.dmx', ['body','face'])
            _, result = kv3.loads(dst.read_text())
        owners = [g for g in kv3.find(result,'BodyGroup') if any(
            kv3.unq(n)=='swap' for c in kv3.find(g,'BodyGroupChoice') for n in c['meshes'])]
        self.assertEqual(len(owners),1)
        self.assertEqual([kv3.unq(x['name']) for x in kv3.find(result,'RenderMeshFile')],['swap','wand'])
        self.assertNotIn('wand',build.guess_body_meshes(['magician_body','wand']))


if __name__ == '__main__':
    unittest.main()
