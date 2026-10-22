"""用途：按指定建筑范围自动取景，渲染固定检查图；不修改几何，不保存 blend。
输入：--blend 模型；--config JSON；--output-dir 图片目录（默认 views/）。
输出：01_front.png、02_street.png、03_top.png、04_<局部名>.png 等，
      以及 render_manifest.json（相机参数、目标范围、文件与几何只读核对）。
示例：blender --background --disable-autoexec --python render_views.py --
      --blend model.blend --config views-config.json --output-dir run/views
      --resolution 1600 900 --samples 32
示例写为一行。路径相对当前工作目录；输出文件已存在时拒绝覆盖。

配置示例（仅为虚构值）：
{
  "target_objects": ["Building_A"],
  "target_collections": ["Facade"],
  "front_start_xy": [0, 0], "front_end_xy": [12, 0], "inward_sign": 1,
  "margin": 1.12, "street_side": -1, "lens_mm": 50,
  "closeups": [
    {"name": "corner", "local_bounds": [[0, -1, 3], [4, 3, 7]],
     "view": "perspective"}
  ]
}
目标按网格或父级分组精确名称与集合精确名称的并集选择（两者均递归），
顶层及各立面的 target_objects 都支持父级名；至少一种选择器非空。
front_start_xy/end_xy 明确立面方向，不能从包围盒猜出哪面是街面；
inward_sign=1 为起点至终点方向左侧，-1 为右侧。所有坐标使用模型世界单位。
局部 local_bounds 为 [[u_min,depth_min,z_min],[u_max,depth_max,z_max]]：
u 沿起终点方向，depth 向建筑内为正，z 是世界标高；view 可为 front 或 perspective。
margin>1、street_side=±1、lens_mm>0 可省略，默认如示例。closeups 可为空。
--engine 可指定 CYCLES、BLENDER_EEVEE_NEXT 或 BLENDER_WORKBENCH，省略则沿用场景；
--device CPU/GPU 默认为 CPU，仅影响 Cycles；GPU 须在运行环境预先可用。
不调整灯光、材质、对象显隐或集合显隐；被隐藏／遮挡的几何不会为了截图而改动。
目标应选当前可渲染的展示对象；包围盒只负责取景，不自动判断遮挡或 QA 是否通过。

多立面配置增加 facades 列表；每面必填安全文件名式 id、front_start_xy、
front_end_xy、inward_sign，可各自给 target_objects/target_collections 与 closeups。
省略每面的目标选择器时继承公共目标；省略整个 facades 时旧行为与文件名不变。
例如："facades":[
 {"id":"front","front_start_xy":[0,0],"front_end_xy":[12,0],"inward_sign":1},
 {"id":"side","front_start_xy":[12,0],"front_end_xy":[12,8],"inward_sign":1}]
首面沿用 01_front、02_street、03_top 和首面近景；其他面的正立面、转角透视
及近景依次追加到 views/。多立面俯视覆盖各面目标的并集。
默认对端点相接且不平行的两面自动增加转角透视；也可在顶层明确指定
"corners":[{"id":"entry","facades":["front","side"]}]。
无法确定任何转角时停止并要求补充 corners，不猜立面间的连接关系。
新增视角包含求值网格和集合/GN实例；不保存相机到输入 blend。
"""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import struct
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _selection import scene_objects

import bpy
from mathutils import Matrix, Vector


def file_sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def geometry_sha():
    """Compare all mesh coordinates, indexed topology and non-camera transforms."""
    h = hashlib.sha256()
    for obj in sorted(bpy.data.objects, key=lambda o: o.name):
        if obj.type == 'CAMERA':
            continue
        h.update((obj.name + '\0' + obj.type + '\0').encode('utf-8'))
        h.update(struct.pack('<16d', *(x for row in obj.matrix_world for x in row)))
        if obj.type == 'MESH':
            mesh = obj.data
            h.update(struct.pack('<3Q', len(mesh.vertices), len(mesh.edges), len(mesh.polygons)))
            for vertex in mesh.vertices:
                h.update(struct.pack('<3d', *vertex.co))
            for edge in mesh.edges:
                h.update(struct.pack('<2Q', *edge.vertices))
            for polygon in mesh.polygons:
                h.update(struct.pack('<Q', len(polygon.vertices)))
                h.update(struct.pack('<' + 'Q' * len(polygon.vertices), *polygon.vertices))
    return h.hexdigest()


def finite_vector(value, length, label):
    if not isinstance(value, list) or len(value) != length or not all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in value):
        raise ValueError(label + ' must contain finite coordinates')
    return Vector(value)


