"""Run with Blender --background --python tests/test_heels_blender.py."""
import sys
from pathlib import Path
import bpy
from mathutils import Vector
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addon'))
from hero_swap_tools import proportions

def rig(name, positions, parents):
    ob=bpy.data.objects.new(name,bpy.data.armatures.new(name))
    bpy.context.collection.objects.link(ob)
    bpy.context.view_layer.objects.active=ob;ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for n, head in positions.items():
        b=ob.data.edit_bones.new(n);b.head=head;b.tail=Vector(head)+Vector((0,.2,0))
        if n in parents:b.parent=ob.data.edit_bones[parents[n]]
    bpy.ops.object.mode_set(mode='OBJECT');ob.select_set(False)
    return ob

for heels in (False, True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    positions={'pelvis':(0,0,10),'thigh_l':(0,1,9),'foot_l':(0,1,2),'ball_l':(2,1,.5),'ball_l_end':(-1,1,.5),'thigh_r':(0,-1,9),'foot_r':(0,-1,2),'ball_r':(2,-1,.5),'ball_r_end':(3,-1,.5)}
    parents={'thigh_l':'pelvis','foot_l':'thigh_l','ball_l':'foot_l','ball_l_end':'ball_l','thigh_r':'pelvis','foot_r':'thigh_r','ball_r':'foot_r','ball_r_end':'ball_r'}
    src=rig('source',positions,parents)
    target={n.replace('foot','ankle'):p for n,p in positions.items()}
    target['ball_l']=(3,1,1);target['ball_l_end']=(4,1,1)
    target['ball_r']=(3,-1,1);target['ball_r_end']=(4,-1,1)
    hero=rig('hero',target,{n.replace('foot','ankle'):p.replace('foot','ankle') for n,p in parents.items()})
    mesh=bpy.data.meshes.new('shoe');mesh.from_pydata([(0,1,0),(2,1,0),(3,1,0)],[],[])
    ob=bpy.data.objects.new('shoe',mesh);bpy.context.collection.objects.link(ob)
    for i,n in enumerate(['foot_l','ball_l','ball_l_end']):ob.vertex_groups.new(name=n).add([i],1,'REPLACE')
    mod=ob.modifiers.new('rig','ARMATURE');mod.object=src
    bm={'pelvis':['pelvis','pelvis'],'side_bones':['thigh_l','thigh_r'],'ground_bones':['ball_l','ball_r'],'height_bones':['pelvis','pelvis','ball_l'],'groups':{n:n.replace('foot','ankle') for n in positions},'segments':[['foot_l','ball_l','ankle_l','ball_l'],['ball_l','ball_l_end','ball_l','ball_l_end'],['foot_r','ball_r','ankle_r','ball_r'],['ball_r','ball_r_end','ball_r','ball_r_end']]}
    report=proportions.fit(src,[ob],hero,bm,preserve_heels=heels)
    bpy.context.view_layer.update()
    if heels:
        for a,b,dist in [(0,1,2),(1,2,1),(0,2,3)]:
            assert abs((ob.data.vertices[a].co-ob.data.vertices[b].co).length-dist*report['scale'])<1e-4
        assert not hero.pose.bones['CV_ball_l'].constraints
        assert len(hero.pose.bones['CV_foot_l'].constraints)==1
        assert 'drive_CV_ball_l"' not in hero['proportion_constraints']
        before=[v.co.copy() for v in ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices]
        hero.pose.bones['ankle_l'].rotation_mode='XYZ';hero.pose.bones['ankle_l'].rotation_euler.x=.7
        bpy.context.view_layer.update()
        after=[v.co.copy() for v in ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices]
        assert (before[2]-after[2]).length>.01
        assert abs((after[0]-after[2]).length-3*report['scale'])<1e-4
    else:
        assert hero.pose.bones['CV_ball_l'].constraints
print('HEEL_TESTS_PASS: rigid sole, reversed toe helper, ankle animation, opt-out')
