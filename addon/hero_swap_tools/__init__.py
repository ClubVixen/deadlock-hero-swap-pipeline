"""Hero Swap Tools: sidebar buttons for fitting an SFM character onto a Deadlock hero skeleton."""
import os

import bpy
from bpy.props import EnumProperty, PointerProperty, StringProperty

from . import core

bl_info = {
    "name": "Hero Swap Tools",
    "author": "ClubVixen",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Hero Swap",
    "description": "Bone-merge outfits, swap skins, fit a character onto a Deadlock hero skeleton and export ModelDoc DMX",
    "category": "Rigging",
}

DEFAULT_BONEMAP = os.path.join(os.path.dirname(__file__), "bonemap_va_anthro_to_deadlock.json")
DEFAULT_JIGGLE = os.path.join(os.path.dirname(__file__), "jiggle_presets.json")


def is_armature(_self, ob):
    return ob.type == "ARMATURE"


class HeroSwapSettings(bpy.types.PropertyGroup):
    character_rig: PointerProperty(name="Character Rig", type=bpy.types.Object, poll=is_armature,
                                   description="The SFM character's armature")
    hero_rig: PointerProperty(name="Hero Skeleton", type=bpy.types.Object, poll=is_armature,
                              description="The Deadlock hero skeleton (imported from the hero's body DMX)")
    bonemap: StringProperty(name="Bone Map", subtype="FILE_PATH", default=DEFAULT_BONEMAP)
    skin_swaps: StringProperty(name="Skin Swaps", default="nmm_outfit_sleeves=nmm_outfit_sleeves_textured",
                               description="Comma-separated old=new material names")
    body_mesh: PointerProperty(name="Body Mesh", type=bpy.types.Object, poll=lambda _s, o: o.type == "MESH",
                               description="The mesh whose shape keys (bust, hips, ...) the outfit should follow")
    create_missing: bpy.props.BoolProperty(name="Create Missing Keys", default=True,
                                           description="Copy body shape keys the outfit lacks, from the nearest body surface")
    max_distance: bpy.props.FloatProperty(name="Max Distance", default=0.0, min=0.0,
                                          description="Ignore outfit vertices farther than this from the body (0 = no limit)")
    keep_jiggle: bpy.props.BoolProperty(name="Keep Jiggle Bones", default=True,
                                        description="Keep hair/tail/chest/butt chains as bones on the hero skeleton so they can get physics")
    jiggle_presets: StringProperty(name="Jiggle Presets", subtype="FILE_PATH", default=DEFAULT_JIGGLE,
                                   description="JSON with the bone patterns to keep and their ModelDoc jiggle settings")
    eye_texture_dir: StringProperty(name="Eye Textures", subtype="DIR_PATH",
                                    description="Where the baked eye PNGs go, e.g. <addon>/materials/models/heroes_wip/<hero>")
    export_collection: PointerProperty(name="Export Collection", type=bpy.types.Collection,
                                       description="Named after the hero mesh it replaces, e.g. unicorn_body")
    export_dir: StringProperty(name="Export Folder", subtype="DIR_PATH")


def selected_meshes(context):
    return [o for o in context.selected_objects if o.type == "MESH"]


class HEROSWAP_OT_bonemerge(bpy.types.Operator):
    """Move the selected skinned meshes (e.g. an outfit) onto the character rig, matching bones by name"""
    bl_idname = "heroswap.bonemerge"
    bl_label = "Bone-merge Selected onto Character"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.hero_swap
        if not s.character_rig:
            self.report({"ERROR"}, "Pick the Character Rig first")
            return {"CANCELLED"}
        done = [o.name for o in selected_meshes(context) if core.bonemerge(o, s.character_rig)]
        self.report({"INFO"}, f"Bone-merged {len(done)} mesh(es)")
        return {"FINISHED"}


class HEROSWAP_OT_apply_skin(bpy.types.Operator):
    """Swap materials on the selected meshes using the Skin Swaps list"""
    bl_idname = "heroswap.apply_skin"
    bl_label = "Apply Skin Swaps"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pairs = [p.split("=", 1) for p in context.scene.hero_swap.skin_swaps.split(",") if "=" in p]
        n = core.apply_skin(selected_meshes(context), {a.strip(): b.strip() for a, b in pairs})
        self.report({"INFO"}, f"Swapped {n} material slot(s)")
        return {"FINISHED"}


