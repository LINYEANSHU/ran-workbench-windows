"""Two-port Type-I single-panel subset, TS 38.214 V18.4.0 table 5.2.2.2.1-1.

Rank/PMI search and MMSE receiver are implementation choices. Every W has
unit Frobenius energy, so total transmit power is independent of rank.
"""
import numpy as np
from .nr import integer


def precoder(tx_ports,rank,pmi):
    integer(tx_ports,'tx_ports',1,2);integer(rank,'RI',1,tx_ports)
    if tx_ports==1:
        if pmi!=0:raise ValueError('SISO has no PMI')
        return np.ones((1,1),complex)
    integer(pmi,'PMI',0,3 if rank==1 else 1)
    if rank==1:return np.array([[1],[1j**pmi]],complex)/np.sqrt(2)
    phase=1 if pmi==0 else 1j
    return np.array([[1,1],[phase,-phase]],complex)/2


def post_mmse_sinr(h,w,power_w,noise_cov):
    h=np.asarray(h,dtype=complex);w=np.asarray(w,dtype=complex);r=np.asarray(noise_cov,dtype=complex)
    if h.ndim!=3 or w.ndim!=2 or h.shape[-1]!=w.shape[0] or power_w<0:raise ValueError('MIMO dimensions / power invalid')
    if not np.isfinite(h).all() or not np.isfinite(r).all() or not np.isfinite(w).all():raise ValueError('Nonfinite MIMO data')
    g=np.sqrt(power_w)*(h@w)
    gram=np.swapaxes(g.conj(),-1,-2)@np.linalg.solve(r,g)
    error=np.linalg.inv(np.eye(w.shape[1])+gram)
    return np.maximum(0,1/np.real(np.diagonal(error,axis1=-2,axis2=-1))-1)


def select_csi(h,noise_cov,power_w,rank_limit,subset_mask,model,symbols=12,dmrs_re=12,target=.1,max_mcs=28):
    tx=h.shape[-1];rx=h.shape[-2];choices=[]
    integer(subset_mask,'twoTX-CodebookSubsetRestriction',1,63)
    for rank in range(1,min(tx,rx,rank_limit)+1):
        for pmi in range(1 if tx==1 else 4 if rank==1 else 2):
            bit=pmi if rank==1 else 4+pmi
            if tx==2 and not subset_mask&(1<<bit):continue
            w=precoder(tx,rank,pmi);values=post_mmse_sinr(h,w,power_w,noise_cov)
            report=model.report(values,reference_prbs=1,symbols=symbols,dmrs_re=dmrs_re,target=target,max_mcs=max_mcs,layers=rank)
            score=report['reference_tb_bits']*(1-report.get('estimated_tbler',1.))
            choices.append((score,-rank,float(np.log1p(values).mean()),-pmi,dict(report,ri=rank,pmi=pmi if tx==2 else None)))
    if not choices:raise ValueError('码本子集与端口 / RI 限制没有合法候选')
    return max(choices,key=lambda v:v[:4])[4]
