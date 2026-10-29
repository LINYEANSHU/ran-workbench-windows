"""Preserve official glTF triangles in small, recentered native-coordinate GLB tiles.

Source glTF scenes are Y-up. The declared survey axes after that conversion are
HK1980 easting/northing and HKPD height; no ellipsoidal-height conversion is made.
"""
from __future__ import annotations

import base64
import itertools
import json
import math
from pathlib import Path
import struct
from urllib.parse import unquote

import numpy as np

from .city_data import StorageBudget, atomic_json, directory_bytes, safe_member_path

_DTYPES = {5120: '<i1', 5121: '<u1', 5122: '<i2', 5123: '<u2', 5125: '<u4', 5126: '<f4'}
_WIDTHS = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
_TO_NATIVE = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=float)
_COLORS = {'B': [169, 183, 178], 'I': [120, 139, 136], 'T': [59, 79, 67],
           'V': [57, 90, 68], 'W': [39, 99, 117], 'G': [129, 151, 146]}


def local_resource(parent, uri):
    if not isinstance(uri, str) or '?' in uri or '#' in uri:
        raise ValueError('Invalid local glTF resource URI')
    parts = safe_member_path(unquote(uri)).parts
    path = (Path(parent) / Path(*parts)).resolve()
    if not path.is_relative_to(Path(parent).resolve()):
        raise ValueError('glTF resource path escaped its asset directory')
    if not path.is_file():
        raise FileNotFoundError(f'Missing glTF resource: {path}')
    return path


def read_glb(path):
    body = Path(path).read_bytes()
    if len(body) < 20 or body[:4] != b'glTF':
        raise ValueError('Invalid GLB header')
    _, version, length = struct.unpack_from('<4sII', body)
    if version != 2 or length != len(body):
        raise ValueError('Invalid GLB version or length')
    at, document, binary = 12, None, b''
    while at < len(body):
        size, kind = struct.unpack_from('<I4s', body, at)
        chunk = body[at + 8:at + 8 + size]
        if len(chunk) != size:
            raise ValueError('Truncated GLB chunk')
        if kind == b'JSON':
            document = json.loads(chunk)
        elif kind == b'BIN\0':
            binary = chunk
        at += 8 + size
    if document is None:
        raise ValueError('GLB is missing its JSON chunk')
    return document, binary


class Gltf:
    def __init__(self, path):
        self.path = Path(path)
        self.document = json.loads(self.path.read_text('utf-8-sig'))
        if self.document.get('asset', {}).get('version') != '2.0':
            raise ValueError('Only glTF 2.0 source geometry is supported')
        forbidden = {'KHR_draco_mesh_compression', 'EXT_meshopt_compression'}
        if forbidden.intersection(self.document.get('extensionsRequired', [])):
            raise ValueError('Compressed source geometry must be decoded explicitly, never skipped')
        self.buffers = []
        for buffer in self.document.get('buffers', []):
            uri = buffer.get('uri', '')
            if uri.startswith('data:'):
                if ';base64,' not in uri:
                    raise ValueError('Unsupported embedded glTF buffer')
                body = base64.b64decode(uri.split(';base64,', 1)[1], validate=True)
            else:
                body = local_resource(self.path.parent, uri).read_bytes()
            if len(body) < buffer['byteLength']:
                raise ValueError('Truncated glTF buffer')
            self.buffers.append(body)
        # A complete original model includes every externally referenced texture.
        for image in self.document.get('images', []):
            if 'uri' in image and not image['uri'].startswith('data:'):
                local_resource(self.path.parent, image['uri'])

    def view(self, index, offset, count, component, width, stride=None):
        view = self.document['bufferViews'][index]
        dtype = np.dtype(_DTYPES[component])
        item_bytes = dtype.itemsize * width
        step = int(stride or item_bytes)
        end = offset + (count - 1) * step + item_bytes if count else offset
        if count < 0 or offset < 0 or step < item_bytes or end > view['byteLength']:
            raise ValueError('glTF accessor exceeds its bufferView')
        body = self.buffers[view['buffer']]
        start = view.get('byteOffset', 0) + offset
        if start < 0 or start + max(0, end - offset) > len(body):
            raise ValueError('glTF bufferView exceeds its buffer')
        return np.ndarray((count, width), dtype=dtype, buffer=body, offset=start,
                          strides=(step, dtype.itemsize)).copy()

    def accessor(self, index):
        accessor = self.document['accessors'][index]
        component, width = accessor['componentType'], _WIDTHS[accessor['type']]
        count = accessor['count']
        if 'bufferView' in accessor:
            view = self.document['bufferViews'][accessor['bufferView']]
            data = self.view(accessor['bufferView'], accessor.get('byteOffset', 0), count,
                             component, width, view.get('byteStride'))
        else:
            data = np.zeros((count, width), dtype=_DTYPES[component])
        if 'sparse' in accessor:
            sparse = accessor['sparse']
            indices = sparse['indices']
            indices = self.view(indices['bufferView'], indices.get('byteOffset', 0),
                                sparse['count'], indices['componentType'], 1).reshape(-1)
            if len(indices) and (indices.max() >= count or len(np.unique(indices)) != len(indices)):
                raise ValueError('Invalid sparse accessor indices')
            values = sparse['values']
            data[indices] = self.view(values['bufferView'], values.get('byteOffset', 0),
                                      sparse['count'], component, width)
        if accessor.get('normalized') and component != 5126:
            limits = np.iinfo(data.dtype)
            data = data.astype(float) / limits.max
            if limits.min < 0:
                data = np.maximum(data, -1)
        return data


