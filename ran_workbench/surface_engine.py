"""CUDA mesh radio maps, with bounded launches and linear GPU accumulation."""
import importlib.metadata
from contextlib import contextmanager
from importlib import import_module
import math
import time

import numpy as np

from .spherical import launch_rotation, ray_chunks, sample_surfaces


class _FaceInteriorMask:
    """GPU lookup of finite physical edges on each actual hit triangle.

    Coplanar triangle seams are absent from the catalog. The lookup is bounded
    by the edges incident to one triangle, rather than all edges in the city.
    """
    def __init__(self, catalog, mi, dr):
        from .path_geometry import FACE_EDGE_EXCLUSION_M
        self.mi, self.dr, self.tables = mi, dr, []
        for object_id, obj in catalog.items():
            count = len(obj['faces'])
            width = max(map(len, obj['physical_edges']), default=0)
            edges = np.zeros((count, width, 2, 3), dtype=np.float32)
            sizes = np.array([len(row) for row in obj['physical_edges']], dtype=np.uint32)
            for index, row in enumerate(obj['physical_edges']):
                edges[index, :len(row)] = np.asarray(row).reshape(-1, 2, 3)
            columns = []
            for column in range(width):
                start, end = edges[:, column, 0], edges[:, column, 1]
                columns.append((mi.Point3f(*start.T), mi.Point3f(*end.T)))
            self.tables.append((object_id, count, mi.UInt(sizes), columns,
                                max(FACE_EDGE_EXCLUSION_M, obj['position_tolerance_m']) ** 2))

    def __call__(self, si, active):
        mi, dr = self.mi, self.dr
        shape = dr.reinterpret_array(mi.UInt, si.shape)
        allowed = ~active
        for object_id, count, sizes, columns, threshold2 in self.tables:
            own = active & (shape == object_id) & (si.prim_index < count)
            edge_count = dr.gather(mi.UInt, sizes, si.prim_index, active=own)
            on_edge = mi.Bool(False)
            for column, (starts, ends) in enumerate(columns):
                use = own & (column < edge_count)
                start = dr.gather(mi.Point3f, starts, si.prim_index, active=use)
                end = dr.gather(mi.Point3f, ends, si.prim_index, active=use)
                delta = end - start
                fraction = dr.clip(dr.dot(si.p - start, delta) /
                                   dr.maximum(dr.squared_norm(delta), 1e-20), 0, 1)
                on_edge |= use & (dr.squared_norm(si.p - start - fraction * delta) <= threshold2)
            allowed |= own & ~on_edge
        return allowed


def _face_only_radio_map_solver(rt, mi, dr, catalog):
    """Terminate excluded interactions before another ray can carry energy.

    This instance-local Sionna 2.2 adapter leaves the installed package intact.
    It applies to every bounce, including transmission and mixed histories.
    """
    solver = rt.RadioMapSolver()
    original = solver._sample_radio_material
    face_interior = _FaceInteriorMask(catalog, mi, dr)

    def sample(si, k_world, e_fields, solid_angle, sample1, sample2, enabled_interactions, active):
        allowed = face_interior(si, active)
        record, fields = original(si, k_world, e_fields, solid_angle, sample1, sample2,
                                  enabled_interactions, active & allowed)
        record.sampled_component = dr.select(allowed, record.sampled_component, mi.UInt(0))
        return record, fields

    solver._sample_radio_material = sample
    return solver


@contextmanager
def _randomized_launch_directions(mi, dr, seed):
    """Scope a Sionna 2.2 adapter to this single-job compute process.

    Sionna seeds its material sampler but launches a deterministic Fibonacci
    lattice. Rotate only that lattice, before array weights/antenna patterns
    are evaluated. The scene, polarization and antenna orientation stay fixed.
    The installed package is never edited and the symbol is always restored.
    """
    module = import_module('sionna.rt.radio_map_solvers.radio_map_solver')
    original = module.spawn_ray_from_sources
    rotation = mi.Matrix3f(launch_rotation(seed).tolist())
    dr.make_opaque(rotation)
    def spawn(*args, **kwargs):
        ray = original(*args, **kwargs)
        ray.d = rotation @ ray.d
        return ray
    module.spawn_ray_from_sources = spawn
    try:
        yield
    finally:
        module.spawn_ray_from_sources = original


