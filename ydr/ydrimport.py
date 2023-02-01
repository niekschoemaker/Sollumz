from math import pi, radians
import bmesh
import os
import bpy
from mathutils import Matrix, Vector
from .shader_materials import create_shader, create_tinted_shader_graph, get_detail_extra_sampler
from ..ybn.ybnimport import composite_to_obj, bound_to_obj
from ..sollumz_properties import SOLLUMZ_UI_NAMES, LODLevel, TextureFormat, TextureUsage, SollumType, LightType
from ..cwxml.drawable import YDR, Shader, ShaderGroup
from ..tools.meshhelper import create_uv_layer, create_vertexcolor_layer
from ..tools.blenderhelper import build_tag_bone_map, remove_unused_vertex_groups_of_mesh, join_objects, remove_unused_materials, get_addon_preferences
from ..tools.drawablehelper import join_drawable_geometries, drawable_to_asset

BONE_TAIL_POS = (0, 0.05, 0)


def shadergroup_to_materials(shader_group, filepath):
    materials = []

    for shader in shader_group.shaders:
        material = shader_item_to_material(shader, shader_group, filepath)
        materials.append(material)

    return materials


def shader_item_to_material(shader: Shader, shader_group: ShaderGroup, filepath: str):
    texture_folder = os.path.dirname(
        filepath) + "\\" + os.path.basename(filepath)[:-8]

    filename = shader.filename

    if not filename:
        filename = f"{shader.name}.sps"

    material = create_shader(filename)

    material.shader_properties.renderbucket = shader.render_bucket
    material.shader_properties.filename = shader.filename

    for param in shader.parameters:
        for n in material.node_tree.nodes:
            if isinstance(n, bpy.types.ShaderNodeTexImage):
                if param.name == n.name:
                    texture_path = os.path.join(
                        texture_folder, param.texture_name + ".dds")
                    if os.path.isfile(texture_path):
                        img = bpy.data.images.load(
                            texture_path, check_existing=True)
                        n.image = img
                    if not n.image:
                        # for texture shader parameters with no name
                        if not param.texture_name:
                            continue
                        # Check for existing texture
                        existing_texture = None
                        for image in bpy.data.images:
                            if image.name == param.texture_name:
                                existing_texture = image
                        texture = bpy.data.images.new(
                            name=param.texture_name, width=512, height=512) if not existing_texture else existing_texture
                        n.image = texture

                    # assign non color to normal maps
                    if "Bump" in param.name:
                        n.image.colorspace_settings.name = "Non-Color"

                    # Assign embedded texture dictionary properties
                    if shader_group.texture_dictionary is not None:
                        for texture in shader_group.texture_dictionary:
                            if texture.name == param.texture_name:
                                n.texture_properties.embedded = True
                                try:
                                    format = TextureFormat[texture.format.replace(
                                        "D3DFMT_", "")]
                                    n.texture_properties.format = format
                                except AttributeError:
                                    print(
                                        f"Failed to set texture format: format '{texture.format}' unknown.")

                                try:
                                    usage = TextureUsage[texture.usage]
                                    n.texture_properties.usage = usage
                                except AttributeError:
                                    print(
                                        f"Failed to set texture usage: usage '{texture.usage}' unknown.")

                                n.texture_properties.extra_flags = texture.extra_flags

                                for prop in dir(n.texture_flags):
                                    for uf in texture.usage_flags:
                                        if uf.lower() == prop:
                                            setattr(
                                                n.texture_flags, prop, True)

                    if not n.texture_properties.embedded:
                        # Set external texture name for non-embedded textures
                        n.image.source = "FILE"
                        n.image.filepath = "//" + param.texture_name + ".dds"

                    if param.name == "BumpSampler" and hasattr(n.image, "colorspace_settings"):
                        n.image.colorspace_settings.name = "Non-Color"

            elif isinstance(n, bpy.types.ShaderNodeValue):
                if param.name == n.name[:-2]:
                    key = n.name[-1]
                    if key == "x":
                        n.outputs[0].default_value = param.x
                    if key == "y":
                        n.outputs[0].default_value = param.y
                    if key == "z":
                        n.outputs[0].default_value = param.z
                    if key == "w":
                        n.outputs[0].default_value = param.w

    # assign extra detail node image for viewing
    dtl_ext = get_detail_extra_sampler(material)
    if dtl_ext:
        dtl = material.node_tree.nodes["DetailSampler"]
        dtl_ext.image = dtl.image

    return material