def _node_matrix(node):
    if 'matrix' in node:
        matrix = np.asarray(node['matrix'], dtype=float).reshape(4, 4, order='F')
    else:
        x, y, z, w = node.get('rotation', [0, 0, 0, 1])
        length = math.sqrt(x*x + y*y + z*z + w*w)
        if length == 0:
            raise ValueError('Zero glTF rotation quaternion')
        x, y, z, w = x/length, y/length, z/length, w/length
        rotation = np.array([[1-2*y*y-2*z*z, 2*x*y-2*z*w, 2*x*z+2*y*w],
                             [2*x*y+2*z*w, 1-2*x*x-2*z*z, 2*y*z-2*x*w],
                             [2*x*z-2*y*w, 2*y*z+2*x*w, 1-2*x*x-2*y*y]])
        matrix = np.eye(4)
        matrix[:3, :3] = rotation @ np.diag(node.get('scale', [1, 1, 1]))
        matrix[:3, 3] = node.get('translation', [0, 0, 0])
    if not np.isfinite(matrix).all() or not np.allclose(matrix[3], [0, 0, 0, 1]):
        raise ValueError('Invalid affine glTF node transform')
    return matrix


def read_meshes(path):
    source = Gltf(path)
    document = source.document
    nodes = document.get('nodes', [])
    roots = document['scenes'][document.get('scene', 0)]['nodes']

    def visit(index, parent, ancestors):
        if index in ancestors:
            raise ValueError('glTF node cycle')
        node = nodes[index]
        matrix = parent @ _node_matrix(node)
        native = _TO_NATIVE @ matrix
        linear = native[:3, :3]
        if 'mesh' in node:
            for primitive in document['meshes'][node['mesh']]['primitives']:
                if primitive.get('extensions', {}).get('KHR_draco_mesh_compression'):
                    raise ValueError('Draco source mesh is unsupported')
                mode = primitive.get('mode', 4)
                positions = source.accessor(primitive['attributes']['POSITION']).astype(np.float64)
                vertices = positions @ linear.T + native[:3, 3]
                indices = (source.accessor(primitive['indices']).reshape(-1) if 'indices' in primitive
                           else np.arange(len(vertices)))
                if not np.issubdtype(indices.dtype, np.integer) or (len(indices) and
                        (indices.min() < 0 or indices.max() >= len(vertices))):
                    raise ValueError('glTF mesh indices are invalid')
                if mode == 4:
                    if len(indices) % 3:
                        raise ValueError('Incomplete triangle indices')
                    faces = indices.reshape(-1, 3).astype(np.uint32)
                elif mode == 5:
                    faces = np.array([[indices[i], indices[i+1+(i%2)], indices[i+2-(i%2)]]
                                      for i in range(len(indices)-2)], dtype=np.uint32)
                elif mode == 6:
                    faces = np.array([[indices[0], indices[i], indices[i+1]]
                                      for i in range(1, len(indices)-1)], dtype=np.uint32)
                else:
                    raise ValueError(f'Non-triangle source mode {mode} must be handled explicitly')
                if np.linalg.det(linear) < 0:
                    faces = faces[:, [0, 2, 1]]
                if 'NORMAL' in primitive['attributes']:
                    normals = source.accessor(primitive['attributes']['NORMAL']).astype(float)
                    if normals.shape != vertices.shape:
                        raise ValueError('glTF normal count differs from positions')
                    normals = normals @ np.linalg.inv(linear)
                else:
                    normals = np.zeros_like(vertices)
                    a, b, c = (vertices[faces[:, column]] for column in (0, 1, 2))
                    cross = np.cross(b-a, c-a)
                    for column in (0, 1, 2):
                        np.add.at(normals, faces[:, column], cross)
                lengths = np.linalg.norm(normals, axis=1)
                normals = np.divide(normals, lengths[:, None], out=np.zeros_like(normals), where=lengths[:, None] > 0)
                if not np.isfinite(vertices).all() or not np.isfinite(normals).all():
                    raise ValueError('Nonfinite source geometry')
                yield vertices, normals, faces
        for child in node.get('children', []):
            yield from visit(child, matrix, ancestors | {index})

    for root in roots:
        yield from visit(root, np.eye(4), set())


