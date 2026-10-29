"""Published Sionna NR reference curves with explicit applicability reporting.

NumPy linear interpolation deliberately differs from Sionna's default spline.
The upstream historical EESM beta lacks calibration provenance; we expose that.
"""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from .nr import cqi, mcs, code_block_layout, transport_block_size


@lru_cache(maxsize=1)
def _reference():
    root = Path(__file__).with_name('reference')
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        if hashlib.sha256((root/item['path']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('NR reference checksum mismatch: '+item['path'])
    tables = json.loads((root/'PDSCH_table1.json').read_text())['category']['1']['index']['1']['MCS']
    betas = json.loads((root/'eesm_beta_table.json').read_text())['index']['1']
    return manifest, tables, betas


class LinkModel:
    def __init__(self):
        self.manifest, self.tables, self.betas = _reference()
        self.supported_mcs = sorted(map(int, self.tables))

    def effective_sinr(self, sinr_linear, mcs_index):
        mcs(mcs_index)
        values = np.asarray(sinr_linear, dtype=float).ravel()
        if not len(values) or not np.isfinite(values).all() or np.any(values < 0):
            raise ValueError('SINR must be a nonempty finite nonnegative array')
        # Zero is an allocated erased resource; it must not be excluded.
        beta = self.betas[mcs_index]
        minimum = float(np.min(values))
        return max(0., minimum-beta*math.log(float(np.exp(-(values-minimum)/beta).mean())))

    def tbler(self, sinr_linear, mcs_index, tb_bits):
        if mcs_index not in self.supported_mcs:
            raise ValueError(f'No published PDSCH curve for MCS {mcs_index}; available: 3..28')
        layout = code_block_layout(tb_bits, mcs_index)
        table = self.tables[str(mcs_index)]
        sizes = sorted(map(int, table['CBS']))
        k = layout['cb_bits']
        if not sizes[0] <= k <= sizes[-1]:
            raise ValueError(f'CBS {k} outside published range {sizes[0]}..{sizes[-1]}; reduce grant/TBS')
        eff = self.effective_sinr(sinr_linear, mcs_index)
        db = 10*math.log10(eff) if eff > 0 else None
        snrs = table['SNR_db']
        low = max(s for s in sizes if s <= k)
        high = min(s for s in sizes if s >= k)
        extrapolated = db is None or db < snrs[0] or db > snrs[-1]
        def probability(size):
            values = table['CBS'][str(size)]['BLER']
            if db is None or db < snrs[0]:
                return 1.  # declared conservative outage below the sampled domain
            return float(np.interp(db, snrs, values))
        a, b = probability(low), probability(high)
        bler = a if high == low else a+(b-a)*(k-low)/(high-low)
        return dict(tbler=float(1-(1-bler)**layout['blocks']), bler=bler,
                    sinr_db=db, **layout, extrapolated=extrapolated,
                    extrapolation_policy='below:outage; above:hold-last-sampled-BLER',
                    model='Sionna-NR-AWGN-reference/linear-CBS-interpolation',
                    reference_commit=self.manifest['commit'],
                    beta_provenance='upstream_historical_unverified',
                    calibrated=False, reason='Reference curves; EESM provenance and deployment/receiver calibration not established')

    def report(self, sinr_linear, reference_prbs=1, symbols=12, dmrs_re=12, target=.1, max_mcs=28, layers=1):
        """Implementation-specific CQI policy, tested against target reference TBLER.

        Candidate MCS rates cannot exceed the reported CQI's nominal efficiency.
        Unsupported low-rate CQIs result in CQI=0, not invented performance.
        """
        selected = {'cqi': 0, 'mcs': 3, 'reference_tb_bits': 0, 'target_tbler': target}
        performance = {}
        for idx in self.supported_mcs:
            if idx > max_mcs:
                continue
            bits = transport_block_size(reference_prbs, symbols, dmrs_re, 0, idx, layers)
            try:
                stats = self.tbler(sinr_linear, idx, bits)
            except ValueError:
                continue
            if stats['tbler'] <= target:
                performance[idx] = (bits, stats)
        for index in range(1,16):
            q = cqi(index)
            # Closest same-modulation MCS for the reference CQI; this mapping
            # is our declared adaptation policy, not an extra 3GPP table.
            idx = min((j for j in range(29) if mcs(j)['qm'] == q['qm']),
                      key=lambda j: abs(mcs(j)['rate_x1024']-q['rate_x1024']))
            if idx in performance:
                bits, stats = performance[idx]
                selected = dict(cqi=index, mcs=idx, reference_tb_bits=bits,
                                target_tbler=target, estimated_tbler=stats['tbler'])
        return selected