def bone_to_obj(bone, armature):

    if armature is None:
        return None

    # bpy.context.view_layer.objects.active = armature
    edit_bone = armature.data.edit_bones.new(bone.name)
    if bone.parent_index != -1:
        edit_bone.parent = armature.data.edit_bones[bone.parent_index]

    # https://github.com/LendoK/Blender_GTA_V_model_importer/blob/master/importer.py
    mat_rot = bone.rotation.to_matrix().to_4x4()
    mat_loc = Matrix.Translation(bone.translation)
    mat_sca = Matrix.Scale(1, 4, bone.scale)

    edit_bone.head = (0, 0, 0)
    edit_bone.tail = BONE_TAIL_POS
    edit_bone.matrix = mat_loc @ mat_rot @ mat_sca
    if edit_bone.parent is not None:
        edit_bone.matrix = edit_bone.parent.matrix @ edit_bone.matrix

    return bone.name


def set_bone_properties(bone, armature):

    bl_bone = armature.pose.bones[bone.name].bone
    bl_bone.bone_properties.tag = bone.tag
    # LimitRotation and Unk0 have their special meanings, can be deduced if needed when exporting
    flags_restricted = set(["LimitRotation", "Unk0"])
    for _flag in bone.flags:
        if _flag in flags_restricted:
            continue

        flag = bl_bone.bone_properties.flags.add()
        flag.name = _flag


def skeleton_to_obj(skeleton, armature):

    if skeleton is None:
        return None

    bpy.context.view_layer.objects.active = armature
    bones = skeleton.bones
    bpy.ops.object.mode_set(mode="EDIT")

    for bone in bones:
        bone_to_obj(bone, armature)

    bpy.ops.object.mode_set(mode="OBJECT")

    for bone in bones:
        set_bone_properties(bone, armature)

    return armature


def set_rotation_limit(joint, bone):

    if bone is None:
        return None

    constraint = bone.constraints.new("LIMIT_ROTATION")
    constraint.owner_space = "LOCAL"
    constraint.use_limit_x = True
    constraint.use_limit_y = True
    constraint.use_limit_z = True
    constraint.max_x = joint.max.x
    constraint.max_y = joint.max.y
    constraint.max_z = joint.max.z
    constraint.min_x = joint.min.x
    constraint.min_y = joint.min.y
    constraint.min_z = joint.min.z

    # joints don't have an unique name so return the bone name instead
    return bone.name


def rotation_limits_to_obj(rotation_limits, armature):

    # there should be more joint types than RotationLimits
    tag_bone_map = build_tag_bone_map(armature)
    if tag_bone_map is None:
        return None

    bones_with_constraint = []
    for joint in rotation_limits:
        bone = armature.pose.bones.get(tag_bone_map[joint.bone_id])
        bone_name = set_rotation_limit(joint, bone)
        bones_with_constraint.append(bone_name)

    return bones_with_constraint


