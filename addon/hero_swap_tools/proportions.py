"""Experimental proportion-preserving deformation rig driven by ModelDoc constraints.

The game's animation skeleton stays unchanged. A separately named child skeleton
keeps the source lengths and follows mapped hero rotations, not translations.
Runtime constraint evaluation must be verified for each hero/game build.
"""
import math
import re
import bpy
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree
from . import core, kv3


def fit(src, meshes, hero, bm, prefix='CV_', preserve_heels=False):
    bones=src.data.bones
    h={b.name:src.matrix_world@b.head_local for b in bones}
    hp={b.name:hero.matrix_world@b.head_local for b in hero.data.bones}
    sp,dp=bm['pelvis']
    # Unweighted vertices otherwise remain at the world origin in the game.
    # Repair isolated holes from their nearest weighted neighbour; wholly rigid
    # unweighted parts ride the pelvis. Record each repair for review.
    repaired = {}
    for ob in meshes:
        valid={g.index:g.name for g in ob.vertex_groups if g.name in bones}
        weighted={v.index:[(valid[g.group],g.weight) for g in v.groups
                           if g.group in valid and g.weight>1e-6] for v in ob.data.vertices}
        good=[i for i,w in weighted.items() if w]
        bad=[i for i,w in weighted.items() if not w]
        if not bad:continue
        tree=KDTree(len(good))
        for i in good:tree.insert(ob.data.vertices[i].co,i)
        tree.balance()
        for i in bad:
            weights=weighted[tree.find(ob.data.vertices[i].co)[1]] if good else [(sp,1.0)]
            for name,weight in weights:
                group=ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
                group.add([i],weight,'REPLACE')
        repaired[ob.name]=len(bad)
    left=h[bm['side_bones'][0]]-h[bm['side_bones'][1]]
    fwd=left.cross(Vector((0,0,1)))
    yaw=Vector((fwd.x,fwd.y)).angle_signed(Vector((1,0)))
    ground=min(h[n].z for n in bm['ground_bones'] if n in h)
    sa,da,df=bm['height_bones']
    scale=(hp[da].z-hp[df].z)/(h[sa].z-ground)
    turn=Matrix.Rotation(-yaw,4,'Z')
    T=Matrix.Scale(scale,4)@turn
    h={n:T@v for n,v in h.items()}
    placement=Matrix.Translation(hp[dp]-h[sp])
    groups={a:b for a,b in bm['groups'].items() if a in bones and b in hero.data.bones}
    # Foot roots come from the explicit map, never toe-tip directions. Advanced
    # maps can supply heel_feet when target bones use nonstandard names.
    heel_roots = set()
    heel_owner = {}
    if preserve_heels:
        heel_roots = set(bm.get('heel_feet', [])) or {
            n for n, target in groups.items()
            if re.search(r'(^|_)(ankle|foot)(_|$)', target, re.I)}
        if not heel_roots or any(n not in groups for n in heel_roots):
            raise ValueError('Heeled shoes need mapped foot roots; set heel_feet in the bone map')
        for b in bones:
            ancestor = b
            while ancestor:
                if ancestor.name in heel_roots:
                    heel_owner[b.name] = ancestor.name
                    break
                ancestor = ancestor.parent
        # Toe/helper bones inherit the ankle's motion instead of independent
        # toe rotations. Their original relative transforms remain intact.
        groups = {n: target for n, target in groups.items()
                  if n not in heel_owner or n in heel_roots}
    parents={b.name:b.parent.name if b.parent else None for b in bones}
    rotations={}
    for seg in bm['segments']:
        a,b,c,d=seg[:4]
        if any(x not in h for x in (a,b)):continue
        parents[b]=a
        if len(seg)>4 and seg[4]=='move':continue
        if c in hp and d in hp and (h[b]-h[a]).length>1e-6:
            rotations[a]=(h[b]-h[a]).rotation_difference(hp[d]-hp[c]).to_matrix().to_4x4()
    # Several Blender control rigs split the hips and spine into separate control
    # trees. Establish one actual deformation hierarchy, independent of IK controls.
    first_spine=next((n for n in bm.get('torso',[]) if 'spine' in n.lower()),None)
    if first_spine:parents[first_spine]=sp
    parents[sp]=None
    wanted=set(groups)
    for ob in meshes:
        used={g.group for v in ob.data.vertices for g in v.groups if g.weight>0}
        wanted.update(g.name for g in ob.vertex_groups if g.index in used and g.name in bones)
    # Retain only deformation-relevant nodes. Missing/control ancestors resolve
    # to the closest retained ancestor, then pelvis as a stable fallback.
    resolved={}
    for n in wanted:
        p=parents.get(n);seen={n}
        while p and p not in wanted and p not in seen:
            seen.add(p);p=parents.get(p)
        resolved[n]=p if p in wanted and p not in seen else (sp if n!=sp else None)
    maps={sp:placement}
    visiting=set()
    def transform(n):
        if n in maps:return maps[n]
        if n in visiting:raise ValueError('Cyclic deformation hierarchy: '+n)
        visiting.add(n)
        P=transform(resolved[n]) if resolved[n] else placement
        R=rotations.get(n)
        if n in heel_roots:
            maps[n] = Matrix.Translation(P @ h[n] - h[n])
            visiting.remove(n)
            return maps[n]
        if n in heel_owner:
            maps[n] = transform(heel_owner[n]).copy()
            visiting.remove(n)
            return maps[n]
        maps[n]=Matrix.Translation(P@h[n])@R@Matrix.Translation(-h[n]) if R else P.copy()
        visiting.remove(n)
        return maps[n]
    for n in wanted:transform(n)
    names={n:prefix+re.sub('[^A-Za-z0-9_]','_',n) for n in wanted}
    if len(set(names.values()))!=len(names):raise ValueError('Bone name sanitization collision')
    if any(n in hero.data.bones for n in names.values()):raise ValueError('Refit requires a clean target skeleton')
    for ob in meshes:
        core.deform_by_bones(ob,lambda n:None,world=True)
        for data in [ob.data.vertices]+([k.data for k in ob.data.shape_keys.key_blocks] if ob.data.shape_keys else []):
            for v in data:v.co=T@v.co
        core.deform_dq(ob,lambda n:maps.get(n))
        core.retarget_weights(ob,lambda n:names.get(n))
        core.reparent(ob,hero)
    bpy.context.view_layer.objects.active=hero
    hero.hide_set(False);hero.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    inv=hero.matrix_world.inverted()
    pending=set(wanted);ordered=[]
    while pending:
        ready=sorted(n for n in pending if not resolved[n] or resolved[n] in ordered)
        if not ready:raise ValueError('Cyclic retained hierarchy')
        for n in ready:
            eb=hero.data.edit_bones.new(names[n])
            if n in groups:
                M=hero.matrix_world@hero.data.edit_bones[groups[n]].matrix
            else:M=maps[n]@T@src.matrix_world@bones[n].matrix_local
            M=core.rigid(M);M.translation=maps[n]@h[n]
            # A newly created edit bone has zero length. Give it a direction
            # before assigning its matrix, otherwise Blender loses its Y axis.
            eb.head=(0.,0.,0.);eb.tail=(0.,max(bones[n].length*scale,.1),0.)
            eb.matrix=inv@M
            eb.parent=hero.data.edit_bones[names[resolved[n]]] if resolved[n] else hero.data.edit_bones[dp]
            eb.use_deform=True
            ordered.append(n);pending.remove(n)
    bpy.ops.object.mode_set(mode='OBJECT')
    constraints=[]
    for n in ordered:
        if n not in groups or n==sp:continue
        pb=hero.pose.bones[names[n]]
        c=pb.constraints.new('COPY_ROTATION');c.name='Preview hero rotation';c.target=hero;c.subtarget=groups[n]
        c.target_space='WORLD';c.owner_space='WORLD';c.mix_mode='REPLACE'
        constraints.append({'_class':kv3.q('AnimConstraintOrient'),'name':kv3.q('drive_'+names[n]),'children':[
            {'_class':kv3.q('AnimConstraintSlave'),'parent_bone':kv3.q(names[n]),'weight':1.0},
            {'_class':kv3.q('AnimConstraintBoneInput'),'parent_bone':kv3.q(groups[n]),'weight':1.0,'relative_origin':[kv3.Raw('0')]*3,'relative_angles':[kv3.Raw('0')]*3}]})
    hero['proportion_constraints']=kv3.dumps('',{'_class':kv3.q('AnimConstraintList'),'children':constraints})
    hero['proportion_source_map']={names[n]:groups[n] for n in ordered if n in groups}
    return {'scale':scale,'added_bones':len(wanted),'orientation_constraints':len(constraints),'names':names,'repaired_unweighted_vertices':repaired,'heel_roots':sorted(heel_roots),'rigid_shoe_bones':sorted(heel_owner),'status':'experimental; requires runtime validation'}
