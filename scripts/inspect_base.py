"""用途：按解析后的原始网格生成底模副本、对象清单和阶段 1 几何事实卡。
输入：--geometry 为 import_base.py 输出；--output-dir 为新输出目录；
      可选 --facts-config JSON，用于提供无法自动判定的事实与依据。
输出：base_inventory.json、source_import.blend、facts.md。
示例：blender --background --python inspect_base.py --
      --geometry temp/source_geometry.json --output-dir temp/inspection
      --facts-config facts-config.json
以上示例写为一行。命令行路径相对当前工作目录；不回写输入文件。

facts-config 示例（数字仅为虚构说明，坐标使用转换后的世界坐标）：
{
  "target_objects": ["Block_A"],
  "facts": {
    "units": {"value": "1 coordinate unit = 1 m", "evidence": "人工核对尺寸"},
    "facade_orientation": {"value": "朝南", "evidence": "总平面指北针"},
    "endpoints": {"value": [[0, 0, 0], [12, 0, 0]], "evidence": "核对过的源顶点"},
    "floor_count": {"value": 2, "evidence": "几何核对截图"},
    "floor_levels": {"value": [0, 3, 6], "evidence": "核对过的楼板标高"},
    "parapet_top": {"value": 7, "evidence": "核对过的女儿墙顶"},
    "bays": {"value": "0–4、4–12", "evidence": "逐开间核对图"},
    "turns": {"value": "无", "evidence": "平面核对图"}
  }
}
其他可用事实键：axes、target_scope、total_length。所有人工事实都需 value
与 evidence；未提供、空值或无依据时标缺失，配置内容不代表脚本自动核实。
自动事实仅包括坐标映射、指定对象的世界包围盒、端点平面距离和源校验值。
坐标频数不是楼层或开间识别；单位、层数、标高、女儿墙、开间、转折不猜测。

--target-object 可重复使用，按 JSON 中的精确网格或父级分组名递归选择目标，例如
--target-object tower；提供时替代配置的 target_objects，不筛掉底模中的其他对象。
依赖新版 import_base.py 的 hierarchy；旧 JSON 保持网格名选择，组名需重新导入。
source_import.blend 保留选择所需的父子关系；坐标已烘入网格，父级为恒等变换。
--bays-undivided 明确声明目标没有竖向开间划分；配置也可写 bays_divided:false。
未声明且无开间证据时仍标缺失，不能把缺信息猜成未划分。
多立面 facts-config 可增加 facades 列表，例如：
{"facades":[{"id":"front","front_start_xy":[0,0],"front_end_xy":[12,0],
 "inward_sign":1,"evidence":"核对过的底模边线","bays_divided":false,
 "facts":{"floor_count":{"value":2,"evidence":"核对截图"},
 "floor_levels":{"value":[0,3,6],"evidence":"底模楼板标高"},
 "adjacent_blocks":{"value":"起点接低层体块，z=0–3m","evidence":"平面核对图"}}}]}
每面逐项列朝向、端点、长度、层数、标高、女儿墙、开间与交接；
未另填朝向时只计算模型坐标中的外法线，不猜地理东南西北。
各面可继承公共 facts 中的 floor_count/floor_levels/parapet_top，继承会注明。
末尾总是追加项目信息待补占位，不从几何猜建筑类型、各层功能或特殊开间用途。
"""
import argparse, bpy, json, collections, sys, math
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _selection import source_names, restore_hierarchy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--geometry', required=True, type=Path)
parser.add_argument('--output-dir', required=True, type=Path)
parser.add_argument('--facts-config', type=Path)
parser.add_argument('--target-object', action='append', dest='target_objects')
parser.add_argument('--bays-undivided', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
OUT = args.output_dir.resolve()
outputs = [OUT / name for name in ('base_inventory.json', 'source_import.blend', 'facts.md')]
inputs = [args.geometry.resolve()] + ([args.facts_config.resolve()] if args.facts_config else [])
if any(p in inputs or p.exists() for p in outputs):
    parser.error('Use an output directory without existing output files; inputs must stay read-only')
config = json.loads(args.facts_config.read_text(encoding='utf-8-sig')) if args.facts_config else {}
data = json.loads(args.geometry.read_text(encoding='utf-8-sig'))
target_names = args.target_objects if args.target_objects is not None else config.get('target_objects', [])
try:
    target_names = source_names(data, target_names)
except ValueError as error:
    parser.error(str(error))
OUT.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
mats={}
for d in data['materials']:
    m=bpy.data.materials.new(d['name']);m.diffuse_color=(*d['color'][:3],1);mats[d['id']]=m
for d in data['nodes']:
    me=bpy.data.meshes.new(d['name']);me.from_pydata(d['verts'],[],d['faces']);me.update()
    o=bpy.data.objects.new(d['name'],me);bpy.context.collection.objects.link(o);o['FBX_element_id']=str(d['id'])
    for i in d['mats']:me.materials.append(mats[i])
    for p,i in zip(me.polygons,d['face_mats']):p.material_index=i
restore_hierarchy(data, bpy.context.scene)
bpy.context.scene['SourceSHA256']=data['sha256']
objs=[]
for o in bpy.context.scene.objects:
    if o.type!='MESH': continue
    vs=[o.matrix_world@v.co for v in o.data.vertices]
    lo=[min(v[i] for v in vs) for i in range(3)]
    hi=[max(v[i] for v in vs) for i in range(3)]
    coords={a:collections.Counter(round(v[i],3) for v in vs).most_common(25) for i,a in enumerate('xyz')}
    objs.append(dict(name=o.name,verts=len(vs),polys=len(o.data.polygons),min=lo,max=hi,coords=coords,materials=[m.name if m else None for m in o.data.materials]))
with (OUT / 'base_inventory.json').open('w',encoding='utf-8') as f:json.dump(objs,f,ensure_ascii=False,indent=2)
print('BASE_INVENTORY',json.dumps([{k:v for k,v in d.items() if k!='coords'} for d in objs],ensure_ascii=False))
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'source_import.blend'))