class HEROSWAP_OT_sync_shape_keys(bpy.types.Operator):
    """Drive the selected meshes' shape keys from the Body Mesh's keys of the same name, creating missing ones"""
    bl_idname = "heroswap.sync_shape_keys"
    bl_label = "Match Outfit to Body Shape Keys"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.hero_swap
        if not s.body_mesh or not s.body_mesh.data.shape_keys:
            self.report({"ERROR"}, "Pick a Body Mesh that has shape keys")
            return {"CANCELLED"}
        res = core.sync_shape_keys(s.body_mesh, selected_meshes(context), s.create_missing, s.max_distance)
        driven = sum(d for d, _ in res.values())
        created = sum(c for _, c in res.values())
        self.report({"INFO"}, f"{len(res)} mesh(es): {created} keys created, {driven} keys linked to the body")
        return {"FINISHED"}


class HEROSWAP_OT_bake_eyes(bpy.types.Operator):
    """Turn the selected meshes' projected Source eyes into UV-mapped textures that survive the fit and export. Run it before the fit"""
    bl_idname = "heroswap.bake_eyes"
    bl_label = "Convert Source Eyes to Textures"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        out = bpy.path.abspath(context.scene.hero_swap.eye_texture_dir)
        if not out:
            self.report({"ERROR"}, "Pick the Eye Textures folder first")
            return {"CANCELLED"}
        made = [m.name for o in selected_meshes(context) for m in core.bake_source_eyes(o, out)]
        if not made:
            self.report({"ERROR"}, "No SourceIO eyes (eye_N_* properties) on the selected meshes")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Made {', '.join(made)}")
        return {"FINISHED"}


class HEROSWAP_OT_fit(bpy.types.Operator):
    """Fit every mesh skinned to the Character Rig onto the Hero Skeleton (keeps shape keys)"""
    bl_idname = "heroswap.fit"
    bl_label = "Fit Character to Hero"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.hero_swap
        if not (s.character_rig and s.hero_rig):
            self.report({"ERROR"}, "Pick both the Character Rig and the Hero Skeleton")
            return {"CANCELLED"}
        meshes = [o for o in bpy.data.objects if o.type == "MESH" and core.skinned_armature(o) == s.character_rig]
        if not meshes:
            self.report({"ERROR"}, "No meshes are skinned to the Character Rig")
            return {"CANCELLED"}
        keep = core.load_bonemap(s.jiggle_presets).get("keep_patterns", []) if s.keep_jiggle else []
        before = {b.name for b in s.hero_rig.data.bones}
        try:
            yaw, scale = core.fit_to_hero(s.character_rig, meshes, s.hero_rig, core.load_bonemap(s.bonemap),
                                          keep_patterns=keep)
        except KeyError as e:
            self.report({"ERROR"}, f"Bone not found: {e}. Check the bone map matches both rigs")
            return {"CANCELLED"}
        added = [b.name for b in s.hero_rig.data.bones if b.name not in before]
        s.hero_rig["hero_swap_jiggle_bones"] = added
        s.character_rig.hide_set(True)
        self.report({"INFO"}, f"Fitted {len(meshes)} meshes (turned {yaw * 57.2958:.0f} deg, scaled x{scale:.2f}), kept {len(added)} jiggle bones")
        return {"FINISHED"}


