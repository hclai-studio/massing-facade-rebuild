"""用途：自动识别二进制／ASCII FBX，统一输出底模网格 JSON，不做建模。
输入：--source FBX；--output JSON；--axes 为输出 XYZ 对应的源轴；
      --scale 为坐标倍率（默认 1，不按 FBX 单位标签自动缩放）。
输出：source_geometry.json，含源路径、SHA256、材质、网格及实际坐标映射。
示例：blender --background --python import_base.py -- --source base.fbx
      --output temp/source_geometry.json --axes x,-z,y --scale 1
以上示例在命令行中写为一行。路径相对当前工作目录。
二进制通过 Blender 自带 FBX 导入器读取，再将对象世界变换烘入输出坐标；
ASCII 保留现有解析器及非恒等变换拒绝逻辑。两条路径均使用源文件坐标轴，
只执行 --axes 和 --scale 指定的映射；二进制导入器的单位换算会被显式抵消，
不会因为 FBX 单位标签不同而悄悄缩放。请按核实后的单位显式选择 --scale。
两种格式都输出 source、sha256、materials、nodes、coordinate_transform；
每节点包含 name、id、geometry_id、verts、faces、mats、face_mats。
有父级关系时额外输出 hierarchy（name、parent、type），供下游按分组名递归选择。
网格坐标仍已转换到世界空间；层级仅用于选择，重建时父级为恒等变换。
旧 JSON 若不含层级，需重新运行本脚本才能按源分组名选择。
二进制 id/geometry_id 是本次导入内的稳定编号，不冒充原 FBX 元素编号；
材质仍只保留漫反射颜色，不在 JSON 中保存贴图、动画、UV 或材质节点树。
原生导入器可能清理无效面；二进制结果以原生导入后的静态网格为准，
不保证与 ASCII 解析器保留的逐索引拓扑一致，也不保证保留畸形源面的数量。
二进制支持依赖 Blender 自带 io_scene_fbx；未安装或禁用时明确失败。
"""
import argparse, json, hashlib, re, sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--axes', required=True, help='Output XYZ source axes, e.g. x,-z,y')
parser.add_argument('--scale', type=float, default=1.0)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
axes = args.axes.lower().split(',')
if (len(axes) != 3 or any(a not in ('x', 'y', 'z', '-x', '-y', '-z') for a in axes)
        or len({a[-1] for a in axes}) != 3):
    parser.error('--axes must be a signed permutation of x,y,z')
if not 0 < args.scale < float('inf'):
    parser.error('--scale must be positive and finite')
src = str(args.source.resolve(strict=True))
if args.output.resolve() == Path(src):
    parser.error('Output must not overwrite the source')
out={'source':src,'sha256':hashlib.sha256(open(src,'rb').read()).hexdigest(),'materials':[],'nodes':[]}
def mapped(v):
    return tuple(v['xyz'.index(a[-1])] * (-1 if a.startswith('-') else 1) * args.scale for a in axes)


def read_binary():
    import bpy
    from io_scene_fbx import import_fbx, parse_fbx
    from io_scene_fbx.fbx_utils import units_blender_to_fbx_factor

    root, _version = parse_fbx.parse(src)
    settings = import_fbx.elem_find_first(root, b'GlobalSettings')
    props = import_fbx.elem_find_first(settings, b'Properties70') if settings else None
    if props is None:
        raise ValueError('Binary FBX has no GlobalSettings/Properties70')
    unit_scale = import_fbx.elem_props_get_number(props, b'UnitScaleFactor', 1.0)
    if not 0 < unit_scale < float('inf'):
        raise ValueError('Invalid binary FBX UnitScaleFactor')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    native_factor = unit_scale / units_blender_to_fbx_factor(bpy.context.scene)
    # Identity axis import lets both branches apply the same explicit signed mapping.
    result = bpy.ops.import_scene.fbx(filepath=src, use_manual_orientation=True,
        axis_forward='Y', axis_up='Z', global_scale=1.0, bake_space_transform=False,
        use_anim=False, use_image_search=False, use_custom_normals=False)
    if 'FINISHED' not in result:
        raise RuntimeError('Blender binary FBX import did not finish')
    bpy.context.view_layer.update()
    objects = sorted((o for o in bpy.context.scene.objects if o.type == 'MESH'), key=lambda o: o.name)
    retained = set(objects)
    for obj in objects:
        parent = obj.parent
        while parent is not None:
            retained.add(parent)
            parent = parent.parent
    if any(o.parent is not None for o in retained):
        out['hierarchy'] = [{'name': o.name, 'parent': o.parent.name if o.parent else None,
                             'type': o.type} for o in sorted(retained, key=lambda o: o.name)]
    materials = {}
    geometry_ids = {}
    for obj_id, obj in enumerate(objects, 1):
        mesh = obj.data
        geometry_id = geometry_ids.setdefault(mesh.as_pointer(), len(geometry_ids) + 1)
        slots = []
        for slot in obj.material_slots:
            material = slot.material
            key = material.as_pointer() if material else None
            if key not in materials:
                mid = len(materials) + 1
                materials[key] = mid
                color = list(material.diffuse_color) if material else [0.65, 0.65, 0.65, 1.0]
                out['materials'].append(dict(name=material.name if material else 'Unassigned', id=mid, color=color))
            slots.append(materials[key])
        vertices = [mapped((obj.matrix_world @ v.co) / native_factor) for v in mesh.vertices]
        out['nodes'].append(dict(name=obj.name, id=obj_id, geometry_id=geometry_id,
            verts=vertices, faces=[list(p.vertices) for p in mesh.polygons], mats=slots,
            face_mats=[p.material_index for p in mesh.polygons]))


