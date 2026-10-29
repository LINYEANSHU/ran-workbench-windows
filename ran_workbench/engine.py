"""Bounded 3-D Sionna RT simulation on a real CUDA backend.

Native libraries are deliberately imported only inside ``simulate``. Meshes
and the Dr.Jit cache live in an ASCII temporary directory because the native
Windows PLY/OptiX readers cannot safely consume the Unicode project path.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid
from xml.etree import ElementTree as ET


def _runtime():
    """Select CUDA explicitly; a CPU or synthetic fallback is never accepted."""
    root = Path(tempfile.gettempdir()) / "ran-workbench-rt"
    if not str(root).isascii():
        raise RuntimeError("CUDA RT requires an ASCII TEMP directory on this Windows runtime")
    root.mkdir(parents=True, exist_ok=True)
    cache = root / "drjit-cache"
    cache.mkdir(exist_ok=True)
    # This app owns its worker environment; do not inherit an incompatible
    # Unicode cache path from a source checkout or another Python application.
    os.environ["DRJIT_CACHE_DIR"] = str(cache)
    os.environ.setdefault("MPLCONFIGDIR", str(root / "matplotlib"))
    import mitsuba as mi
    import drjit as dr
    mi.set_variant("cuda_ad_mono_polarized")
    import sionna.rt as rt
    if mi.variant() != "cuda_ad_mono_polarized" or not dr.has_backend(dr.JitBackend.CUDA):
        raise RuntimeError("A working real CUDA backend is required; no fallback is available")
    # Force an actual CUDA allocation and kernel, not just a compiled-backend
    # availability check. Driver/runtime failures must fail the job explicitly.
    probe = dr.arange(mi.Float, 4)
    dr.eval(probe + 1)
    dr.sync_thread()
    return rt, mi, dr, root


def _write_ply(path, vertices, faces):
    header = ["ply", "format ascii 1.0", f"element vertex {len(vertices)}",
              "property float x", "property float y", "property float z",
              f"element face {len(faces)}", "property list uchar int vertex_indices", "end_header"]
    with Path(path).open("w", encoding="ascii", newline="\n") as file:
        file.write("\n".join(header) + "\n")
        for vertex in vertices:
            file.write(" ".join(format(float(value), ".17g") for value in vertex) + "\n")
        for face in faces:
            file.write("3 " + " ".join(str(int(value)) for value in face) + "\n")


@contextmanager
def _staged_scene(root):
    # Python 3.12 mkdtemp applies a user-only Windows ACL (mode 0700), which
    # excludes the restricted desktop worker token. Inherit the parent TEMP
    # ACL with an ordinary mkdir instead; the random name remains private to
    # this job and cleanup is restricted to this exact verified child.
    root = Path(root).resolve()
    folder = root / f"scene-{uuid.uuid4().hex}"
    folder.mkdir()
    try:
        yield folder
    finally:
        target = folder.resolve()
        if target.parent != root or not target.name.startswith("scene-"):
            raise RuntimeError("Refusing to clean a scene directory outside the runtime root")
        shutil.rmtree(target)


def _create_scene(project, rt, folder):
    from .geometry import building_mesh, propagation_geometry

    xml = ET.Element("scene", version="3.0.0")
    diffuse = project["solver"]["diffuse_reflection"]
    scattering = project["solver"]["scattering_coefficient"] if diffuse else 0.0

    def material(identifier, itu_type, thickness):
        node = ET.SubElement(xml, "bsdf", type="itu-radio-material", id=identifier)
        ET.SubElement(node, "string", name="type", value=itu_type)
        ET.SubElement(node, "float", name="thickness", value=str(thickness))
        ET.SubElement(node, "float", name="scattering_coefficient", value=str(scattering))

    def shape(identifier, filename, material_id):
        node = ET.SubElement(xml, "shape", type="ply", id=identifier)
        ET.SubElement(node, "string", name="filename", value=filename)
        ET.SubElement(node, "boolean", name="face_normals", value="true")
        ET.SubElement(node, "ref", id=material_id, name="bsdf")

    # The explicit finite, flat ground covers all scene/sampling/device bounds.
    # Free-floating ground-boundary edges are excluded from diffraction.
    bounds = propagation_geometry(project)['ground_bounds_m']
    x0, y0, _ = bounds['min_m']
    x1, y1, _ = bounds['max_m']
    material("material_ground", "concrete", 0.2)
    _write_ply(folder / "ground.ply", [[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0]],
               [[0, 1, 2], [0, 2, 3]])
    shape("mesh_ground", "ground.ply", "material_ground")
    for index, building in enumerate(project["buildings"]):
        material_id = f"material_{index}"
        material(material_id, building["material"], building["thickness_m"])
        mesh = building_mesh(building)
        filename = f"building_{index:05d}.ply"
        _write_ply(folder / filename, mesh["vertices"], mesh["faces"])
        shape(f"mesh_building_{index}", filename, material_id)
    filename = folder / "scene.xml"
    ET.ElementTree(xml).write(filename, encoding="ascii", xml_declaration=True)
    scene = rt.load_scene(str(filename), merge_shapes=False)
    scene.frequency = project["frequency_hz"]
    scene.bandwidth = project["bandwidth_hz"]
    scene.rx_array = rt.PlanarArray(num_rows=1, num_cols=1, vertical_spacing=0.5,
                                   horizontal_spacing=0.5, pattern="iso", polarization="V")
    return scene


def _path_kind(codes):
    names = {1: "specular", 2: "diffuse", 4: "transmission", 8: "diffraction"}
    interactions = [names.get(int(code), "unknown") for code in codes if int(code) != 0]
    if not interactions:
        return "los", interactions
    unique = set(interactions)
    return (interactions[0] if len(unique) == 1 else "mixed"), interactions


class _CandidateCapacityError(RuntimeError):
    """The native candidate list filled before physical path validation."""


class _CandidateBudgetGuard:
    """Observe Sionna 2.2's candidate count before image-method filtering.

    The final Paths buffer has discarded invalid candidates and cannot reveal
    earlier saturation. This app always has exactly one source per solve.
    """
    def __init__(self, generator, dr):
        self.generator, self.dr = generator, dr

    def __call__(self, **kwargs):
        buffer = self.generator(**kwargs)
        buffer.schedule()
        self.dr.eval()
        if int(buffer.paths_counter[0]) >= kwargs['max_num_paths_per_src']:
            raise _CandidateCapacityError('Candidate-path buffer reached its memory budget; reduce receiver batch size or interaction depth and retry. No truncated RF result was accepted.')
        return buffer


def _surface_catalog(scene, project):
    """Read actual loaded triangles, including native mesh face ordering."""
    import numpy as np
    from .path_geometry import mesh_topology, solid_geometry, position_tolerance
    catalog = {}
    for obj in scene.objects.values():
        name = obj.name
        building = None
        if name.startswith('mesh_building_'):
            building = project['buildings'][int(name.rsplit('_', 1)[1])]
        mesh = obj.mi_mesh
        vertices = np.asarray(mesh.vertex_positions_buffer()).reshape(-1, 3).astype(float)
        faces = np.asarray(mesh.faces_buffer()).reshape(-1, 3)
        triangles = vertices[faces]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        sizes = np.linalg.norm(normals, axis=1)
        normals /= np.where(sizes > 0, sizes, 1)[:, None]
        catalog[int(obj.object_id)] = {
            'object_id': building['id'] if building else 'ground',
            'object_name': building['name'] if building else '地面',
            'material': building['material'] if building else 'concrete',
            'vertices': vertices, 'faces': faces,
            'triangles': triangles, 'normals': normals,
            'physical_edges': mesh_topology(vertices, faces),
            'solid': solid_geometry(building) if building else None,
            'position_tolerance_m': position_tolerance(vertices),
        }
    return catalog


def _surface_hit(catalog, object_id, primitive_id):
    import numpy as np
    obj = catalog.get(int(object_id))
    record = {'object_id': str(int(object_id)), 'object_name': '未知表面',
              'material': None, 'primitive_id': int(primitive_id)}
    if obj is not None and 0 <= int(primitive_id) < len(obj['faces']):
        triangle = obj['vertices'][obj['faces'][int(primitive_id)]]
        normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        norm = np.linalg.norm(normal)
        if norm:
            normal /= norm
        record.update({key: obj[key] for key in ('object_id', 'object_name', 'material')})
        record.update(normal=normal.astype(float).tolist(), triangle_m=triangle.astype(float).tolist())
        record['physical_edges_m'] = obj['physical_edges'][int(primitive_id)]
        record['position_tolerance_m'] = obj['position_tolerance_m']
        if obj['object_id'] != 'ground':
            record['object_name'] += ' · ' + ('屋顶/底面' if abs(float(normal[2])) > 0.9 else '外墙')
    return record


def _extract_links(paths, bs, receivers, bandwidth_hz, details, warnings, catalog=None):
    import numpy as np
    from .channels import complex_response, frequency_response, summarize_channels

    # cir() applies carrier propagation phase exactly once. paths.a is the
    # *passband* coefficient and must never be treated as this baseband beta.
    coefficients, delays = paths.cir(normalize_delays=False, out_type="numpy")
    count = len(receivers)
    path_slots = coefficients.shape[-2]
    coefficients = np.asarray(coefficients, dtype=np.complex128).reshape(count, path_slots)
    delays = np.asarray(delays, dtype=np.float64).reshape(count, path_slots)
    valid = np.asarray(paths.valid).reshape(count, path_slots)
    geometry_mask = np.ones_like(valid, dtype=bool)
    rejection_bits = np.zeros_like(valid, dtype=np.uint8)
    if catalog is not None and path_slots:
        from .path_geometry import validate_sparse_paths
        sparse_rx = np.asarray(paths._rx_ind, dtype=int)
        sparse_slots = np.asarray(paths._path_ind, dtype=int)
        bits = validate_sparse_paths(paths._paths_buffer, bs['position_m'], receivers, sparse_rx, catalog)
        rejection_bits[sparse_rx, sparse_slots] = bits
        geometry_mask[sparse_rx, sparse_slots] = bits == 0
    if details:
        interactions = np.asarray(paths.interactions)
        depth = interactions.shape[0]
        interactions = interactions.reshape(depth, count, path_slots)
        vertices = np.asarray(paths.vertices).reshape(depth, count, path_slots, 3)
        objects = np.asarray(paths.objects).reshape(depth, count, path_slots)
        primitives = np.asarray(paths.primitives).reshape(depth, count, path_slots)
    elif path_slots:
        # Sionna 2.2 builds vertices/objects/primitives together when the public
        # interactions property is first requested. Coverage only needs a LoS
        # flag: inspect the compact native path buffer and its RX/slot mapping
        # instead of allocating depth x RX x max-path tensors for all geometry.
        sparse_interactions = np.asarray(paths._paths_buffer.interaction_types)
        sparse_los = np.all(sparse_interactions == 0, axis=1)
        los_receivers = np.asarray(paths._rx_ind)[sparse_los]
        los_slots = np.asarray(paths._path_ind)[sparse_los]
    else:
        los_receivers = los_slots = np.empty(0, dtype=int)
    output = []
    for receiver_index, receiver in enumerate(receivers):
        beta, tau = coefficients[receiver_index], delays[receiver_index]
        active = (valid[receiver_index] & np.isfinite(beta.real) & np.isfinite(beta.imag)
                  & np.isfinite(tau) & (tau >= 0) & ((beta.real != 0) | (beta.imag != 0)))
        checked = active.copy()
        active &= geometry_mask[receiver_index]
        indices = np.flatnonzero(active)
        selected_beta, selected_tau = beta[active], tau[active]
        summary = summarize_channels(selected_beta, selected_tau, bs["power_dbm"], bandwidth_hz)
        summary["bs_id"] = bs["id"]
        if catalog is not None:
            from .path_geometry import REJECTION_REASONS
            rejected = checked & ~geometry_mask[receiver_index]
            summary['geometry_filter'] = {
                'checked_path_count': int(np.count_nonzero(checked)),
                'rejected_path_count': int(np.count_nonzero(rejected)),
                'rejected_by_reason': {name: int(np.count_nonzero(checked & ((rejection_bits[receiver_index] & bit) != 0)))
                                       for bit, name in REJECTION_REASONS.items()}}
            if np.any(rejected):
                message = 'Independent finite-solid geometry validation rejected invalid paths before power, phase and CFR reduction; inspect geometry_filter.'
                if message not in warnings:
                    warnings.append(message)
        summary["has_los"] = (bool(np.any(np.all(interactions[:, receiver_index, indices] == 0, axis=0)))
                              if details else bool(np.any(active[los_slots[los_receivers == receiver_index]])))
        if details:
            summary["paths"] = []
            ordered = sorted(indices, key=lambda index: abs(beta[index]), reverse=True)
            summary['displayed_path_count'] = min(50, len(ordered))
            scale = max((abs(beta[index]) for index in ordered), default=0)
            denominator = sum(abs(beta[index] / scale) ** 2 for index in ordered) if scale else 0
            summary['displayed_power_fraction'] = (float(sum(abs(beta[index] / scale) ** 2
                for index in ordered[:50]) / denominator) if denominator else None)
            for index in ordered[:50]:
                codes = interactions[:, receiver_index, index]
                kind, interaction_names = _path_kind(codes)
                points = [list(bs["position_m"])]
                points.extend(vertices[:, receiver_index, index, :][codes != 0].astype(float).tolist())
                points.append(list(receiver))
                value = beta[index]
                path_record = {"kind": kind, "interaction_types": interaction_names,
                    "delay_ns": float(tau[index] * 1e9),
                    "power_dbm": float(bs["power_dbm"] + 20 * math.log10(abs(value))),
                    "phase_deg": float(math.degrees(math.atan2(value.imag, value.real))),
                    "beta_real": float(value.real), "beta_imag": float(value.imag), "vertices_m": points}
                from .path_audit import audit_path
                surfaces = [_surface_hit(catalog or {}, objects[d, receiver_index, index],
                                         primitives[d, receiver_index, index])
                            for d in np.flatnonzero(codes)]
                path_record.update(audit_path(points, interaction_names, path_record['delay_ns'], surfaces))
                summary['paths'].append(path_record)
                if not path_record['geometry_valid']:
                    message = 'Some displayed paths failed the independent geometry audit; inspect their geometry_warnings before interpretation.'
                    if message not in warnings:
                        warnings.append(message)
            if len(ordered) > 50:
                summary["paths_truncated"] = int(len(ordered) - 50)
                message = "UE path display is limited to the strongest 50 paths per link; summaries and CFR use all valid paths."
                if message not in warnings:
                    warnings.append(message)
            summary["cfr"] = frequency_response(selected_beta, selected_tau, bandwidth_hz)
            summary['complex_cfr'] = complex_response(selected_beta, selected_tau, bs['power_dbm'], bandwidth_hz)
        output.append(summary)
    return output


def simulate(project: dict, mode: str = "coverage", progress=None, probe_position_m=None) -> dict:
    """Trace a validated frozen project, returning JSON-safe result schema v2."""
    if mode not in ("coverage", "ue", "probe"):
        raise ValueError("mode must be 'coverage', 'ue' or 'probe'")
    from .model import project_fingerprint, validate_project
    from .geometry import sample_points, propagation_geometry

    project = validate_project(project)
    if mode == 'coverage' and project.get('surface_sampling', {}).get('enabled'):
        from .surface_engine import simulate_surface
        return simulate_surface(project, progress)
    if mode == 'probe':
        from .geometry import _vector, contains_point
        probe_position_m = _vector(probe_position_m, 3, 'probe position_m')
        if probe_position_m[2] < 0 or contains_point(project['buildings'], probe_position_m):
            raise ValueError('Probe must be above ground and outside buildings')
    from .compute_policy import candidate_path_budget, effective_batch_size
    settings = project['solver']
    batch_size = effective_batch_size(settings)
    start = time.perf_counter()
    if mode == "coverage":
        sampled = sample_points(project)
        positions = sampled["positions_m"]
        sampling = {"requested": int(sampled["requested"]), "excluded": int(sampled["excluded"]),
                    "computed": len(positions)}
    elif mode == 'probe':
        positions = [probe_position_m]
        sampling = {"requested": 0, "excluded": 0, "computed": 0}
    else:
        positions = [ue["position_m"] for ue in project["ues"]]
        sampling = {"requested": 0, "excluded": 0, "computed": 0}
    total = len(positions) * len(project["bs"])

    def report(stage, completed=0, **extra):
        if progress:
            progress({"stage": stage, "completed": completed, "total": total, **extra})

    report("initializing_cuda")
    rt, mi, dr, runtime_root = _runtime()
    from .path_geometry import FACE_EDGE_EXCLUSION_M, FACE_ONLY_WARNING
    warnings = ["Flat ground at ENU z=0; per-surface ITU materials and 1×1 vertically polarized antennas are assumed, not measured.",
                "Finite stochastic ray sampling can miss paths; a null result means no valid resolved path, not a measured physical zero.",
                FACE_ONLY_WARNING]
    if project["solver"]["refraction"]:
        warnings.append("Transmission uses the Sionna thin-layer approximation: rays remain straight and each crossed surface applies its material slab thickness; closed-building entry/exit walls are separate layers.")
    result = {"schema_version": 2, "scene_sha256": project_fingerprint(project), "mode": mode,
              "backend": mi.variant(), "points": [], "ues": [], "sampling": sampling,
              "scene_geometry": propagation_geometry(project),
              "elapsed_s": 0.0, "warnings": warnings}
    import importlib.metadata
    result['solver'] = {**settings, 'effective_batch_size': min(batch_size, len(positions)),
                       'candidate_path_budget': candidate_path_budget(settings, min(batch_size, len(positions))),
                       'sionna_rt_version': importlib.metadata.version('sionna-rt'),
                       'spatial_spacing_m': list(project['sampling']['spacing_m'])}
    if batch_size < settings['batch_size']:
        warnings.append(f"Receiver batch reduced from {settings['batch_size']} to {batch_size} by the ray/depth work budget; grid density is unchanged.")
    if settings['diffuse_reflection']:
        warnings.append('Diffuse paths are Monte Carlo surface samples, not discrete mirror rays. Increase rays and compare seeds for convergence; displayed samples are not all scattered energy.')
        warnings.append('Carrier-coherent power with sampled diffuse scattering can fluctuate with ray count and seed; evaluate noncoherent or bandwidth-averaged coverage and check convergence separately.')
    rows = [{"position_m": list(position), "links": []} for position in positions]
    if mode in ("ue", "probe"):
        devices = [{'id': 'PROBE'}] if mode == 'probe' else project['ues']
        for row, ue in zip(rows, devices):
            row["ue_id"] = ue["id"]
        result["ues"] = rows
    else:
        result["points"] = rows
    # Exactly one BS per solve supports different patterns/orientations and
    # bounds memory independently of BS count. Never form rays × cloud tensors.
    if positions and project["bs"]:
        with _staged_scene(runtime_root) as staged, ExitStack() as work:
            report("building_scene")
            scene = _create_scene(project, rt, Path(staged))
            catalog = _surface_catalog(scene, project)
            result['solver']['geometry_validation'] = {
                'scope': 'all_discrete_paths_before_rf_reduction',
                'finite_solid_visibility': True, 'physical_mesh_edges': True,
                'face_only': True, 'edge_exclusion_m': FACE_EDGE_EXCLUSION_M,
                'max_position_tolerance_m': max((obj['position_tolerance_m'] for obj in catalog.values()), default=0.0001)}
            solver = rt.PathSolver()
            solver._candidate_generator = _CandidateBudgetGuard(solver._candidate_generator, dr)
            from .parallel_coverage import (CoveragePipeline, cpu_worker_count,
                download_coverage_paths, receiver_batch_targets)
            cpu_workers = cpu_worker_count(len(positions), batch_size) if mode == 'coverage' else 1
            pipeline = work.enter_context(CoveragePipeline(catalog, cpu_workers)) if mode == 'coverage' else None
            result['solver'].update(endpoint_preparation='batched_cuda_arrays', cpu_workers=cpu_workers)
            completed = 0

            def collect(batches):
                nonlocal completed
                for reduced in batches:
                    for index, link in enumerate(reduced['links']):
                        rows[reduced['first'] + index]['links'].append(link)
                    for message in reduced['warnings']:
                        if message not in warnings:
                            warnings.append(message)
                    completed += len(reduced['links'])
                    report('tracing', completed, bs_id=reduced['links'][0]['bs_id'],
                           cpu_workers=cpu_workers)

            for bs in project["bs"]:
                scene.tx_array = rt.PlanarArray(num_rows=1, num_cols=1, vertical_spacing=0.5,
                        horizontal_spacing=0.5, pattern=bs["pattern"], polarization="V")
                scene.add(rt.Transmitter(name="source", position=bs["position_m"],
                        orientation=[math.radians(value) for value in bs["orientation_deg"]],
                        power_dbm=bs["power_dbm"]))
                first = 0
                while first < len(positions):
                    batch = positions[first:first + batch_size]
                    for index, position in enumerate(batch):
                        scene.add(rt.Receiver(name=f"receiver_{index}", position=position))
                    report("tracing", completed, bs_id=bs["id"])
                    try:
                        with receiver_batch_targets(scene, batch, mi, dr):
                            paths = solver(scene, max_depth=settings["max_depth"],
                                samples_per_src=settings["samples_per_src"],
                                max_num_paths_per_src=candidate_path_budget(settings, len(batch)),
                                synthetic_array=True, los=settings.get("los", True),
                                specular_reflection=settings.get("specular_reflection", True),
                                diffuse_reflection=settings["diffuse_reflection"],
                                refraction=settings["refraction"], diffraction=False,
                                edge_diffraction=False, seed=settings["seed"])
                    except _CandidateCapacityError:
                        scene.remove([f"receiver_{index}" for index in range(len(batch))])
                        if len(batch) <= 1:
                            raise
                        batch_size = max(1, len(batch) // 2)
                        result['solver']['effective_batch_size'] = min(result['solver']['effective_batch_size'], batch_size)
                        message = f'Candidate capacity reached: receiver batch reduced to {batch_size} and retraced; no saturated batch contributes to the result. Grid density and launched ray count are unchanged.'
                        if message not in warnings:
                            warnings.append(message)
                        report('tracing', completed, bs_id=bs['id'], effective_batch_size=batch_size,
                               reason='retry_candidate_capacity')
                        continue
                    if pipeline is not None:
                        collect(pipeline.submit(first, download_coverage_paths(paths), bs,
                                                batch, project['bandwidth_hz']))
                    else:
                        links = _extract_links(paths, bs, batch, project["bandwidth_hz"], True, warnings, catalog)
                        for index, link in enumerate(links):
                            rows[first + index]["links"].append(link)
                        completed += len(batch)
                    dr.sync_thread()
                    first += len(batch)
                    report("tracing", completed, bs_id=bs["id"])
                    scene.remove([f"receiver_{index}" for index in range(len(batch))])
                    del paths
                scene.remove("source")
            if pipeline is not None:
                collect(pipeline.finish())
                result['solver'].update(cpu_worker_pids=sorted(pipeline.worker_pids),
                    cpu_reduction_work_s=pipeline.cpu_elapsed_s,
                    cpu_peak_pending_batches=pipeline.peak_pending)
            # Scene retains native references to its mesh files until here.
            del solver, scene
    dr.sync_thread()
    result["elapsed_s"] = float(time.perf_counter() - start)
    report("complete", total)
    from .network import attach_network
    return attach_network(result, project)
