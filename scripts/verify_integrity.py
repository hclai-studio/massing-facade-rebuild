"""用途：只读核验已保存模型的源网格、非目标展示副本、指定保护副本、
新增立面宽高包络及 Array 修改器；只写 JSON，不保存或修改 blend。
输入：--blend 模型；--source 实际源 FBX；--geometry 解析记录；--config 核验配置。
输出：--report 新 JSON 文件，含逐对象误差、八项检查及总 pass。
示例：blender --background --python verify_integrity.py -- --blend final.blend
      --source base.fbx --geometry temp/source_geometry.json
      --config verification-config.json --report temp/verification_report.json
以上示例写为一行。路径相对当前工作目录，源路径显式传入以支持归档搬迁。

配置结构（以下名称和尺寸仅为虚构示例，必须按本项目填写）：
{
  "coordinate_unit": "m",
  "target_objects": ["Block_A"],
  "source_object_prefix": "ORIGINAL_",
  "context_object_prefix": "SURROUNDING_",
  "context_display_max_min_z_m": 80,
  "protected_display_objects": {"Block_A": "PRESERVED_Block_A"},
  "facade_collection_prefixes": ["Facade", "Windows"],
  "interior_collection_prefixes": ["Interior"],
  "presentation_collection_prefixes": ["Presentation"],
  "front_start_xy": [0, 0], "front_end_xy": [12, 0], "inward_sign": 1,
  "facade_z_limits_m": [3, 7],
  "source_floor_levels_m": [0, 3, 6], "source_parapet_top_m": 7,
  "coordinate_tolerance_m": 0.00004, "evaluated_envelope_tolerance_m": 0.0001
}
全部选择器及边界必填；无保护副本时映射可为空，辅助集合列表可为空。
非目标对象最低 Z 不超过阈值时要求展示副本，超出时仍核验源网格。
inward_sign=1 为从起点到终点方向左侧，-1 为右侧；沿用源坐标的米制解释。
楼板标高与女儿墙字段只抄录配置，缺省为 null，不自动识别或核验。
容差可省略，默认保留原算法值；集合按前缀选择，对象按源名加前缀匹配。
原算法仅检查立面局部 u 宽度及世界 Z 高度，不检查进深或任意保护多边形，
地面/室内及展示构件单独列出，不纳入立面包络；不把这些记录宣称为全面保护核验。

新增 --facade-type irregular|motif（配置同名键 facade_type；默认 irregular）。
旧的单立面不规则配置及其报告字段保持不变，任何 Array 仍失败。
motif 允许 Array、集合实例及几何节点，报告 generation_usage 中记录宿主位置、
集合、修改器及实例世界变换；宽高核验包含求值网格和依赖图中的实例。
多立面：公共源保护参数仍在顶层，边界和选择器放在 facades 列表中，例如：
"facades": [
 {"id":"front","facade_type":"irregular","front_start_xy":[0,0],
  "front_end_xy":[12,0],"inward_sign":1,"facade_z_limits_m":[3,7],
  "facade_collection_prefixes":["Front"]},
 {"id":"side","facade_type":"motif","front_start_xy":[12,0],
  "front_end_xy":[12,8],"inward_sign":1,"facade_z_limits_m":[3,7],
  "facade_objects":["SideGenerator"]}]
每面必须显式提供边界及至少一种选择器（集合前缀或精确对象名）；集合递归。
每面的 facade_type 优先于全局默认，可用于两类并存。同一对象属于多个面时，
须在每个面都通过。未分配的生成工具在多立面模式中不能自动获得母题许可。
instances/GN 在不规则范围不获许可；旧报告只在这些新增机制或新参数出现时扩展。
实例源的隐藏状态不代替实例结果检查；不修改、应用修改器或保存 blend。
扩展核验使用当前依赖图；生成物所用修改器的视口/渲染启用状态不一致时，
该对象会报告无法完整核验并失败，不以未求值的渲染结果冒充通过。
target_objects 与 protected_display_objects 的键可为源网格或父级分组名；
源分组依赖 import_base 输出的 hierarchy，旧 JSON 需重新导入。
protected_display_objects 的值及各面的 facade_objects 可为 blend 中父级名，
递归包含全部子网格。源组到保护组按世界空间逐多边形几何做一一完整匹配，
不依赖副本名称或顺序；漏网格、多网格、几何改变均不通过。
source_object_prefix/context_object_prefix 仍按展开后的源网格名拼接，不是分组选择器。
"""
import bpy
import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _selection import source_names, scene_objects

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--blend', required=True, type=Path)
parser.add_argument('--geometry', required=True, type=Path)
parser.add_argument('--source', required=True, type=Path)
parser.add_argument('--config', required=True, type=Path)
parser.add_argument('--report', required=True, type=Path)
parser.add_argument('--facade-type', choices=['irregular', 'motif'])
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
BLEND = args.blend.resolve(strict=True)
REPORT = args.report.resolve()
if REPORT.exists() or REPORT in [p.resolve() for p in (args.blend, args.geometry, args.source, args.config)]:
    parser.error('Report must be a new file separate from all inputs')