class HEROSWAP_OT_write_jiggle(bpy.types.Operator):
    """Write ModelDoc JiggleBone settings for the kept bones into a text block and the clipboard"""
    bl_idname = "heroswap.write_jiggle"
    bl_label = "Write Jiggle Bones for ModelDoc"

    def execute(self, context):
        s = context.scene.hero_swap
        names = list(s.hero_rig.get("hero_swap_jiggle_bones", [])) if s.hero_rig else []
        if not names:
            self.report({"ERROR"}, "No kept jiggle bones yet: run Fit Character to Hero with Keep Jiggle Bones on")
            return {"CANCELLED"}
        text, n = core.jiggle_vmdl_block(names, core.load_bonemap(s.jiggle_presets)["presets"])
        block = bpy.data.texts.get("jiggle_bones.vmdl") or bpy.data.texts.new("jiggle_bones.vmdl")
        block.from_string(text)
        context.window_manager.clipboard = text
        self.report({"INFO"}, f"{n} jiggle bones written to the 'jiggle_bones.vmdl' text and the clipboard")
        return {"FINISHED"}


class HEROSWAP_OT_export(bpy.types.Operator):
    """Export the collection as a ModelDoc DMX (Binary 9, Model 22) with Blender Source Tools"""
    bl_idname = "heroswap.export"
    bl_label = "Export ModelDoc DMX"

    def execute(self, context):
        s = context.scene.hero_swap
        if not (s.export_collection and s.export_dir):
            self.report({"ERROR"}, "Pick the Export Collection and Export Folder")
            return {"CANCELLED"}
        if not hasattr(context.scene, "vs"):
            self.report({"ERROR"}, "Enable Blender Source Tools first")
            return {"CANCELLED"}
        if s.hero_rig:
            bpy.context.view_layer.objects.active = s.hero_rig
            bpy.ops.object.mode_set(mode="EDIT")
            for eb in [b for b in s.hero_rig.data.edit_bones if b.name.startswith("_cloth")]:
                s.hero_rig.data.edit_bones.remove(eb)
            bpy.ops.object.mode_set(mode="OBJECT")
        for c in bpy.data.collections:
            c.vs.mute = c != s.export_collection
        vs = context.scene.vs
        vs.export_format, vs.dmx_encoding, vs.dmx_format = "DMX", "9", "22_modeldoc"
        vs.export_path = bpy.path.abspath(s.export_dir)
        bpy.ops.export_scene.smd(collection=s.export_collection.name)
        return {"FINISHED"}


class HEROSWAP_PT_panel(bpy.types.Panel):
    bl_label = "Hero Swap"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hero Swap"

    def draw(self, context):
        s = context.scene.hero_swap
        col = self.layout.column()
        col.label(text="1. Outfit")
        col.prop(s, "character_rig")
        col.operator(HEROSWAP_OT_bonemerge.bl_idname, icon="MOD_ARMATURE")
        col.prop(s, "skin_swaps")
        col.operator(HEROSWAP_OT_apply_skin.bl_idname, icon="MATERIAL")
        col.prop(s, "body_mesh")
        row = col.row()
        row.prop(s, "create_missing")
        row.prop(s, "max_distance")
        col.operator(HEROSWAP_OT_sync_shape_keys.bl_idname, icon="SHAPEKEY_DATA")
        col.prop(s, "eye_texture_dir")
        col.operator(HEROSWAP_OT_bake_eyes.bl_idname, icon="HIDE_OFF")
        col.separator()
        col.label(text="2. Fit to hero")
        col.prop(s, "hero_rig")
        col.prop(s, "bonemap")
        col.prop(s, "keep_jiggle")
        if s.keep_jiggle:
            col.prop(s, "jiggle_presets")
        col.operator(HEROSWAP_OT_fit.bl_idname, icon="ARMATURE_DATA")
        col.operator(HEROSWAP_OT_write_jiggle.bl_idname, icon="PHYSICS")
        col.separator()
        col.label(text="3. Export")
        col.prop(s, "export_collection")
        col.prop(s, "export_dir")
        col.operator(HEROSWAP_OT_export.bl_idname, icon="EXPORT")


classes = (HeroSwapSettings, HEROSWAP_OT_bonemerge, HEROSWAP_OT_apply_skin, HEROSWAP_OT_sync_shape_keys, HEROSWAP_OT_bake_eyes, HEROSWAP_OT_fit, HEROSWAP_OT_write_jiggle,
           HEROSWAP_OT_export, HEROSWAP_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.hero_swap = PointerProperty(type=HeroSwapSettings)


def unregister():
    del bpy.types.Scene.hero_swap
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