def box_corners(lo, hi):
    return [Vector(p) for p in itertools.product(*zip(lo, hi))]


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--blend', required=True, type=Path)
parser.add_argument('--config', required=True, type=Path)
parser.add_argument('--output-dir', type=Path, default=Path('views'))
parser.add_argument('--resolution', nargs=2, type=int, default=[1600, 900], metavar=('WIDTH', 'HEIGHT'))
parser.add_argument('--samples', type=int, default=32)
parser.add_argument('--engine', choices=['CYCLES', 'BLENDER_EEVEE_NEXT', 'BLENDER_WORKBENCH'])
parser.add_argument('--device', choices=['CPU', 'GPU'], default='CPU')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
if min(args.resolution) <= 0 or args.samples <= 0:
    parser.error('Resolution and samples must be positive')
source = args.blend.resolve(strict=True)
config = json.loads(args.config.read_text(encoding='utf-8-sig'))
root_config = config
face_configs = root_config.get('facades')
if face_configs is not None:
    if not isinstance(face_configs, list) or not face_configs:
        parser.error('facades must be a nonempty list')
    ids = [face['id'] for face in face_configs]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', i) for i in ids):
        parser.error('Facade ids must be unique safe filename identifiers')
    for face in face_configs:
        if any(key not in face for key in ('front_start_xy', 'front_end_xy', 'inward_sign')):
            parser.error('Every facade needs an explicit frame')
    config = {**root_config, **face_configs[0]}
    if 'target_objects' in face_configs[0] or 'target_collections' in face_configs[0]:
        config['target_objects'] = face_configs[0].get('target_objects', [])
        config['target_collections'] = face_configs[0].get('target_collections', [])
out_dir = args.output_dir.resolve()
start = finite_vector(config['front_start_xy'], 2, 'front_start_xy')
end = finite_vector(config['front_end_xy'], 2, 'front_end_xy')
delta = end - start
if delta.length == 0 or config['inward_sign'] not in (-1, 1):
    parser.error('Distinct frontage endpoints and inward_sign=±1 required')
u = Vector((delta.x, delta.y, 0)).normalized()
inward = Vector((-u.y, u.x, 0)) * config['inward_sign']
up = Vector((0, 0, 1))
origin = Vector((start.x, start.y, 0))
margin = float(config.get('margin', 1.12))
lens = float(config.get('lens_mm', 50))
side = config.get('street_side', -1)
if not math.isfinite(margin) or margin <= 1 or not math.isfinite(lens) or lens <= 0 or side not in (-1, 1):
    parser.error('Invalid margin, lens_mm or street_side')

closeups = []
for detail in config.get('closeups', []):
    name = detail['name']
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', name):
        parser.error('Closeup name must be a safe filename using letters, digits, _ or -')
    lo, hi = [finite_vector(v, 3, 'local_bounds') for v in detail['local_bounds']]
    if any(lo[i] >= hi[i] for i in range(3)):
        parser.error('local_bounds min must be less than max on every axis')
    view = detail.get('view', 'perspective')
    if view not in ('front', 'perspective'):
        parser.error('Closeup view must be front or perspective')
    points = [origin + u*p.x + inward*p.y + up*p.z for p in box_corners(lo, hi)]
    closeups.append((name, view, points))
filenames = ['01_front.png', '02_street.png', '03_top.png'] + [
    f'{i:02d}_{name}.png' for i, (name, _, _) in enumerate(closeups, 4)]

source_before = file_sha(source)
bpy.ops.wm.open_mainfile(filepath=str(source), use_scripts=False)
scene = bpy.context.scene
bpy.context.view_layer.update()
geometry_before = geometry_sha()
depsgraph = bpy.context.evaluated_depsgraph_get()


def select_targets(cfg):
    selected = scene_objects(scene, cfg.get('target_objects', []))
    for name in cfg.get('target_collections', []):
        collection = bpy.data.collections.get(name)
        if collection is None:
            raise ValueError('Target collection not found: ' + name)
        selected.update(o for o in collection.all_objects if o.name in scene.objects)
    return selected


def target_points(objects):
    result = []
    def append_geometry(obj, matrix):
        if obj.type not in ('MESH', 'CURVE', 'SURFACE', 'FONT', 'META'):
            return
        mesh = obj.to_mesh()
        try:
            if mesh:
                result.extend(matrix @ vertex.co for vertex in mesh.vertices)
        finally:
            obj.to_mesh_clear()
    for obj in sorted(objects, key=lambda o: o.name):
        evaluated = obj.evaluated_get(depsgraph)
        append_geometry(evaluated, evaluated.matrix_world)
    names = {o.name for o in objects}
    for instance in depsgraph.object_instances:
        if instance.is_instance and instance.parent and instance.parent.original.name in names:
            append_geometry(instance.object, instance.matrix_world)
    if not result:
        raise ValueError('Selected targets contain no evaluated mesh vertices')
    return result


targets = select_targets(config)
points = target_points(targets)