CONFIG = json.loads(args.config.read_text(encoding='utf-8-sig'))
ROOT_CONFIG = CONFIG
FACE_CONFIGS = ROOT_CONFIG.get('facades')
DEFAULT_TYPE = args.facade_type or ROOT_CONFIG.get('facade_type', 'irregular')
if DEFAULT_TYPE not in ('irregular', 'motif'):
    parser.error('facade_type must be irregular or motif')
if FACE_CONFIGS is not None:
    if not isinstance(FACE_CONFIGS, list) or not FACE_CONFIGS:
        parser.error('facades must be a nonempty list')
    ids = [c['id'] for c in FACE_CONFIGS]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
        parser.error('facades must have unique nonempty ids')
    for face in FACE_CONFIGS:
        if any(k not in face for k in ('front_start_xy', 'front_end_xy', 'inward_sign', 'facade_z_limits_m')):
            parser.error('Every facade must explicitly supply its boundary')
        if not face.get('facade_collection_prefixes') and not face.get('facade_objects'):
            parser.error('Every facade must explicitly select its geometry')
    CONFIG = {**ROOT_CONFIG, **FACE_CONFIGS[0]}
SOURCE = json.loads(args.geometry.read_text(encoding='utf-8-sig'))
NODES = {n['name']: n for n in SOURCE['nodes']}
if not NODES or len(NODES) != len(SOURCE['nodes']):
    parser.error('Source nodes must be nonempty and have unique names')
try:
    TARGET = set(source_names(SOURCE, CONFIG['target_objects']))
except ValueError as error:
    parser.error(str(error))
if not TARGET:
    parser.error('target_objects must select existing source meshes or groups')
if CONFIG['coordinate_unit'] != 'm':
    parser.error('Verification coordinates and tolerances must be supplied in metres')
TOL = float(CONFIG.get('coordinate_tolerance_m', 4e-5))
EVALUATED_ENVELOPE_TOL = float(CONFIG.get('evaluated_envelope_tolerance_m', 1e-4))
if not all(math.isfinite(v) and v >= 0 for v in (TOL, EVALUATED_ENVELOPE_TOL)):
    parser.error('Tolerances must be finite and nonnegative')
SOURCE_PREFIX = CONFIG['source_object_prefix']
CONTEXT_PREFIX = CONFIG['context_object_prefix']
PROTECTED_DISPLAY = CONFIG['protected_display_objects']
try:
    protected_sources = {name: source_names(SOURCE, [name]) for name in PROTECTED_DISPLAY}
except ValueError as error:
    parser.error(str(error))
FACADE_PREFIXES = tuple(CONFIG.get('facade_collection_prefixes', []))
INTERIOR_PREFIXES = tuple(CONFIG['interior_collection_prefixes'])
PRESENTATION_PREFIXES = tuple(CONFIG['presentation_collection_prefixes'])
if (not FACADE_PREFIXES and not CONFIG.get('facade_objects')) or any(not isinstance(p, str) or not p for p in FACADE_PREFIXES + INTERIOR_PREFIXES + PRESENTATION_PREFIXES):
    parser.error('Collection prefixes must be nonempty strings; facade selection is required')
