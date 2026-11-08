"""Run with Blender --background --factory-startup --python tests/blender_regions.py."""
import bpy
import importlib.util
from pathlib import Path
from mathutils import Vector

path = Path(__file__).resolve().parents[1] / 'addon/hero_swap_tools/core.py'
spec = importlib.util.spec_from_file_location('fitting_core', path)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

def mesh(name, points, translation):
    data = bpy.data.meshes.new(name)
    data.from_pydata(points, [], [])
    ob = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = translation
    for i in range(2):
        ob.vertex_groups.new(name=f'Finger{i}').add([i], 1.0, 'REPLACE')
    return ob

skin = mesh('reference', [(0,0,0),(1,0,0)], (3,4,5))
cloth = mesh('glove', [(0,2,0),(1,2,0)], (3,4,5))
cloth.shape_key_add(name='Basis')
key = cloth.shape_key_add(name='Adjustment')
key.data[0].co.z += .25
bpy.context.view_layer.update()
result = core.align_weighted_regions([cloth], skin, ['Finger*'])
assert result['glove'] == {'regions':2, 'vertices':2}
for i in range(2):
    assert (cloth.matrix_world @ cloth.data.vertices[i].co - skin.matrix_world @ skin.data.vertices[i].co).length < 1e-6
    assert (cloth.data.shape_keys.key_blocks['Basis'].data[i].co - skin.data.vertices[i].co).length < 1e-6
assert abs(key.data[0].co.z - .25) < 1e-6
before = [v.co.copy() for v in cloth.data.vertices]
core.align_weighted_regions([cloth], skin, ['NoMatchingRegion'])
assert all((v.co-p).length < 1e-6 for v,p in zip(cloth.data.vertices,before))
print('PASS: world-space finger alignment, shape-key deltas and unmatched regions')

def panel(name, z):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(-1,-1,z),(1,-1,z),(1,1,z),(-1,1,z)], [], [(0,1,2,3)])
    ob = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(ob)
    ob.vertex_groups.new(name='Chest').add([0,1,2,3], 1.0, 'REPLACE')
    return ob
surface, garment = panel('skin_plane',0), panel('cloth_plane',.05)
bpy.context.view_layer.update()
report = core.seat_clothing_edges([garment], surface, ['Chest'], gap=.003)
assert report['cloth_plane']['edge_vertices'] == 4
assert report['cloth_plane']['max_gap_after'] < .003001
assert all(abs(v.co.z-.003) < 1e-6 for v in garment.data.vertices)
print('PASS: open-edge contact clearance')