if args.engine:
    scene.render.engine = args.engine
scene.render.resolution_x, scene.render.resolution_y = args.resolution
scene.render.resolution_percentage = 100
scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
scene.render.use_border = False
scene.render.use_crop_to_border = False
scene.render.use_multiview = False
# Existing compositor/output nodes may write outside views/ or replace the camera image.
scene.render.use_compositing = False
scene.render.use_sequencer = False
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.image_settings.color_depth = '8'
if scene.render.engine == 'CYCLES':
    scene.cycles.samples = args.samples
    scene.cycles.device = args.device


def make_camera(name, sample_points, direction, projection, screen_up):
    center = sum(sample_points, Vector()) / len(sample_points)
    # Center framing on projected extrema, not the density of mesh vertices.
    outward = direction.normalized()
    right = screen_up.cross(outward).normalized()
    vertical = outward.cross(right).normalized()
    rotation = Matrix((right, vertical, outward)).transposed()
    local = [rotation.transposed() @ (p - center) for p in sample_points]
    mid = Vector(tuple((min(p[i] for p in local) + max(p[i] for p in local)) / 2 for i in range(3)))
    center += rotation @ mid
    local = [p - mid for p in local]
    camera_data = bpy.data.cameras.new('QA_' + name)
    camera_data.type = projection
    camera_data.lens = lens
    camera_data.sensor_fit = 'HORIZONTAL'
    camera_data.ortho_scale = 1.0
    camera_data.dof.use_dof = False
    camera = bpy.data.objects.new('QA_' + name, camera_data)
    scene.collection.objects.link(camera)
    frame = camera_data.view_frame(scene=scene)
    radius = max((p.length for p in local), default=1.0)
    if projection == 'ORTHO':
        half_x = max(abs(p.x) for p in frame)
        half_y = max(abs(p.y) for p in frame)
        camera_data.ortho_scale = max(max(abs(p.x)/half_x, abs(p.y)/half_y) for p in local) * margin
        distance = max(radius * 3, 1.0)
    else:
        tan_x = max(abs(p.x/p.z) for p in frame)
        tan_y = max(abs(p.y/p.z) for p in frame)
        distance = max(p.z + margin * max(abs(p.x)/tan_x, abs(p.y)/tan_y) for p in local)
        distance = max(distance, max(p.z for p in local) + max(radius * .01, .01))
    camera.location = center + outward * distance
    camera.rotation_euler = rotation.to_euler()
    camera_data.clip_start = max(min(radius * 0.00001, 0.01), 0.00001)
    camera_data.clip_end = max(distance + radius * 4, 100.0)
    scene.camera = camera
    bpy.context.view_layer.update()
    return camera


views = [('front', points, -inward, 'ORTHO', up),
         ('street', points, -inward + u * side * .55 + up * .25, 'PERSP', up),
         ('top', points, up, 'ORTHO', up.cross(u))]
for name, view, samples in closeups:
    direction = -inward if view == 'front' else -inward + u * side * .3 + up * .12
    views.append((name, samples, direction, 'ORTHO' if view == 'front' else 'PERSP', up))
