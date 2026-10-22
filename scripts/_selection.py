"""Shared exact-name selection for source records and Blender parent hierarchies.

Internal helper; the command-line entry points are inspect_base, verify_integrity
and render_views. No geometry or files are changed by these functions.
"""


def source_names(data, names):
    """Expand a source mesh or parent name, retaining request/record order."""
    mesh_names = [node['name'] for node in data['nodes']]
    hierarchy = data.get('hierarchy', [])
    known = set(mesh_names)
    children = {}
    for item in hierarchy:
        known.add(item['name'])
        if item.get('parent') is not None:
            children.setdefault(item['parent'], []).append(item['name'])
    result = []
    for name in names:
        if name not in known:
            hint = ('; old JSON without hierarchy must be re-imported for group selection'
                    if not hierarchy else '')
            raise ValueError('Unknown source object/group: ' + name + hint)
        descendants, pending = set(), [name]
        while pending:
            current = pending.pop()
            if current in descendants:
                continue
            descendants.add(current)
            pending.extend(children.get(current, []))
        matches = [n for n in mesh_names if n in descendants]
        if not matches:
            raise ValueError('Source group contains no meshes: ' + name)
        result.extend(n for n in matches if n not in result)
    return result


def scene_objects(scene, names, meshes_only=False):
    """Resolve exact names in the active scene, including recursive descendants.

    Non-mesh generators stay selectable for existing collection/GN support.
    Plain parent empties are traversal nodes, not render or bounds geometry.
    """
    result = set()
    for name in names:
        root = scene.objects.get(name)
        if root is None:
            raise ValueError('Object/group not found in scene: ' + name)
        pending, seen, matches = [root], set(), set()
        while pending:
            obj = pending.pop()
            if obj.name in seen:
                continue
            seen.add(obj.name)
            pending.extend(obj.children)
            if obj.name not in scene.objects:
                continue
            if (obj.type == 'MESH' or (not meshes_only and (
                    obj.type in ('CURVE', 'SURFACE', 'FONT', 'META') or
                    obj.instance_type == 'COLLECTION' or
                    any(m.type == 'NODES' for m in obj.modifiers)))):
                matches.add(obj)
        if not matches:
            raise ValueError('Object/group contains no selectable geometry: ' + name)
        result.update(matches)
    return result


def restore_hierarchy(data, scene):
    """Restore identity parents: JSON mesh vertices already use world coordinates."""
    import bpy
    hierarchy = data.get('hierarchy', [])
    for item in hierarchy:
        if scene.objects.get(item['name']) is None:
            obj = bpy.data.objects.new(item['name'], None)
            scene.collection.objects.link(obj)
    for item in hierarchy:
        parent = item.get('parent')
        if parent is not None:
            obj = scene.objects[item['name']]
            world = obj.matrix_world.copy()
            obj.parent = scene.objects[parent]
            obj.matrix_world = world
    bpy.context.view_layer.update()