Z_MIN, Z_MAX = CONFIG['facade_z_limits_m']
DISPLAY_MAX_Z = CONFIG['context_display_max_min_z_m']
if not all(math.isfinite(v) for v in (Z_MIN, Z_MAX, DISPLAY_MAX_Z)) or Z_MIN >= Z_MAX:
    parser.error('Invalid facade limits or display threshold')
SOURCE_PATH = args.source.resolve(strict=True)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


blend_hash_before = sha(BLEND)
source_hash_before = sha(SOURCE_PATH)
if Path(bpy.data.filepath).resolve() != BLEND.resolve():
    bpy.ops.wm.open_mainfile(filepath=str(BLEND), use_scripts=False)


def world_vertices(obj):
    return [tuple(obj.matrix_world @ v.co) for v in obj.data.vertices]


def compare_exact(node, obj):
    if obj is None or obj.type != "MESH":
        return {"pass": False, "reason": "missing mesh object"}
    actual = world_vertices(obj)
    source_faces = node["faces"]
    actual_faces = [list(p.vertices) for p in obj.data.polygons]
    count_ok = len(actual) == len(node["verts"])
    max_error = max((abs(a[j] - b[j]) for a, b in zip(actual, node["verts"])
                     for j in range(3)), default=0.0)
    topology_ok = actual_faces == source_faces
    return {
        "pass": count_ok and topology_ok and max_error <= TOL,
        "source_vertices": len(node["verts"]),
        "actual_vertices": len(actual),
        "source_polygons": len(source_faces),
        "actual_polygons": len(actual_faces),
        "exact_indexed_topology": topology_ok,
        "max_world_coordinate_error_m": max_error,
    }


def compare_polygon_geometry(node, obj):
    """Allow a face-preserving display copy to duplicate vertex indices."""
    if obj is None or obj.type != "MESH":
        return {"pass": False, "reason": "missing display mesh"}
    actual_vertices = world_vertices(obj)
    actual_faces = [list(p.vertices) for p in obj.data.polygons]
    shape_ok = len(actual_faces) == len(node["faces"])
    max_error = 0.0
    if shape_ok:
        for sf, af in zip(node["faces"], actual_faces):
            if len(sf) != len(af):
                shape_ok = False
                break
            for si, ai in zip(sf, af):
                max_error = max(max_error, *(abs(node["verts"][si][j] - actual_vertices[ai][j])
                                             for j in range(3)))
    return {
        "pass": shape_ok and max_error <= TOL,
        "same_ordered_polygon_geometry": shape_ok,
        "source_polygons": len(node["faces"]),
        "actual_polygons": len(actual_faces),
        "max_world_coordinate_error_m": max_error,
        "note": "Vertex indexing may differ; ordered polygon coordinates are compared.",
    }


f = CONFIG['front_start_xy']
a = CONFIG['front_end_xy']
if any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in (f, a)):
    parser.error('Frontage endpoints must be finite XY pairs')
front_length = math.hypot(a[0] - f[0], a[1] - f[1])
if front_length == 0:
    parser.error('Frontage endpoints must be distinct')
ux, uy = (a[0] - f[0]) / front_length, (a[1] - f[1]) / front_length
inward_sign = CONFIG['inward_sign']
if inward_sign not in (-1, 1):
    parser.error('inward_sign must be -1 or 1')
dx, dy = -uy * inward_sign, ux * inward_sign


def local(v):
    x, y = v[0] - f[0], v[1] - f[1]
    return (x * ux + y * uy, x * dx + y * dy, v[2])


def bounds(vertices):
    p = [local(v) for v in vertices]
    if not p:
        return None
    return {"min_u_depth_z_m": [min(v[j] for v in p) for j in range(3)],
            "max_u_depth_z_m": [max(v[j] for v in p) for j in range(3)]}