def simulate_surface(project, progress=None):
    from .engine import _create_scene, _runtime, _staged_scene, _surface_catalog
    from .geometry import propagation_geometry
    from .model import project_fingerprint
    from .path_geometry import FACE_EDGE_EXCLUSION_M, FACE_ONLY_WARNING
    start = time.perf_counter()
    spec, settings = project['surface_sampling'], project['solver']
    sampled = sample_surfaces(project)
    rows, meshes = sampled['points'], sampled['meshes']
    chunks = list(ray_chunks(settings['samples_per_src'], spec['ray_chunk_size']))
    total = len(project['bs']) * len(meshes) * spec['replicates'] * len(chunks)
    completed = 0

    def report(stage, **extra):
        if progress:
            progress(dict(stage=stage, completed=completed, total=total, **extra))

    report('initializing_cuda')
    rt, mi, dr, root = _runtime()
    result = dict(schema_version=2, scene_sha256=project_fingerprint(project), mode='coverage',
        scene_geometry=propagation_geometry(project),
        backend=mi.variant(), points=rows, ues=[], surface_meshes=meshes, elapsed_s=0.,
        metric_kind='surface_average_noncoherent',
        sampling=dict(requested=sampled['requested'], excluded=sampled['excluded'], computed=len(rows)),
        surface={**spec, 'below_ground_cells':sampled['below_ground_cells'],
                 'building_cells':sampled['building_cells'], 'launches_per_replicate':len(chunks),
                 'rays_per_replicate':settings['samples_per_src'],
                 'nominal_cells_per_radius':20 * 4**spec['subdivisions'],
                 'receiver_convention':'ideal_isotropic_field_norm',
                 'geometry':'planar triangular facets approximating each nominal-radius sphere'},
        solver={**settings, 'algorithm':'Sionna MeshRadioMap', 'effective_batch_size':0,
                'launch_sampling':'Haar-rotated Fibonacci lattice per CUDA launch',
                'geometry_validation': {'face_only': True, 'edge_exclusion_m': FACE_EDGE_EXCLUSION_M,
                                        'scope': 'every_radio_material_interaction_before_ray_continuation'},
                'sionna_rt_version':importlib.metadata.version('sionna-rt'),
                'ray_chunk_size':spec['ray_chunk_size'], 'replicates':spec['replicates'],
                'total_launched_rays':0},
        warnings=[
            FACE_ONLY_WARNING,
            'Surface values are noncoherent area averages for an ideal isotropic receiver; exact polarized receiver power, coherent power, delays and path counts require a point probe.',
            'Cells crossing ground or a building are excluded. Facet centroids lie slightly inside the nominal sphere radius.',
            'Monte Carlo cells without a positive estimate have null power; this is not evidence of a physical zero or a measured coverage hole.',
            'Scene geometry and ITU material parameters are assumed and have not been calibrated to field measurements.',
        ])
    if spec['replicates'] > 1:
        result['warnings'].append('mc_standard_error_db_approx estimates only independent-replicate Monte Carlo variation using a linearized dB conversion; it is not a physical accuracy bound.')
    with _staged_scene(root) as staged:
        report('building_scene')
        scene = _create_scene(project, rt, staged)
        solver = _face_only_radio_map_solver(rt, mi, dr, _surface_catalog(scene, project))
        from sionna.rt.scene_utils import extend_scene_with_mesh
        for radius_index, mesh_data in enumerate(meshes):
            indices = mesh_data['point_indices']
            if not indices:
                completed += len(project['bs']) * spec['replicates'] * len(chunks)
                report('surface', radius_m=mesh_data['radius_m'])
                continue
            # Construct the GPU mesh directly; avoid serializing/parsing dense
            # ASCII PLY files or retaining an extra CPU scene representation.
            props = mi.Properties()
            props['face_normals'] = True
            props['bsdf'] = rt.RadioMaterial('measurement-dummy', 1.0)
            measurement = mi.Mesh('spherical-measurement', vertex_count=len(mesh_data['vertices_m']),
                                  face_count=len(mesh_data['faces']), props=props)
            params = mi.traverse(measurement)
            params['vertex_positions'] = mi.Float(np.asarray(mesh_data['vertices_m'], dtype=np.float32).ravel())
            params['faces'] = mi.UInt(np.asarray(mesh_data['faces'], dtype=np.uint32).ravel())
            params.update()
            # Build the OptiX measurement-scene acceleration structure once per
            # radius, reusing it across BSs, independent replicas and launches.
            modified_scene = extend_scene_with_mesh(scene.mi_scene, measurement)
            for bs_index, bs in enumerate(project['bs']):
                scene.tx_array = rt.PlanarArray(num_rows=1, num_cols=1,
                    vertical_spacing=.5, horizontal_spacing=.5, pattern=bs['pattern'], polarization='V')
                scene.add(rt.Transmitter(name='source', position=bs['position_m'],
                    orientation=[math.radians(a) for a in bs['orientation_deg']], power_dbm=bs['power_dbm']))
                mean = dr.zeros(mi.Float, len(indices))
                m2 = dr.zeros(mi.Float, len(indices))
                positive_replicates = dr.zeros(mi.UInt, len(indices))
                for replica in range(spec['replicates']):
                    replica_gain = dr.zeros(mi.Float, len(indices))
                    for chunk_index, count in enumerate(chunks):
                        seed = (settings['seed'] + 104729 * replica + 1009 * radius_index
                                + 15485863 * bs_index + 32452843 * chunk_index) % (2**32 - 1)
                        with _randomized_launch_directions(mi, dr, seed):
                            radio_map = solver(scene, measurement_surface=measurement, modified_scene=modified_scene,
                                samples_per_tx=count, max_depth=settings['max_depth'], seed=seed,
                                los=settings['los'], specular_reflection=settings['specular_reflection'],
                                diffuse_reflection=settings['diffuse_reflection'], refraction=settings['refraction'],
                                diffraction=False, edge_diffraction=False, check_cells_area=True)
                        gains = dr.ravel(radio_map.path_gain)
                        replica_gain += gains * (count / settings['samples_per_src'])
                        dr.eval(replica_gain)
                        dr.sync_thread()
                        result['solver']['total_launched_rays'] += count
                        completed += 1
                        report('surface', bs_id=bs['id'], radius_m=mesh_data['radius_m'],
                               replica=replica + 1, replicas=spec['replicates'], rays=count)
                        del radio_map, gains
                    positive_replicates += mi.UInt(replica_gain > 0)
                    delta = replica_gain - mean
                    mean += delta / (replica + 1)
                    m2 += delta * (replica_gain - mean)
                    dr.eval(mean, m2, positive_replicates)
                # Transfer only the final means/variation. No rays x receivers
                # CPU tensor or CPU electromagnetic solver exists in this path.
                gains = np.asarray(mean, dtype=np.float64)
                variance = np.maximum(np.asarray(m2, dtype=np.float64), 0)
                positives = np.asarray(positive_replicates)
                for local_index, global_index in enumerate(indices):
                    gain = float(gains[local_index])
                    if not math.isfinite(gain) or gain < 0:
                        raise RuntimeError('CUDA surface estimator returned an invalid path gain')
                    power = bs['power_dbm'] + 10 * math.log10(gain) if gain > 0 else None
                    error = None
                    if gain > 0 and spec['replicates'] > 1:
                        se = math.sqrt(float(variance[local_index]) /
                                       (spec['replicates'] * (spec['replicates'] - 1)))
                        error = 10 / math.log(10) * se / gain
                    rows[global_index]['links'].append(dict(bs_id=bs['id'], incoherent_dbm=power,
                        coherent_dbm=None, wideband_dbm=None, path_count=None, has_los=None,
                        rms_delay_ns=None, path_gain=gain, metric_kind='surface_average_noncoherent',
                        mc_standard_error_db_approx=error, positive_replicates=int(positives[local_index]),
                        estimate_status='positive_estimate' if gain > 0 else 'no_positive_estimate'))
                scene.remove('source')
            del modified_scene, measurement
        del solver, scene
    dr.sync_thread()
    result['elapsed_s'] = time.perf_counter() - start
    result['surface']['completed_launches'] = completed
    report('complete')
    from .network import attach_network
    return attach_network(result, project)