face_manifest = []
if face_configs is not None:
    prepared = {}
    all_targets = set(targets)
    for index, face in enumerate(face_configs):
        fs = finite_vector(face['front_start_xy'], 2, 'front_start_xy')
        fe = finite_vector(face['front_end_xy'], 2, 'front_end_xy')
        segment = fe-fs
        if segment.length == 0 or face['inward_sign'] not in (-1, 1):
            raise ValueError('Invalid facade frame: ' + face['id'])
        fu = Vector((segment.x, segment.y, 0)).normalized()
        fi = Vector((-fu.y, fu.x, 0)) * face['inward_sign']
        fo = Vector((fs.x, fs.y, 0))
        cfg = {**root_config, **face}
        if 'target_objects' in face or 'target_collections' in face:
            cfg['target_objects'] = face.get('target_objects', [])
            cfg['target_collections'] = face.get('target_collections', [])
        objects = targets if index == 0 else select_targets(cfg)
        samples = points if index == 0 else target_points(objects)
        all_targets.update(objects)
        prepared[face['id']] = {'start': fs, 'end': fe, 'u': fu, 'inward': fi,
                                 'origin': fo, 'points': samples}
        face_manifest.append({'id': face['id'], 'target_objects': sorted(o.name for o in objects),
                              'front_start_xy': list(fs), 'front_end_xy': list(fe),
                              'inward_sign': face['inward_sign']})
        if index:
            name = 'front_' + face['id']
            filenames.append(f'{len(filenames)+1:02d}_{name}.png')
            views.append((name, samples, -fi, 'ORTHO', up))
    # Only the multi-face mode broadens top-view framing to the union.
    views[2] = ('top', target_points(all_targets), up, 'ORTHO', up.cross(u))
    targets = all_targets
    corners = root_config.get('corners')
    if corners is None:
        tolerance = float(root_config.get('corner_tolerance', 1e-4))
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError('Invalid corner_tolerance')
        corners = []
        for left, right in itertools.combinations(prepared, 2):
            lf, rf = prepared[left], prepared[right]
            adjacent = min((x-y).length for x in (lf['start'], lf['end']) for y in (rf['start'], rf['end'])) <= tolerance
            if adjacent and abs(lf['u'].dot(rf['u'])) < .999999:
                corners.append({'id': left + '_' + right, 'facades': [left, right]})
    if len(face_configs) > 1 and not corners:
        raise ValueError('No unambiguous corner found; specify corners with two nonparallel facade ids')
    for corner in corners:
        corner_id = corner['id']
        if not isinstance(corner_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', corner_id):
            raise ValueError('Invalid corner id')
        pair = corner['facades']
        if len(pair) != 2 or pair[0] == pair[1] or any(i not in prepared for i in pair):
            raise ValueError('Each corner needs two distinct known facade ids')
        lf, rf = (prepared[i] for i in pair)
        direction = -lf['inward']-rf['inward']
        if direction.length < 1e-6 or abs(lf['u'].dot(rf['u'])) > .999999:
            raise ValueError('Parallel facades do not define a corner view')
        direction.normalize()
        name = 'corner_' + corner_id
        filenames.append(f'{len(filenames)+1:02d}_{name}.png')
        views.append((name, lf['points']+rf['points'], direction+up*.25, 'PERSP', up))
    for face in face_configs[1:]:
        frame = prepared[face['id']]
        for detail in face.get('closeups', []):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', detail['name']):
                raise ValueError('Invalid closeup name')
            lo, hi = [finite_vector(v, 3, 'local_bounds') for v in detail['local_bounds']]
            if any(lo[i] >= hi[i] for i in range(3)):
                raise ValueError('Invalid closeup bounds')
            kind = detail.get('view', 'perspective')
            if kind not in ('front', 'perspective'):
                raise ValueError('Unknown closeup view')
            samples = [frame['origin']+frame['u']*p.x+frame['inward']*p.y+up*p.z for p in box_corners(lo, hi)]
            name = face['id'] + '_' + detail['name']
            direction = -frame['inward'] if kind == 'front' else -frame['inward']+frame['u']*side*.3+up*.12
            filenames.append(f'{len(filenames)+1:02d}_{name}.png')
            views.append((name, samples, direction, 'ORTHO' if kind == 'front' else 'PERSP', up))
outputs = [out_dir / name for name in filenames + ['render_manifest.json']]
if any(p.exists() or p in (source, args.config.resolve()) for p in outputs):
    parser.error('Use a fresh views directory; output files must not overwrite inputs')
out_dir.mkdir(parents=True, exist_ok=True)
records = []
for filename, (name, samples, direction, projection, screen_up) in zip(filenames, views):
    camera = make_camera(name, samples, direction, projection, screen_up)
    scene.render.filepath = str(out_dir / filename)
    print('RENDER_VIEW', filename, flush=True)
    bpy.ops.render.render(write_still=True)
    records.append({'file': filename, 'camera': camera.name, 'projection': projection,
        'location': list(camera.location), 'rotation_euler': list(camera.rotation_euler),
        'lens_mm': camera.data.lens, 'ortho_scale': camera.data.ortho_scale,
        'framing_world_points_min': [min(p[i] for p in samples) for i in range(3)],
        'framing_world_points_max': [max(p[i] for p in samples) for i in range(3)]})

geometry_after = geometry_sha()
source_after = file_sha(source)
manifest = {'blend': str(source), 'target_objects': sorted(o.name for o in targets),
    'config': config, 'engine': scene.render.engine, 'resolution': args.resolution,
    'cycles_samples': args.samples if scene.render.engine == 'CYCLES' else None,
    'views': records, 'geometry_sha256_before': geometry_before,
    'geometry_sha256_after': geometry_after, 'blend_sha256_before': source_before,
    'blend_sha256_after': source_after, 'geometry_unchanged': geometry_before == geometry_after,
    'input_file_unchanged': source_before == source_after,
    'note': 'Camera/render-only run. Framing and read-only checks are not facade QA approval.'}
if face_configs is not None:
    manifest['config'] = root_config
    manifest['facades'] = face_manifest
    manifest['corners'] = corners
(out_dir / 'render_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
if not manifest['geometry_unchanged'] or not manifest['input_file_unchanged']:
    raise RuntimeError('Read-only verification failed; see render_manifest.json')
print('RENDER_VIEWS_COMPLETE', len(records), flush=True)