def facade_bounds_check(obj):
    raw = bounds(world_vertices(obj))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated_obj.to_mesh()
    evaluated = bounds([tuple(evaluated_obj.matrix_world @ v.co) for v in evaluated_mesh.vertices])
    evaluated_obj.to_mesh_clear()

    def overshoot(value):
        lo, hi = value["min_u_depth_z_m"], value["max_u_depth_z_m"]
        return max(0.0, -lo[0], hi[0] - front_length, Z_MIN - lo[2], hi[2] - Z_MAX)

    raw_overshoot = overshoot(raw) if raw is not None else None
    evaluated_overshoot = overshoot(evaluated) if evaluated is not None else None
    return {"pass": raw is not None and evaluated is not None and raw_overshoot <= TOL and evaluated_overshoot <= EVALUATED_ENVELOPE_TOL,
            "raw": raw, "evaluated_modifiers": evaluated,
            "raw_max_envelope_overshoot_m": raw_overshoot,
            "evaluated_max_envelope_overshoot_m": evaluated_overshoot}


archive = {name: compare_exact(node, bpy.data.objects.get(SOURCE_PREFIX + name))
           for name, node in NODES.items()}
non_target_originals = {name: r for name, r in archive.items() if name not in TARGET}
non_target_display = {}
for name, node in NODES.items():
    if name in TARGET:
        continue
    obj = bpy.data.objects.get(CONTEXT_PREFIX + name)
    expected_display = min(v[2] for v in node["verts"]) <= DISPLAY_MAX_Z
    if obj is not None or expected_display:
        non_target_display[name] = compare_exact(node, obj)
    else:
        non_target_display[name] = {"pass": archive[name]["pass"],
                                 "display_copy_expected": False,
                                 "note": "Remote elevated source geometry retained in complete source archive."}
def compare_protected(name, display_name):
    source_members = protected_sources[name]
    root = bpy.data.objects.get(display_name)
    if source_members == [name] and (root is None or not root.children):
        return compare_polygon_geometry(NODES[name], root)
    try:
        display_members = sorted(scene_objects(bpy.context.scene, [display_name], meshes_only=True),
                                 key=lambda o: o.name)
    except ValueError as error:
        return {'pass': False, 'reason': str(error), 'source_meshes': source_members}
    # A complete bipartite matching handles identical meshes without guessing name correspondence.
    comparisons = {src: {obj.name: compare_polygon_geometry(NODES[src], obj)
                         for obj in display_members} for src in source_members}
    assigned = {}
    def assign(src, visited):
        for dest, result in comparisons[src].items():
            if not result['pass'] or dest in visited:
                continue
            visited.add(dest)
            if dest not in assigned or assign(assigned[dest], visited):
                assigned[dest] = src
                return True
        return False
    for src in source_members:
        assign(src, set())
    matched = {src: {'display_object': dest, **comparisons[src][dest]}
               for dest, src in assigned.items()}
    return {'pass': len(matched) == len(source_members) == len(display_members),
            'source_meshes': source_members, 'display_meshes': [o.name for o in display_members],
            'matched_meshes': matched,
            'unmatched_source_meshes': [n for n in source_members if n not in matched],
            'unmatched_display_meshes': [o.name for o in display_members if o.name not in assigned]}

protected_display = {name: compare_protected(name, display_name)
                     for name, display_name in PROTECTED_DISPLAY.items()}
array_modifiers = [{"object": o.name, "modifier": m.name} for o in bpy.data.objects
                   for m in o.modifiers if m.type == "ARRAY"]
facade = {}
interior_ground = {}
presentation = {}
for obj in bpy.data.objects:
    if obj.type != "MESH":
        continue
    memberships = [c.name for c in obj.users_collection]
    if any(n.startswith(FACADE_PREFIXES) for n in memberships):
        object_bounds = bounds(world_vertices(obj))
        if object_bounds is not None and object_bounds["max_u_depth_z_m"][2] <= Z_MIN + TOL:
            interior_ground[obj.name] = object_bounds
        else:
            facade[obj.name] = facade_bounds_check(obj)
    elif any(n.startswith(INTERIOR_PREFIXES) for n in memberships):
        interior_ground[obj.name] = bounds(world_vertices(obj))
    elif any(n.startswith(PRESENTATION_PREFIXES) for n in memberships):
        presentation[obj.name] = bounds(world_vertices(obj))

