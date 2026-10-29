"""Tiny synthetic import fixture generated at test time; no city dataset."""
import json


def write_import_fixture(path):
    rings = [
        [[836680,819000],[836720,819000],[836720,819020],[836700,819020],[836700,819040],[836680,819040],[836680,819000]],
        [[836750,819000],[836800,819000],[836800,819050],[836750,819050],[836750,819000]],
        [[836710,818940],[836740,818940],[836740,818970],[836710,818970],[836710,818940]],
    ]
    features = []
    for i, ring in enumerate(rings):
        coordinates = [ring]
        if i == 1:
            coordinates.append([[836765,819015],[836765,819035],[836785,819035],[836785,819015],[836765,819015]])
        features.append({'type':'Feature', 'properties':dict(name=f'Synthetic {i+1}', **({'height':[45,30][i]} if i<2 else {})),
                         'geometry':{'type':'Polygon','coordinates':coordinates}})
    path.write_text(json.dumps({'type':'FeatureCollection','features':features}), encoding='utf-8')
    return path


def city_fixture(folder):
    """One synthetic triangle, sufficient to exercise the local model viewer."""
    import numpy as np
    from ran_workbench.city_geometry import write_geometry_glb
    folder.mkdir(parents=True, exist_ok=True)
    vertices = np.array([[0.,0.,0.],[20.,0.,0.],[0.,20.,12.]])
    normals = np.array([[0.,0.,1.]] * 3)
    report = write_geometry_glb([(vertices, normals, np.array([[0,1,2]], dtype=np.uint32))], folder/'triangle.glb')
    bounds = [[0.,0.,0.],[20.,20.,12.]]
    tile = dict(report, id='fixture', bank='north', models=1, buildings=1, bounds_native_m=bounds,
                url='/city-data/triangle.glb')
    asset = dict(tile, tile='fixture', kind='B', lod=1, level_code='synthetic', vertices=3, triangles=1)
    manifest = dict(schema='ran-city-1', name='Synthetic integration fixture', complete=True, rf_supported=False,
        coordinate_system=dict(anchor_native_m=[0,0,0],render_anchor_deg=[113.3,23.1]),
        bounds_native_m=bounds, tiles=[tile], assets=[asset], statistics=dict(building_models=1,triangles=1),
        source=dict(provider='Synthetic test fixture', attribution='Synthetic triangle; not official city data'))
    path = folder/'fixture.city.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path