with open(src, 'rb') as stream:
    binary = stream.read(23) == b'Kaydara FBX Binary  \x00\x1a\x00'
if binary:
    read_binary()
else:
    txt=open(src,encoding='utf8').read();objs={}
    for m in re.finditer(r'^\t(Geometry|Model|Material): (\d+), "([^"]*)"[^\n]*\{',txt,re.M):
     start=m.end();depth=1;i=start
     while depth:
      if txt[i]=='{':depth+=1
      elif txt[i]=='}':depth-=1
      i+=1
     objs[int(m[2])]=(m[1],m[3].split('::')[-1],txt[start:i-1])
    links={}
    for a,b in re.findall(r'C: "OO",\s*(\d+),\s*(\d+)',txt):links.setdefault(int(b),[]).append(int(a))
    def array(t,key,cast=float):
     m=re.search(r'\b'+key+r': \*(\d+)\s*\{\s*a:\s*([^}]+)',t)
     if not m:return []
     a=[cast(v.strip()) for v in m[2].split(',') if v.strip()]
     assert len(a)==int(m[1]),key
     return a
    for oid,(typ,name,t) in objs.items():
     if typ=='Material':
      c=re.search(r'P: "DiffuseColor"[^\n]*',t);col=[float(x) for x in c[0].split(',')[-3:]] if c else [.65]*3
      out['materials'].append(dict(name=name,id=oid,color=col+[1]))
     if typ!='Model':continue
     assert not re.search(r'P: "(?:Lcl Translation|Lcl Rotation|Lcl Scaling|GeometricTranslation|GeometricRotation|GeometricScaling)"',t),'Nonidentity source transform: '+name
     children=[(j,objs[j]) for j in links.get(oid,[]) if j in objs]
     mats=[j for j,(kind,n,g) in children if kind=='Material']
     for gid,(kind,n,g) in children:
      if kind!='Geometry':continue
      v=array(g,'Vertices');verts=[tuple(v[i+'xyz'.index(a[-1])] * (-1 if a.startswith('-') else 1) * args.scale for a in axes) for i in range(0,len(v),3)]
      faces=[];face=[]
      for k in array(g,'PolygonVertexIndex',int):
       face.append(k if k>=0 else -k-1)
       if k<0:
        assert len(face)>=3
        faces.append(face);face=[]
      fm=array(g,'Materials',int)
      if len(fm)==1:fm*=len(faces)
      out['nodes'].append(dict(name=name,id=oid,geometry_id=gid,verts=verts,faces=faces,mats=mats,face_mats=fm))
assert len(out['nodes'])>0
if not binary:
    model_ids = {i for i, (kind, _, _) in objs.items() if kind == 'Model'}
    parents = {}
    for parent, children in links.items():
        for child in children:
            if child in model_ids and parent in model_ids:
                if child in parents and parents[child] != parent:
                    raise ValueError('Multiple parents for FBX model: ' + objs[child][1])
                parents[child] = parent
    retained = {n['id'] for n in out['nodes']}
    for child in list(retained):
        visited = set()
        while child in parents:
            if child in visited:
                raise ValueError('Cyclic FBX parent hierarchy')
            visited.add(child)
            child = parents[child]
            retained.add(child)
    names = [objs[i][1] for i in retained]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate FBX model names; exact-name selection is ambiguous')
    mesh_ids = {n['id'] for n in out['nodes']}
    if any(i in parents for i in retained):
        out['hierarchy'] = [{'name': objs[i][1],
                             'parent': objs[parents[i]][1] if i in parents else None,
                             'type': 'MESH' if i in mesh_ids else 'EMPTY'}
                            for i in sorted(retained)]
out['coordinate_transform'] = {'output_xyz_from_source': axes, 'scale': args.scale}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
print('FBX format:', 'binary' if binary else 'ASCII')
print('Converted source mesh instances:', len(out['nodes']))