def generation_records():
    records = []
    for obj in bpy.data.objects:
        location = list(obj.matrix_world.translation)
        common = {'object': obj.name, 'world_location': location,
                  'collections': [c.name for c in obj.users_collection]}
        for modifier in obj.modifiers:
            if modifier.type in ('ARRAY', 'NODES'):
                records.append({**common, 'type': modifier.type, 'modifier': modifier.name,
                    'enabled_viewport': modifier.show_viewport, 'enabled_render': modifier.show_render,
                    'node_group': modifier.node_group.name if modifier.type == 'NODES' and modifier.node_group else None})
        if obj.instance_type == 'COLLECTION' and obj.instance_collection:
            records.append({**common, 'type': 'COLLECTION_INSTANCE',
                            'instance_collection': obj.instance_collection.name})
    return records


def select_face_objects(cfg):
    selected = scene_objects(bpy.context.scene, cfg.get('facade_objects', []))
    prefixes = tuple(cfg.get('facade_collection_prefixes', []))
    if any(not isinstance(p, str) or not p for p in prefixes):
        raise ValueError('Facade collection prefixes must be nonempty strings')
    for collection in bpy.data.collections:
        if prefixes and collection.name.startswith(prefixes):
            selected.update(o for o in collection.all_objects if o.name in bpy.context.scene.objects)
    return selected


def referenced_names(objects):
    """Include nested collection assets when deciding which facade owns a generator."""
    names = set()
    pending = list(objects)
    while pending:
        obj = pending.pop()
        if obj.name in names:
            continue
        names.add(obj.name)
        if obj.instance_type == 'COLLECTION' and obj.instance_collection:
            pending.extend(obj.instance_collection.all_objects)
    return names