def model_level(name):
    code = name[15:17] if len(name) == 18 else ''
    lod = int(code[0]) if code and code[0] in '123' else 0
    return lod, code


def highest_models(names):
    best = {}
    for name in names:
        lod, code = model_level(name)
        key = name[:15] if lod else name
        # Highest LOD first; within a level use newest revision. Texture
        # completeness breaks a remaining tie without changing geometry LOD.
        score = (lod, name[-1], code in {'2A', '3A'})
        if key not in best or score > best[key][0]:
            best[key] = (score, name)
    return sorted(value[1] for value in best.values())


def _bounds_box(bounds):
    low, high = np.asarray(bounds, dtype=float)
    center, extent = (low+high)/2, np.maximum((high-low)/2, 0.001)
    return [*center.tolist(), extent[0], 0, 0, 0, extent[1], 0, 0, 0, extent[2]]


def write_geometry_glb(parts, path, budget=None):
    parts = list(parts)
    if not parts:
        raise ValueError('No source triangle meshes to build')
    low = np.min([part[0].min(axis=0) for part in parts], axis=0)
    high = np.max([part[0].max(axis=0) for part in parts], axis=0)
    origin = np.floor((low+high)/2)
    vertices = np.concatenate([part[0] for part in parts])
    local = vertices - origin
    # glTF requires Y-up. The survey axes remain native (E,N,H) in the manifest.
    packed = (local[:, [0, 2, 1]] * [1, 1, -1]).astype('<f4')
    roundtrip = packed[:, [0, 2, 1]].astype(float) * [1, -1, 1] + origin
    error = float(np.abs(roundtrip - vertices).max(initial=0))
    if error > 0.001:
        raise ValueError(f'Recentered geometry error exceeds 1 mm: {error} m')
    normals = np.concatenate([part[1] for part in parts])
    normals = (normals[:, [0, 2, 1]] * [1, 1, -1]).astype('<f4')
    offsets = list(itertools.accumulate([0] + [len(part[0]) for part in parts]))
    faces = np.concatenate([part[2].astype(np.uint64) + offsets[index] for index, part in enumerate(parts)])
    if len(vertices) >= 2**32:
        raise ValueError('Tile exceeds uint32 geometry index capacity')
    faces = faces.astype('<u4')
    colors = np.concatenate([np.tile(np.asarray(part[3] if len(part) > 3 else _COLORS['B'], dtype='u1'),
                                    (len(part[0]), 1)) for part in parts])
    blobs, views = [], []
    at = 0
    for array, target in [(packed, 34962), (normals, 34962), (faces, 34963), (colors, 34962)]:
        data = array.tobytes()
        views.append({'buffer': 0, 'byteOffset': at, 'byteLength': len(data), 'target': target})
        padded = data + b'\0' * (-len(data) % 4)
        blobs.append(padded)
        at += len(padded)
    document = {'asset': {'version': '2.0', 'generator': 'RAN Workbench: original official triangles, no decimation'},
        'buffers': [{'byteLength': at}], 'bufferViews': views,
        'accessors': [
            {'bufferView': 0, 'componentType': 5126, 'count': len(packed), 'type': 'VEC3',
             'min': packed.min(axis=0).tolist(), 'max': packed.max(axis=0).tolist()},
            {'bufferView': 1, 'componentType': 5126, 'count': len(normals), 'type': 'VEC3'},
            {'bufferView': 2, 'componentType': 5125, 'count': faces.size, 'type': 'SCALAR'},
            {'bufferView': 3, 'componentType': 5121, 'count': len(colors), 'type': 'VEC3', 'normalized': True}],
        'materials': [{'pbrMetallicRoughness': {'baseColorFactor': [1, 1, 1, 1],
                       'metallicFactor': 0, 'roughnessFactor': 1}, 'doubleSided': True}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 1, 'COLOR_0': 3},
                                    'indices': 2, 'material': 0, 'mode': 4}]}],
        'nodes': [{'mesh': 0}], 'scenes': [{'nodes': [0]}], 'scene': 0}
    encoded = json.dumps(document, separators=(',', ':'), allow_nan=False).encode()
    encoded += b' ' * (-len(encoded) % 4)
    length = 12 + 8 + len(encoded) + 8 + at
    if budget:
        budget.contains(path)
        budget.ensure(length)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.glb.writing')
    try:
        with temporary.open('wb') as stream:
            stream.write(struct.pack('<4sII', b'glTF', 2, length))
            stream.write(struct.pack('<I4s', len(encoded), b'JSON'))
            stream.write(encoded)
            stream.write(struct.pack('<I4s', at, b'BIN\0'))
            for data in blobs:
                stream.write(data)
        os_replace = __import__('os').replace
        os_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {'origin_native_m': origin.tolist(), 'bounds_native_m': [low.tolist(), high.tolist()],
            'vertices': len(vertices), 'triangles': len(faces), 'max_position_error_m': error,
            'bytes': length, 'bounds_local_box': _bounds_box([low-origin, high-origin])}


