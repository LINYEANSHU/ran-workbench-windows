"""Complete-CIR CUDA adapter; no displayed-path cap or synthetic RF fallback."""
from copy import deepcopy
import hashlib
import json
import math
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from .schema import validate_experiment
from .graph import compile_graph, required_channel_links
from .nr import slot_duration_s
from ..model import validate_project


def cir_response(beta,delays,valid,offsets_hz):
    """beta[rx_port,tx_port,path] already contains carrier propagation phase."""
    beta=np.asarray(beta,dtype=complex);delays=np.asarray(delays,dtype=float)
    offsets=np.asarray(offsets_hz,dtype=float)
    if beta.ndim!=3:raise ValueError('CIR requires rx,tx,path axes')
    n=beta.shape[-1]
    if not n:raise ValueError('没有有效路径；不能将未解析链路当作零信道')
    delays=np.broadcast_to(delays,beta.shape);valid=np.broadcast_to(valid,beta.shape).astype(bool)
    if not np.isfinite(beta).all() or not np.isfinite(delays).all() or np.any(delays[valid]<0) or not np.isfinite(offsets).all():raise ValueError('CIR 含无效系数 / 时延')
    if not np.any(valid & (np.abs(beta)>0)):raise ValueError('没有有效路径；请检查射线预算或场景')
    response=np.sum(np.where(valid,beta,0)[None,...]*np.exp(-2j*np.pi*offsets[:,None,None,None]*delays[None,...]),axis=-1)
    return response


class ReferenceFixture:
    """Explicit deterministic demonstration only; never selected as RT fallback."""
    metadata={'backend':'reference_fixture','calibrated':False,'description':'合成时变信道，仅供操作演示 / 回归，不是城市传播结果'}
    def __init__(self,experiment):self.exp=experiment;self.pol=compile_graph(experiment)
    def snapshot(self,slot):
        s=self.exp['settings'];result={}
        for bi,b in enumerate(sorted(self.exp['scene']['bs'],key=lambda x:x['id'])):
            result[b['id']]={}
            for ui,u in enumerate(sorted(self.exp['scene']['ues'],key=lambda x:x['id'])):
                db=-100+10*math.sin(slot/8+ui*2+bi)
                amp=10**(db/20)
                response=amp*(1+.3*np.cos(np.arange(s['n_prb'])+ui))
                nt=self.pol['bs'][b['id']]['power']['tx_ports'];nr=self.pol['ues'][u['id']]['decode']['rx_ports']
                base=np.ones((nr,nt),complex)
                if nr==nt==2:base=np.array([[1,.3j],[.2,1]],complex)
                result[b['id']][u['id']]=response[:,None,None]*base[None,...]
        return result