def evaluate_face(cfg, objects, usage):
    start, end = cfg['front_start_xy'], cfg['front_end_xy']
    if any(len(p) != 2 or not all(math.isfinite(x) for x in p) for p in (start, end)):
        raise ValueError('Invalid facade XY endpoints')
    length = math.hypot(end[0]-start[0], end[1]-start[1])
    sign = cfg['inward_sign']
    lo_z, hi_z = cfg['facade_z_limits_m']
    if length == 0 or sign not in (-1, 1) or not all(math.isfinite(v) for v in (lo_z, hi_z)) or lo_z >= hi_z:
        raise ValueError('Invalid facade frame or height limits')
    unit_u = [(end[0]-start[0])/length, (end[1]-start[1])/length]
    unit_d = [-unit_u[1]*sign, unit_u[0]*sign]

    def projected_bounds(vertices):
        if not vertices:
            return None
        local_points = [((v[0]-start[0])*unit_u[0]+(v[1]-start[1])*unit_u[1],
                         (v[0]-start[0])*unit_d[0]+(v[1]-start[1])*unit_d[1], v[2]) for v in vertices]
        return {'min_u_depth_z_m': [min(p[j] for p in local_points) for j in range(3)],
                'max_u_depth_z_m': [max(p[j] for p in local_points) for j in range(3)]}

    def excess(bound):
        if bound is None:
            return None
        lower, upper = bound['min_u_depth_z_m'], bound['max_u_depth_z_m']
        return max(0.0, -lower[0], upper[0]-length, lo_z-lower[2], upper[2]-hi_z)

    graph = bpy.context.evaluated_depsgraph_get()

    def evaluated_vertices(evaluated_obj, matrix):
        if evaluated_obj.type not in ('MESH', 'CURVE', 'SURFACE', 'FONT', 'META'):
            return []
        mesh = evaluated_obj.to_mesh()
        try:
            return [tuple(matrix @ v.co) for v in mesh.vertices] if mesh else []
        finally:
            evaluated_obj.to_mesh_clear()

    selected_names = {obj.name for obj in objects}
    instance_vertices = {name: [] for name in selected_names}
    instance_reports = {name: [] for name in selected_names}
    for instance in graph.object_instances:
        if not instance.is_instance or not instance.parent:
            continue
        parent = instance.parent.original.name
        if parent not in selected_names:
            continue
        verts = evaluated_vertices(instance.object, instance.matrix_world)
        instance_vertices[parent].extend(verts)
        instance_reports[parent].append({'source_object': instance.object.original.name,
            'world_location': list(instance.matrix_world.translation),
            'world_matrix': [list(row) for row in instance.matrix_world],
            'vertex_count': len(verts), 'bounds': projected_bounds(verts)})

    results, ground = {}, {}
    for obj in sorted(objects, key=lambda o: o.name):
        is_generator = any(r['object'] == obj.name for r in usage)
        if obj.type not in ('MESH', 'CURVE', 'SURFACE', 'FONT', 'META') and not is_generator:
            continue
        raw_vertices = world_vertices(obj) if obj.type == 'MESH' else []
        evaluated_obj = obj.evaluated_get(graph)
        ev = evaluated_vertices(evaluated_obj, evaluated_obj.matrix_world)
        ev.extend(instance_vertices[obj.name])
        raw, evaluated = projected_bounds(raw_vertices), projected_bounds(ev)
        assets = referenced_names([obj])
        mismatches = [{'object': candidate.name, 'modifier': modifier.name}
                      for candidate in bpy.data.objects if candidate.name in assets
                      for modifier in candidate.modifiers if modifier.show_viewport != modifier.show_render]
        # Keep legacy ground exclusion, but a generated upper storey cannot hide behind a low seed.
        if not mismatches and raw is not None and evaluated is not None and max(raw['max_u_depth_z_m'][2], evaluated['max_u_depth_z_m'][2]) <= lo_z + TOL:
            ground[obj.name] = {'raw': raw, 'evaluated_modifiers': evaluated}
            continue
        raw_error, ev_error = excess(raw), excess(evaluated)
        raw_ok = (raw is None and is_generator) or (raw_error is not None and raw_error <= TOL)
        results[obj.name] = {'pass': raw_ok and ev_error is not None and ev_error <= EVALUATED_ENVELOPE_TOL,
            'raw': raw, 'evaluated_modifiers': evaluated,
            'raw_max_envelope_overshoot_m': raw_error,
            'evaluated_max_envelope_overshoot_m': ev_error,
            'evaluated_instances': instance_reports[obj.name]}
        if mismatches:
            results[obj.name]['pass'] = False
            results[obj.name]['unevaluated_render_modifiers'] = mismatches
            results[obj.name]['reason'] = 'Viewport/render modifier states differ; render envelope is not certified'
        if evaluated is None:
            results[obj.name]['reason'] = 'No evaluated mesh geometry; cannot certify this generator'
    return {'pass': bool(results) and all(r['pass'] for r in results.values()),
        'facade_type': cfg.get('facade_type', DEFAULT_TYPE),
        'source_frontage': {'start_xy': start, 'end_xy': end, 'length_m': length,
                            'inward_xy': unit_d, 'facade_z_limits_m': [lo_z, hi_z]},
        'selected_objects': sorted(selected_names), 'facade_objects': results,
        'interior_and_ground_objects': ground}


usage = generation_records()
extended = FACE_CONFIGS is not None or DEFAULT_TYPE == 'motif' or any(r['type'] != 'ARRAY' for r in usage) or bool(CONFIG.get('facade_objects'))
face_reports = {}
methods_allowed = True
if extended:
    declarations = FACE_CONFIGS if FACE_CONFIGS is not None else [dict(CONFIG, id='default')]
    scopes = []
    for declaration in declarations:
        cfg = {**ROOT_CONFIG, **declaration}
        # A per-face exact-name selector must not inherit another face's prefixes.
        if FACE_CONFIGS is not None:
            cfg['facade_collection_prefixes'] = declaration.get('facade_collection_prefixes', [])
            cfg['facade_objects'] = declaration.get('facade_objects', [])
        kind = declaration.get('facade_type', DEFAULT_TYPE)
        if FACE_CONFIGS is None:
            kind = DEFAULT_TYPE
        if kind not in ('irregular', 'motif'):
            raise ValueError('Unknown facade_type: ' + str(kind))
        cfg['facade_type'] = kind
        objects = select_face_objects(cfg)
        scopes.append((declaration['id'], kind, referenced_names(objects)))
        face_reports[declaration['id']] = evaluate_face(cfg, objects, usage)
    for record in usage:
        owners = [(name, kind) for name, kind, names in scopes if record['object'] in names]
        record['facades'] = [name for name, kind in owners]
        record['allowed'] = bool(owners) and all(kind == 'motif' for name, kind in owners)
        methods_allowed = methods_allowed and record['allowed']