def build_city(dataset_root, allow_partial=False, progress=None):
    dataset = Path(dataset_root).resolve()
    plan = json.loads((dataset / 'acquisition-plan.json').read_text('utf-8'))
    budget = StorageBudget(plan['budget_root'], plan['limit_bytes'])
    budget.contains(dataset)
    output = dataset / 'city'
    source_paths = {}
    available = []
    for tile in plan['tiles']:
        raw = dataset / 'raw' / tile['tile']
        report_path = raw / 'integrity.json'
        report = json.loads(report_path.read_text('utf-8')) if report_path.exists() else {}
        if not report.get('complete'):
            if allow_partial:
                continue
            raise ValueError(f'Official tile is not completely acquired: {tile["tile"]}')
        available.append(tile)
        for path in sorted(raw.rglob('*.gltf')):
            source_paths.setdefault(path.stem, (tile, path))
    chosen = highest_models(source_paths)
    by_tile = {tile['tile']: [] for tile in available}
    for name in chosen:
        tile, path = source_paths[name]
        by_tile[tile['tile']].append((name, path))
    assets, tiles, all_low, all_high = [], [], [], []
    for tile in available:
        parts, tile_assets = [], []
        for name, path in by_tile[tile['tile']]:
            meshes = list(read_meshes(path))
            if not meshes:
                raise ValueError(f'Official model has no triangle mesh: {name}')
            low = np.min([part[0].min(axis=0) for part in meshes], axis=0)
            high = np.max([part[0].max(axis=0) for part in meshes], axis=0)
            lod, level = model_level(name)
            record = {'id': name, 'tile': tile['tile'], 'bank': tile['bank'], 'kind': name[0],
                      'lod': lod or None, 'level_code': level,
                      'url': '/city-data/' + path.relative_to(dataset).as_posix(),
                      'bounds_native_m': [low.tolist(), high.tolist()],
                      'vertices': sum(len(part[0]) for part in meshes),
                      'triangles': sum(len(part[2]) for part in meshes)}
            tile_assets.append(record)
            color = _COLORS.get(name[0], _COLORS['G'])
            parts.extend((*part, color) for part in meshes)
        if not parts:
            continue
        report = write_geometry_glb(parts, output / 'tiles' / (tile['tile'] + '.glb'), budget)
        if report['triangles'] != sum(item['triangles'] for item in tile_assets):
            raise ValueError('Original and overview triangle counts differ')
        report.update(id=tile['tile'], bank=tile['bank'], models=len(tile_assets),
                      buildings=sum(item['kind'] == 'B' for item in tile_assets),
                      url='tiles/' + tile['tile'] + '.glb')
        tiles.append(report)
        assets.extend(tile_assets)
        all_low.append(report['bounds_native_m'][0])
        all_high.append(report['bounds_native_m'][1])
        if progress:
            progress({'stage': 'geometry', 'tile': tile['tile'], 'models': len(tile_assets),
                      'triangles': report['triangles'], 'error_m': report['max_position_error_m']})
        del parts
    if not tiles:
        raise ValueError('No completely acquired tiles to build')
    low, high = np.min(all_low, axis=0), np.max(all_high, axis=0)
    anchor = np.floor((low + high) / 2)
    # The rendering frame is explicitly a local projected Cartesian frame.
    # Placing that frame near Hong Kong in the renderer is not a datum conversion.
    from pyproj import Transformer
    longitude, latitude = Transformer.from_crs('EPSG:2326', 'EPSG:4326', always_xy=True).transform(anchor[0], anchor[1])
    manifest = {'schema': 'ran-city-1', 'name': '维港两岸 · 港府精细模型',
        'complete': len(available) == len(plan['tiles']), 'planned_tiles': len(plan['tiles']),
        'coordinate_system': {'horizontal': 'EPSG:2326 / HK1980', 'vertical': 'HKPD',
            'render_frame': 'native projected Cartesian coordinates; HKPD heights preserved; no ellipsoid-height conversion',
            'anchor_native_m': anchor.tolist(), 'render_anchor_deg': [longitude, latitude]},
        'bounds_native_m': [low.tolist(), high.tolist()], 'tiles': tiles, 'assets': assets,
        'statistics': {'models': len(assets), 'building_models': sum(item['kind'] == 'B' for item in assets),
            'triangles': sum(tile['triangles'] for tile in tiles), 'vertices': sum(tile['vertices'] for tile in tiles),
            'max_position_error_m': max(tile['max_position_error_m'] for tile in tiles),
            'source_geometry_decimated': False, 'raw_textures_retained': True},
        'source': {'provider': '香港特别行政区政府 · 地政总署',
            'dataset_url': plan['source_dataset'], 'attribution': '© 香港特别行政区政府；资料来源：地政总署 / Open3Dhk'},
        'storage': {'limit_bytes': budget.limit, 'actual_data_bytes': budget.ensure()},
        'rf_supported': False}
    # Native route maps the whole dataset; the manifest lives at its root, while
    # GLB tiles remain in city/ for a distinct derived-data boundary.
    for tile in manifest['tiles']:
        tile['url'] = '/city-data/city/' + tile['url']
    budget.ensure(len(json.dumps(manifest, ensure_ascii=False).encode()) * 2 + 65536)
    atomic_json(dataset / 'victoria-harbour.city.json', manifest)
    atomic_json(output / 'geometry-verification.json', {
        'complete': manifest['complete'], 'tiles': len(tiles), **manifest['statistics']})
    return {'manifest': str(dataset / 'victoria-harbour.city.json'), 'complete': manifest['complete'],
            **manifest['statistics'], 'data_bytes': budget.ensure()}