class RTChannel:
    def __init__(self,experiment,progress=None):
        self.exp=validate_experiment(experiment);self.pol=compile_graph(self.exp);self.progress=progress
        self.cached=None;self.last_slot=None;self.runtime=None
        self.interval=self.pol['global']['global']['channel']['snapshot_slots']
        self.moving=any(any(p['mobility'][k]!=0 for k in ('vx','vy','vz')) for p in self.pol['ues'].values())
        self.checked_slot=0
        self.solids=[(b['id'],Polygon(b['footprint_m'],b['holes_m']),b['base_m'],b['base_m']+b['height_m']) for b in self.exp['scene']['buildings']]
        self.metadata=dict(backend='Sionna RT / CUDA',calibrated=False,prb_channel='center-frequency flat per PRB',
                           motion='piecewise-constant RT snapshots; continuous outdoor trajectory validation; no intra-snapshot Doppler',snapshots=[],
                           tx_ports={bid:p['power']['tx_ports'] for bid,p in self.pol['bs'].items()},
                           rx_ports={uid:p['decode']['rx_ports'] for uid,p in self.pol['ues'].items()},
                           geometry_filter='same finite-solid/face-only filter as propagation engine')

    def snapshot(self,slot):
        # Geometry constraints apply at every slot, even while H is held. Clip
        # each continuous motion segment to the prism's vertical extent before
        # testing its XY intersection; this preserves roofs and courtyards.
        dt=slot_duration_s(self.exp['settings']['mu']);positions={}
        for ue in self.exp['scene']['ues']:
            uid=ue['id'];v=self.pol['ues'][uid]['mobility']
            a=np.asarray([p+self.checked_slot*dt*v[k] for p,k in zip(ue['position_m'],('vx','vy','vz'))])
            b=np.asarray([p+slot*dt*v[k] for p,k in zip(ue['position_m'],('vx','vy','vz'))])
            if not np.isfinite(b).all() or min(a[2],b[2])<0:raise ValueError(f'{uid}: trajectory must stay above or on ground')
            positions[uid]=b.tolist()
            if not self.moving:continue # initial static positions were validated
            delta=b-a
            for ident,polygon,base,top in self.solids:
                if delta[2]==0:
                    if not base<=a[2]<=top:continue
                    low,high=0.,1.
                else:
                    z0,z1=sorted(((base-a[2])/delta[2],(top-a[2])/delta[2]))
                    low,high=max(0.,z0),min(1.,z1)
                    if low>high:continue
                start,end=(a+low*delta)[:2],(a+high*delta)[:2]
                line=Point(start) if np.array_equal(start,end) else LineString([start,end])
                if polygon.intersects(line):raise ValueError(f'{uid}: trajectory enters or touches building {ident}')
        self.checked_slot=slot
        if self.cached is not None and (not self.moving or slot-self.last_slot<self.interval):return self.cached
        project=deepcopy(self.exp['scene'])
        for ue in project['ues']:
            ue['position_m']=positions[ue['id']]
        project=validate_project(project)
        self.cached=self._solve(project,slot);self.last_slot=slot
        self.metadata['snapshots'].append(dict(slot=slot,time_s=slot*dt,
             ue_positions={u['id']:u['position_m'] for u in project['ues']},
             links=deepcopy(self.metadata.get('latest_links',[])),
             max_hold_position_error_m=max(math.sqrt(sum(p['mobility'][k]**2 for k in ('vx','vy','vz')))*self.interval*dt for p in self.pol['ues'].values())))
        return self.cached

    def _solve(self,project,slot):
        from ..engine import _runtime,_staged_scene,_create_scene,_surface_catalog,_CandidateBudgetGuard
        from ..compute_policy import candidate_path_budget
        from ..path_geometry import validate_sparse_paths
        if self.runtime is None:self.runtime=_runtime()
        rt,mi,dr,root=self.runtime;s=project['solver'];settings=self.exp['settings']
        offsets=(np.arange(settings['n_prb'])-(settings['n_prb']-1)/2)*12*15000*2**settings['mu']
        required=required_channel_links(project,self.pol)
        result={};count=0;total=len(required);self.metadata['latest_links']=[]
        self.metadata['omitted_links']=[dict(bs_id=b['id'],ue_id=u['id'],status='not_applicable_orthogonal') for b in project['bs'] for u in project['ues'] if (b['id'],u['id']) not in required]
        with _staged_scene(root) as folder:
            scene=_create_scene(project,rt,folder);catalog=_surface_catalog(scene,project)
            solver=rt.PathSolver();solver._candidate_generator=_CandidateBudgetGuard(solver._candidate_generator,dr)
            try:
                for b in sorted(project['bs'],key=lambda x:x['id']):
                    result[b['id']]={}
                    radio=self.pol['bs'][b['id']]['power'];nt=radio['tx_ports']
                    scene.tx_array=rt.PlanarArray(num_rows=1,num_cols=nt,vertical_spacing=.5,horizontal_spacing=radio['spacing_lambda'],pattern=b['pattern'],polarization='V')
                    scene.add(rt.Transmitter(name='source',position=b['position_m'],orientation=[math.radians(v) for v in b['orientation_deg']],power_dbm=b['power_dbm']))
                    try:
                        for u in sorted(project['ues'],key=lambda x:x['id']):
                            if (b['id'],u['id']) not in required:continue
                            if self.progress:self.progress(dict(stage=f"CUDA 信道 · {b['id']} → {u['id']}",completed=count,total=total,slot=slot))
                            receiver=self.pol['ues'][u['id']]['decode'];nr=receiver['rx_ports']
                            scene.rx_array=rt.PlanarArray(num_rows=1,num_cols=nr,vertical_spacing=.5,horizontal_spacing=receiver['spacing_lambda'],pattern='iso',polarization='V')
                            scene.add(rt.Receiver(name='receiver',position=u['position_m']))
                            try:
                                paths=solver(scene,max_depth=s['max_depth'],samples_per_src=s['samples_per_src'],
                                    max_num_paths_per_src=candidate_path_budget(s,1),synthetic_array=True,
                                    los=s['los'],specular_reflection=s['specular_reflection'],diffuse_reflection=s['diffuse_reflection'],
                                    refraction=s['refraction'],diffraction=False,edge_diffraction=False,seed=s['seed'])
                                coefficients,delays=paths.cir(normalize_delays=False,out_type='numpy')
                                coefficients=np.asarray(coefficients,dtype=complex);n=coefficients.shape[-2]
                                native_valid=np.asarray(paths.valid)
                                # Synthetic arrays share geometric path validity
                                # across ports, despite the public 5D annotation.
                                valid=np.broadcast_to(native_valid.reshape(n) if native_valid.size==n else native_valid.reshape(nr,nt,n),(nr,nt,n)).copy()
                                if n:
                                    bits=validate_sparse_paths(paths._paths_buffer,b['position_m'],[u['position_m']],np.asarray(paths._rx_ind,dtype=int),catalog)
                                    rejected=np.zeros(n,bool);rejected[np.asarray(paths._path_ind,dtype=int)]=bits!=0;valid&=~rejected[None,None,:]
                                try:
                                    delays=np.asarray(delays);tau=delays.reshape(n) if delays.size==n else delays.reshape(nr,nt,n)
                                    result[b['id']][u['id']]=cir_response(coefficients.reshape(nr,nt,n),tau,valid,offsets)
                                except ValueError as error:
                                    raise ValueError(f"{b['id']} → {u['id']}: {error}") from error
                                self.metadata['latest_links'].append(dict(bs_id=b['id'],ue_id=u['id'],matrix_shape=list(result[b['id']][u['id']].shape),
                                    path_slots=n,valid_geometry_paths=int(np.count_nonzero(np.any(valid,axis=(0,1))))))
                                dr.sync_thread();count+=1
                                del paths
                            finally:scene.remove('receiver')
                    finally:scene.remove('source')
            finally:del solver,scene
        self.metadata.update(sionna_rt_version='2.2.0',cuda_variant=mi.variant(),solver=deepcopy(s),
                             channel_sha256=hashlib.sha256(json.dumps(dict(project=project,offsets_hz=offsets.tolist()),sort_keys=True).encode()).hexdigest())
        return result