supplied = dict(config.get('facts', {}))
if args.bays_undivided or config.get('bays_divided') is False:
    supplied['bays'] = {'value': '未划分', 'evidence': '调用者明确声明底模没有竖向开间划分；未由脚本猜测'}
missing = '缺失／待人工确认'

def fact(key, facts=None):
    item = (supplied if facts is None else facts).get(key, {})
    if not isinstance(item, dict) or item.get('value') in (None, '', []) or not item.get('evidence'):
        return None
    return (item['value'], '配置提供，待人工核实；依据：' + str(item['evidence']))

auto = {'source_checksum': (data['sha256'], '解析记录中的源文件 SHA256；实时比对由 verify_integrity.py 执行')}
if data.get('coordinate_transform'):
    auto['axes'] = (data['coordinate_transform'], '导入时实际执行的坐标映射；不代表建筑朝向或单位解释')
if target_names:
    selected = [o for o in objs if o['name'] in target_names]
    auto['target_scope'] = ({'objects': target_names,
        'world_min': [min(o['min'][i] for o in selected) for i in range(3)],
        'world_max': [max(o['max'][i] for o in selected) for i in range(3)]},
        '配置指定目标对象；由导入网格测量世界包围盒，不代表保护边界已获人工确认')
endpoints = fact('endpoints')
if endpoints:
    points = endpoints[0]
    if (not isinstance(points, list) or len(points) != 2
            or any(not isinstance(p, list) or len(p) not in (2, 3)
                   or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in p) for p in points)):
        parser.error('facts.endpoints.value must contain two finite XY or XYZ points')
    auto['total_length'] = (math.hypot(points[1][0]-points[0][0], points[1][1]-points[0][1]),
        '由配置端点计算的平面直线距离，单位同坐标；折线总长需另行提供 total_length 及依据')

fields = [('units', '单位'), ('axes', '轴向'), ('target_scope', '目标建筑范围'),
          ('facade_orientation', '立面朝向'), ('endpoints', '端点坐标'),
          ('total_length', '总长'), ('floor_count', '层数'), ('floor_levels', '各层标高'),
          ('parapet_top', '女儿墙顶'), ('bays', '开间划分'), ('turns', '转折'),
          ('source_checksum', '源文件校验值')]