source_hash_after = sha(SOURCE_PATH)
blend_hash_after = sha(BLEND)
checks = {
    "source_fbx_matches_recorded_sha256": source_hash_before == SOURCE["sha256"] == source_hash_after,
    "blend_file_unchanged_by_verification": blend_hash_before == blend_hash_after,
    "all_source_meshes_exact": len(archive) == len(NODES) and all(r["pass"] for r in archive.values()),
    "all_non_target_originals_exact": all(r["pass"] for r in non_target_originals.values()),
    "all_non_target_display_copies_preserved": all(r["pass"] for r in non_target_display.values()),
    "protected_display_geometry_preserved": all(r["pass"] for r in protected_display.values()),
    "no_array_modifiers": not array_modifiers,
    "all_new_facade_meshes_within_source_front_width_and_height": bool(facade) and all(r["pass"] for r in facade.values()),
}
report = {
    "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    "pass": all(checks.values()),
    "checks": checks,
    "read_only": True,
    "coordinate_tolerance_m": TOL,
    "raw_facade_envelope_tolerance_m": TOL,
    "evaluated_facade_envelope_tolerance_m": EVALUATED_ENVELOPE_TOL,
    "evaluated_tolerance_note": "Evaluated modifier envelope uses evaluated_facade_envelope_tolerance_m; source and raw vertices use coordinate_tolerance_m.",
    "blend_path": str(BLEND),
    "blend_sha256_before": blend_hash_before,
    "blend_sha256_after": blend_hash_after,
    "source_fbx_path": str(SOURCE_PATH),
    "source_fbx_sha256_recorded": SOURCE["sha256"],
    "source_fbx_sha256_before": source_hash_before,
    "source_fbx_sha256_after": source_hash_after,
    "source_frontage": {"start_xy": f[:2], "end_xy": a[:2], "length_m": front_length,
                        "start_to_end_xy": [ux, uy], "inward_xy": [dx, dy],
                        "facade_z_limits_m": [Z_MIN, Z_MAX],
                        "source_floor_levels_m": CONFIG.get("source_floor_levels_m"),
                        "source_parapet_top_m": CONFIG.get("source_parapet_top_m")},
    "archive_objects": archive,
    "non_target_display_objects": non_target_display,
    "protected_display_objects": protected_display,
    "array_modifiers": array_modifiers,
    "facade_mesh_count": len(facade),
    "facade_objects": facade,
    "interior_and_ground_mesh_bounds_separate_from_facade": interior_ground,
    "presentation_mesh_bounds_exempt_from_facade_envelope": presentation,
    "scope_note": "Verifies saved geometry and source protection, not engineering or image interpretation.",
}
if extended:
    checks.pop('no_array_modifiers')
    checks['generation_methods_allowed'] = methods_allowed
    checks['all_new_facade_meshes_within_source_front_width_and_height'] = bool(face_reports) and all(r['pass'] for r in face_reports.values())
    report['pass'] = all(checks.values())
    report['facades'] = face_reports
    report['generation_usage'] = usage
    report['facade_objects'] = {face_id + '/' + name: result for face_id, face_report in face_reports.items()
                                for name, result in face_report['facade_objects'].items()}
    report['facade_mesh_count'] = len(report['facade_objects'])
    report['source_frontage_note'] = 'Legacy source_frontage describes the first face; per-face boundaries are in facades.'
if TARGET != set(CONFIG['target_objects']):
    report['target_selection'] = {'requested': CONFIG['target_objects'], 'resolved_meshes': sorted(TARGET)}
REPORT.parent.mkdir(parents=True, exist_ok=True)
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("SOURCE_INTEGRITY_REPORT", json.dumps({"pass": report["pass"], "checks": checks,
                                             "report": str(REPORT), "facade_mesh_count": report['facade_mesh_count']}, ensure_ascii=False))