def light_to_obj(light, armature_obj=None):
    preferences = get_addon_preferences(bpy.context)
    intensity_factor = 500 if preferences.scale_light_intensity else 1

    light_type = None

    if light.type == "Point":
        light_type = LightType.POINT
    elif light.type == "Spot":
        light_type = LightType.SPOT
    elif light.type == "Capsule":
        light_type = LightType.CAPSULE
    else:
        raise TypeError("Invalid light type")

    name = SOLLUMZ_UI_NAMES[light_type]

    # WORK AROUND FOR INVALID LIGHT TYPES
    try:
        light_data = bpy.data.lights.new(
            name=name, type=light.type.upper())
    except:
        light_data = bpy.data.lights.new(
            name=name, type="SPOT")

    lobj = bpy.data.objects.new(name=name, object_data=light_data)
    bpy.context.collection.objects.link(lobj)
    lobj.sollum_type = SollumType.LIGHT

    if armature_obj and armature_obj.type == "ARMATURE":
        armature = armature_obj.data
        bone_map = {
            bone.bone_properties.tag: bone for bone in armature.bones}
        # Apply bone id
        if light.bone_id in bone_map.keys():
            constraint = lobj.constraints.new("COPY_TRANSFORMS")
            constraint.target = armature_obj
            constraint.subtarget = bone_map[light.bone_id].name
            constraint.mix_mode = "BEFORE_FULL"
            constraint.target_space = "POSE"
            constraint.owner_space = "LOCAL"

    # Calculate orientation
    light.direction.negate()
    bitangent = light.direction.cross(light.tangent).normalized()
    mat = Matrix().to_3x3()
    mat.col[0] = light.tangent
    mat.col[1] = bitangent
    mat.col[2] = light.direction
    lobj.matrix_basis = mat.to_4x4()

    lobj.data.time_flags.total = str(light.time_flags)
    lobj.data.light_flags.total = str(light.flags)

    lobj.location = light.position
    lobj.data.sollum_type = light_type
    lobj.name = name
    lobj.data.name = lobj.name
    lobj.data.color = [channel / 255 for channel in light.color]
    lobj.data.energy = light.intensity * intensity_factor
    lobj.data.light_properties.flashiness = light.flashiness
    lobj.data.light_properties.flags = light.flags
    lobj.data.light_properties.group_id = light.group_id
    lobj.data.light_properties.time_flags = light.time_flags
    lobj.data.use_custom_distance = True
    lobj.data.cutoff_distance = light.falloff
    lobj.data.shadow_soft_size = light.falloff_exponent / 5
    lobj.data.light_properties.culling_plane_normal = light.culling_plane_normal
    lobj.data.light_properties.culling_plane_offset = light.culling_plane_offset
    lobj.data.light_properties.unknown_45 = light.unknown_45
    lobj.data.light_properties.unknown_46 = light.unknown_46
    lobj.data.volume_factor = light.volume_intensity
    lobj.data.light_properties.shadow_blur = light.shadow_blur
    lobj.data.light_properties.volume_size_scale = light.volume_size_scale
    lobj.data.light_properties.volume_outer_color = [
        channel / 255 for channel in light.volume_outer_color]
    lobj.data.light_properties.light_hash = light.light_hash
    lobj.data.light_properties.volume_outer_intensity = light.volume_outer_intensity
    lobj.data.light_properties.corona_size = light.corona_size
    lobj.data.light_properties.volume_outer_exponent = light.volume_outer_exponent
    lobj.data.light_properties.light_fade_distance = light.light_fade_distance
    lobj.data.light_properties.shadow_fade_distance = light.shadow_fade_distance
    lobj.data.light_properties.specular_fade_distance = light.specular_fade_distance
    lobj.data.light_properties.volumetric_fade_distance = light.volumetric_fade_distance
    lobj.data.shadow_buffer_clip_start = light.shadow_near_clip
    lobj.data.light_properties.corona_intensity = light.corona_intensity
    lobj.data.light_properties.corona_z_bias = light.corona_z_bias
    if light_type == LightType.SPOT:
        lobj.data.spot_blend = abs(
            (radians(light.cone_inner_angle) / pi) - 1)
        lobj.data.spot_size = radians(light.cone_outer_angle) * 2
    lobj.data.light_properties.extent = light.extent
    lobj.data.light_properties.projected_texture_hash = light.projected_texture_hash

    return lobj


