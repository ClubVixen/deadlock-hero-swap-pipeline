import bpy,sys,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'addon'))
from hero_swap_tools import core
# Clearance must use the active shape, keep the body unchanged, and retain keys.
def mesh(name,z):
 d=bpy.data.meshes.new(name);d.from_pydata([(-1,-1,z),(1,-1,z),(1,1,z),(-1,1,z)],[],[(0,1,2,3)])
 o=bpy.data.objects.new(name,d);bpy.context.scene.collection.objects.link(o);return o
body=mesh('test_skin',0);body.shape_key_add(name='Basis');key=body.shape_key_add(name='Raised')
for v in key.data:v.co.z=.2
key.value=1
cloth=mesh('test_cloth',.1);cloth.shape_key_add(name='Basis');cloth.shape_key_add(name='Fit')
bpy.context.view_layer.update();core.conform_current_shape([cloth],body,.5,.01)
assert all(abs(v.co.z-.21)<1e-5 for v in cloth.data.shape_keys.key_blocks[0].data)
assert key.value==1 and all(v.co.z==0 for v in body.data.vertices)
assert len(cloth.data.shape_keys.key_blocks)==2
with tempfile.TemporaryDirectory() as td:
 mat=bpy.data.materials.new('sheer_test');mat.use_nodes=True;bs=mat.node_tree.nodes.get('Principled BSDF');bs.inputs['Alpha'].default_value=.45
 cloth.data.materials.append(mat)
 exported=core.export_materials([cloth],td,'test')
 entry=exported[0];vmat=(Path(td)/entry['to']).read_text()
 assert 'F_TRANSLUCENT' in vmat and 'TextureTranslucency1' in vmat
 img=bpy.data.images.load(str(Path(td)/entry['textures']['opacity']));img.colorspace_settings.name='Non-Color'
 assert abs(img.pixels[0]-.45)<.005, img.pixels[0]
print('CURRENT_SHAPE_AND_OPACITY_PASS')