lines = ['# 几何事实卡', '', '本卡不自动通过阶段 1 关卡；层数、开间仍需与核对截图人工比对。',
         '所有未注明单位的测量值均为转换后的坐标单位，不默认当作米。', '']
for key, label in fields:
    value = (auto.get(key) or fact(key)) if key in ('axes', 'source_checksum') else fact(key) or auto.get(key)
    lines.extend(['## ' + label, ''])
    if value is None:
        lines.extend([missing + '。原脚本没有足够信息可靠生成本字段。', ''])
    else:
        rendered = value[0] if isinstance(value[0], str) else json.dumps(value[0], ensure_ascii=False)
        lines.extend([rendered, '', '依据／状态：' + value[1], ''])

def append_fact(label, value, level='###'):
    lines.extend([level + ' ' + label, ''])
    if value is None:
        lines.extend([missing + '。不从缺失信息推断建筑事实。', ''])
    else:
        rendered = value[0] if isinstance(value[0], str) else json.dumps(value[0], ensure_ascii=False)
        lines.extend([rendered, '', '依据／状态：' + value[1], ''])

append_fact('与相邻体块的交接', fact('adjacent_blocks'), '##')
facades = config.get('facades', [])
face_ids = [face['id'] for face in facades]
if len(set(face_ids)) != len(face_ids) or any(not isinstance(i, str) or not i.strip() for i in face_ids):
    parser.error('facades require unique nonempty string ids')
if facades:
    lines.extend(['## 各立面事实', '', '立面范围由配置指定；几何不能确定的语义字段仍需人工确认。', ''])
for face in facades:
    lines.extend(['### 立面 ' + face['id'], ''])
    ff = dict(face.get('facts', {}))
    for key in ('floor_count', 'floor_levels', 'parapet_top'):
        if key not in ff and fact(key):
            ff[key] = dict(supplied[key])
            ff[key]['evidence'] = '继承建筑公共事实；' + str(ff[key]['evidence'])
    if face.get('bays_divided') is False or (face.get('bays_divided') is None and (args.bays_undivided or config.get('bays_divided') is False)):
        ff['bays'] = {'value': '未划分', 'evidence': '调用者明确声明该立面底模没有竖向开间划分'}
    derived = {}
    if all(key in face for key in ('front_start_xy', 'front_end_xy', 'inward_sign')):
        s, e = face['front_start_xy'], face['front_end_xy']
        if any(not isinstance(p, list) or len(p) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in p) for p in (s, e)):
            parser.error('Facade endpoints must be finite XY pairs')
        length = math.hypot(e[0]-s[0], e[1]-s[1])
        sign = face['inward_sign']
        if length == 0 or sign not in (-1, 1):
            parser.error('Distinct facade endpoints and inward_sign=±1 required')
        if face.get('evidence'):
            why = '配置边线及内侧方向；' + str(face['evidence'])
            derived['endpoints'] = ([s, e], why)
            derived['total_length'] = (length, '由配置边线计算的平面长度，单位同坐标；' + why)
            derived['facade_orientation'] = ({'outward_xy': [sign*(e[1]-s[1])/length, -sign*(e[0]-s[0])/length]},
                '模型坐标外法线，不等于地理方位；' + why)
    for key, label in [('facade_orientation','朝向'),('endpoints','端点坐标'),('total_length','长度'),
                       ('floor_count','层数'),('floor_levels','各层标高'),('parapet_top','女儿墙顶'),
                       ('bays','开间划分'),('adjacent_blocks','与相邻体块的交接')]:
        append_fact(label, fact(key, ff) or derived.get(key), '####')
lines.extend(['## 项目信息（待补）', '', '- 建筑类型：待补', '- 各层功能：待补',
              '- 特殊开间的位置与用途：待补', '', '以上仅为占位，需向用户确认后填写。', ''])
(OUT / 'facts.md').write_text('\n'.join(lines), encoding='utf-8')