def obj_from_buffer(vertex_buffer, index_buffer, material, bones=None, name=None, bone_ids=None):
    vertices = []
    normals = []
    texcoords = {}
    colors = {}

    has_normals = False

    for vertex in vertex_buffer:
        vertices.append(vertex.position)
        if hasattr(vertex, "normal"):
            has_normals = True
            normals.append(vertex.normal)

        for key, value in vertex._asdict().items():
            if "texcoord" in key:
                if not key in texcoords.keys():
                    texcoords[key] = []
                texcoords[key].append(value)
            if "colour" in key:
                if not key in colors.keys():
                    colors[key] = []
                colors[key].append(value)

    # create mesh
    mesh = bpy.data.meshes.new(SOLLUMZ_UI_NAMES[SollumType.DRAWABLE_GEOMETRY])
    mesh.from_pydata(vertices, [], index_buffer)
    mesh.validate()

    # set normals
    if has_normals:
        mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
        mesh.normals_split_custom_set_from_vertices(
            [Vector(normal).normalized() for normal in normals])
        mesh.use_auto_smooth = True

    # set uvs
    i = 0
    for coords in texcoords.values():
        create_uv_layer(mesh, i, coords)
        i += 1

    # set vertex colors
    i = 0
    for color in colors.values():
        create_vertexcolor_layer(mesh, i, color)
        i += 1

    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(material)

    # set weights
    if hasattr(vertex_buffer[0], "blendweights"):
        if vertex_buffer[0].blendweights is not None and len(vertex_buffer) > 0:
            bone_count = 256 if not bones else len(bones)
            num = max(256, bone_count)
            for i in range(num):
                bone_name = "UNK"
                bone_id = i
                if bone_ids and i < len(bone_ids):
                    bone_id = bone_ids[i]

                if bones and bone_id < bone_count:
                    bone_name = bones[bone_id].name
                elif bone_ids:
                    bone_name = f"UNKNOWN_BONE.{str(bone_id)}.{bone_ids[len(bone_ids) - 1]}"
                obj.vertex_groups.new(name=bone_name)

            for vertex_idx, vertex in enumerate(vertex_buffer):
                for i in range(0, 4):
                    weight = vertex.blendweights[i] / 255
                    index = vertex.blendindices[i]
                    if weight > 0.0:
                        obj.vertex_groups[index].add(
                            [vertex_idx], weight, "ADD")

            remove_unused_vertex_groups_of_mesh(obj)

    obj.sollum_type = SollumType.DRAWABLE_GEOMETRY
    bpy.context.collection.objects.link(obj)

    return obj


