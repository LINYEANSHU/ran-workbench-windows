"""Hand-checked glTF fixture catches axis, precision and topology changes."""
import base64
import json
from pathlib import Path
import struct

import numpy as np
import pytest


def gltf_fixture(folder, sparse=False):
    folder.mkdir(parents=True, exist_ok=True)
    # An 8 m wide, 6 m deep triangle; the extra float pins byteStride handling.
    body = struct.pack('<12f3H', 0, 0, 0, 9, 8, 0, 0, 9, 0, 6, 0, 9, 0, 1, 2)
    (folder / 'm.bin').write_bytes(body)
    source = {'asset': {'version': '2.0'}, 'buffers': [{'uri': 'm.bin', 'byteLength': len(body)}],
        'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': 48, 'byteStride': 16},
                        {'buffer': 0, 'byteOffset': 48, 'byteLength': 6}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3'},
                      {'bufferView': 1, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1, 'mode': 4}]}],
        'nodes': [{'matrix': [1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 833753.75, 50.795, -815887.25, 1],
                   'children': [1]}, {'mesh': 0}], 'scenes': [{'nodes': [0]}], 'scene': 0}
    path = folder / 'B337501588501063A0.gltf'
    path.write_text(json.dumps(source), 'utf-8')
    return path


def test_original_node_matrix_y_up_and_strided_vertices_preserve_native_metres(tmp_path):
    from ran_workbench.city_geometry import read_meshes
    path = gltf_fixture(tmp_path / 'asset')
    meshes = list(read_meshes(path))
    vertices, normals, indices = meshes[0]
    np.testing.assert_allclose(vertices, [[833753.75, 815887.25, 50.795],
        [833761.75, 815887.25, 50.795], [833753.75, 815893.25, 50.795]], atol=1e-8, rtol=0)
    assert indices.tolist() == [[0, 1, 2]]
    np.testing.assert_allclose(normals, [[0, 0, 1]] * 3, atol=1e-8)


def test_glb_recenter_does_not_simplify_and_has_submillimetre_roundtrip(tmp_path):
    from ran_workbench.city_geometry import read_meshes, write_geometry_glb, read_glb
    path = gltf_fixture(tmp_path / 'asset')
    original = list(read_meshes(path))
    report = write_geometry_glb(original, tmp_path / 'tile.glb')
    gltf, binary = read_glb(tmp_path / 'tile.glb')
    assert report['triangles'] == 1
    assert report['vertices'] == 3
    assert report['max_position_error_m'] < 0.001
    assert gltf['accessors'][2]['count'] == 3
    packed = np.frombuffer(binary, dtype='<f4', count=9).reshape(-1, 3)
    # GLB is Y-up: map (x,y,z) back to native (E,N,H).
    native = packed[:, [0, 2, 1]] * [1, -1, 1] + report['origin_native_m']
    np.testing.assert_allclose(native, original[0][0], rtol=0, atol=0.00001)


def test_missing_binary_or_outside_resource_is_explicit(tmp_path):
    from ran_workbench.city_geometry import read_meshes
    path = gltf_fixture(tmp_path / 'asset')
    (path.parent / 'm.bin').unlink()
    with pytest.raises(FileNotFoundError):
        list(read_meshes(path))
    source = json.loads(path.read_text('utf-8'))
    source['buffers'][0]['uri'] = '../outside.bin'
    path.write_text(json.dumps(source), 'utf-8')
    with pytest.raises(ValueError, match='resource|path'):
        list(read_meshes(path))


def test_highest_lod_is_selected_per_object_and_newest_version_within_level():
    from ran_workbench.city_geometry import highest_models
    names = ['B337501588501062A0', 'B337501588501063A0', 'B337501588501063A1',
             'B337501614301062D0']
    assert highest_models(names) == ['B337501588501063A1', 'B337501614301062D0']


def test_sparse_accessor_and_cycle_are_not_silently_ignored(tmp_path):
    from ran_workbench.city_geometry import read_meshes
    path = gltf_fixture(tmp_path / 'asset')
    source = json.loads(path.read_text('utf-8'))
    # Sparse override of the third triangle vertex: z=2, all in the source's Z-up mesh frame.
    (path.parent / 'sparse.bin').write_bytes(struct.pack('<B3x3f', 2, 0, 6, 2))
    source['buffers'].append({'uri': 'sparse.bin', 'byteLength': 16})
    source['bufferViews'].extend([{'buffer': 1, 'byteOffset': 0, 'byteLength': 1},
                                  {'buffer': 1, 'byteOffset': 4, 'byteLength': 12}])
    source['accessors'][0]['sparse'] = {'count': 1, 'indices': {'bufferView': 2, 'componentType': 5121},
        'values': {'bufferView': 3}}
    path.write_text(json.dumps(source), 'utf-8')
    vertices = list(read_meshes(path))[0][0]
    assert vertices[2, 2] == pytest.approx(52.795)
    source['nodes'][1]['children'] = [0]
    path.write_text(json.dumps(source), 'utf-8')
    with pytest.raises(ValueError, match='cycle'):
        list(read_meshes(path))