def geometry_to_obj(geometry, material, bones=None, name=None):
    vertex_buffer = geometry.vertex_buffer.get_data()
    index_buffer = [geometry.index_buffer.data[i * 3:(i + 1) * 3]
                    for i in range((len(geometry.index_buffer.data) + 3 - 1) // 3)]
    bone_ids = geometry.bone_ids
    return obj_from_buffer(vertex_buffer, index_buffer, material, bones, name, bone_ids)


def drawable_model_to_obj(model, materials, name, lod, bones=None, import_settings=None, armature=None):
    dobj = bpy.data.objects.new(
        SOLLUMZ_UI_NAMES[SollumType.DRAWABLE_MODEL], None)
    dobj.sollum_type = SollumType.DRAWABLE_MODEL
    dobj.empty_display_size = 0
    dobj.drawable_model_properties.sollum_lod = lod
    dobj.drawable_model_properties.render_mask = model.render_mask

    if armature is not None:
        if len(armature.pose.bones) > model.bone_index:
            parent_bone_name = armature.pose.bones[model.bone_index].name
            dobj.parent = armature
            dobj.parent_type = "BONE"
            dobj.parent_bone = parent_bone_name
            dobj.delta_location -= Vector(BONE_TAIL_POS)

    dobj.drawable_model_properties.unknown_1 = model.unknown_1
    dobj.drawable_model_properties.flags = model.flags

    for child in model.geometries:
        child_obj = geometry_to_obj(
            child, materials[child.shader_index], bones, name)
        child_obj.sollum_type = SollumType.DRAWABLE_GEOMETRY
        child_obj.parent = dobj
        # do this after because object has to be linked, will do nothing if a tint parameter is not found... kinda stupid way to do it but its how
        # we check if its a tint shader in the first place so ig it makes sense...
        create_tinted_shader_graph(child_obj)

    bpy.context.collection.objects.link(dobj)

    return dobj


def create_lights(lights, parent, armature_obj=None):
    if armature_obj is None:
        armature_obj = parent
    lights_parent = bpy.data.objects.new("Lights", None)
    lights_parent.empty_display_size = 0
    lights_parent.parent = parent
    bpy.context.collection.objects.link(lights_parent)
    for light in lights:
        lobj = light_to_obj(light, armature_obj)
        lobj.parent = lights_parent

    return lights_parent


def drawable_to_obj(drawable, filepath, name, bones_override=None, materials=None, import_settings=None, is_ydd=None):

    if not materials:
        materials = shadergroup_to_materials(drawable.shader_group, filepath)

    obj = None
    bones = None

    if len(drawable.skeleton.bones) > 0:
        skel = bpy.data.armatures.new(name + ".skel")
        obj = bpy.data.objects.new(name, skel)
    else:
        obj = bpy.data.objects.new(name, None)

    obj.sollum_type = SollumType.DRAWABLE
    obj.empty_display_size = 0
    obj.drawable_properties.lod_dist_high = drawable.lod_dist_high
    obj.drawable_properties.lod_dist_med = drawable.lod_dist_med
    obj.drawable_properties.lod_dist_low = drawable.lod_dist_low
    obj.drawable_properties.lod_dist_vlow = drawable.lod_dist_vlow
    obj.drawable_properties.unknown_9A = drawable.unknown_9A

    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj

    bones = None
    if len(drawable.skeleton.bones) > 0:
        bones = drawable.skeleton.bones
        skeleton_to_obj(drawable.skeleton, obj)

    if len(drawable.joints.rotation_limits) > 0:
        rotation_limits_to_obj(drawable.joints.rotation_limits, obj)

    if bones_override is not None:
        bones = bones_override

    if drawable.bounds:
        for bound in drawable.bounds:
            bobj = None
            if bound.type == "Composite":
                bobj = composite_to_obj(
                    bound, SOLLUMZ_UI_NAMES[SollumType.BOUND_COMPOSITE], True)
                bobj.parent = obj
            else:
                bobj = bound_to_obj(bound)
                if bobj:
                    bobj.parent = obj

    armature = obj if obj.type == "ARMATURE" and not is_ydd else None

    for model in drawable.drawable_models_high:
        dobj = drawable_model_to_obj(
            model, materials, drawable.name, LODLevel.HIGH, bones, import_settings, armature)
        dobj.parent = obj

    for model in drawable.drawable_models_med:
        dobj = drawable_model_to_obj(
            model, materials, drawable.name, LODLevel.MEDIUM, bones, import_settings, armature)
        dobj.parent = obj

    for model in drawable.drawable_models_low:
        dobj = drawable_model_to_obj(
            model, materials, drawable.name, LODLevel.LOW, bones, import_settings, armature)
        dobj.parent = obj

    for model in drawable.drawable_models_vlow:
        dobj = drawable_model_to_obj(
            model, materials, drawable.name, LODLevel.VERYLOW, bones, import_settings, armature)
        dobj.parent = obj

    if armature is not None:
        for model in obj.children:
            if model.sollum_type != SollumType.DRAWABLE_MODEL:
                continue

        for child in model.children:
            if child.sollum_type != SollumType.DRAWABLE_GEOMETRY:
                continue

            mod = child.modifiers.new("Armature", "ARMATURE")
            mod.object = obj

    if len(drawable.lights) > 0:
        create_lights(drawable.lights, obj)

    return obj


def import_ydr(filepath, import_settings):
    ydr_xml = YDR.from_xml_file(filepath)
    drawable = drawable_to_obj(ydr_xml, filepath, os.path.basename(
        filepath.replace(YDR.file_extension, "")), None, None, import_settings)
    if import_settings.join_geometries:
        for child in drawable.children:
            if child.sollum_type == SollumType.DRAWABLE_MODEL:
                join_drawable_geometries(child)

    if import_settings.import_as_asset:
        drawable_to_asset(drawable)
