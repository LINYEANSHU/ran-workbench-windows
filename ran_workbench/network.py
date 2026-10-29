"""Resolved-path multi-cell RF reduction; no native runtime or invented phase.

Ordinary BS transmissions are independent. Cooperation figures are a separate
same-waveform, same-resource experiment, never added to ordinary SINR.
"""
from __future__ import annotations

import cmath
import math


def normalize_network(value=None):
    from .geometry import _number
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('network must be an object')
    result = dict(value)
    for name, default, low, high in [('temperature_k', 290., 1., 500.),
                                     ('noise_figure_db', 7., 0., 40.)]:
        result[name] = _number(result.get(name, default), f'network {name}')
        if not low <= result[name] <= high:
            raise ValueError(f'network {name} must be in [{low}, {high}]')
    return result


def normalize_bs_radio(bs):
    from .geometry import _integer, _number
    bs['channel'] = _integer(bs.get('channel', 0), 'BS channel', 0, 1023)
    for name, default, low, high in [('activity', 1., 0., 1.),
                                     ('phase_deg', 0., -1e6, 1e6),
                                     ('time_offset_ns', 0., -1e6, 1e6)]:
        bs[name] = _number(bs.get(name, default), f'BS {name}')
        if not low <= bs[name] <= high:
            raise ValueError(f'BS {name} must be in [{low}, {high}]')


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def db_sum(values):
    """Log-sum power, keeping tiny interferers independently of the wanted BS."""
    values = [float(v) for v in values if _finite(v)]
    if not values:
        return None
    reference = max(values)
    return reference + 10 * math.log10(math.fsum(10 ** ((v - reference) / 10) for v in values))


def _complex_sum(values):
    return complex(math.fsum(v.real for v in values), math.fsum(v.imag for v in values))


def _carrier_joint(group):
    # Only a known exact intra-link cancellation is a physical zero. Missing
    # channels and noncoherent surface averages carry no synthetic phasor.
    if any(not isinstance(link.get('carrier_phasor'), list) for _, link in group):
        return None, False
    positive = [(bs, link) for bs, link in group if _finite(link.get('coherent_dbm'))]
    if not positive:
        return None, bool(group) and all(link.get('coherent_zero') for _, link in group)
    reference = max(link['coherent_dbm'] for _, link in positive)
    terms = []
    for bs, link in positive:
        phasor = complex(*link['carrier_phasor'])
        terms.append(10 ** ((link['coherent_dbm'] - reference) / 20) * phasor
                     * cmath.exp(1j * math.radians(bs.get('phase_deg', 0))))
    magnitude = abs(_complex_sum(terms))
    return (reference + 20 * math.log10(magnitude), False) if magnitude else (None, True)


def _wideband_joint(group, bandwidth_hz):
    responses = [(bs, link.get('complex_cfr')) for bs, link in group]
    if not responses or any(not isinstance(r, dict) or not _finite(r.get('scale_dbm')) for _, r in responses):
        return None, False
    # A plot CFR cannot establish a band integral. Retain the full normalized
    # CIR on UE links so cross-BS combining uses the same exact integral.
    if any(not isinstance(r.get('cir'),dict) for _,r in responses):
        return None, False
    from .channels import _band_average
    import numpy as np
    reference = max(r['scale_dbm'] for _, r in responses)
    coefficients,delays=[],[]
    for bs,r in responses:
        cir=r['cir']
        coefficient=(np.asarray(cir['real'])+1j*np.asarray(cir['imag']))
        coefficients.extend(coefficient*10**((r['scale_dbm']-reference)/20)*cmath.exp(1j*math.radians(bs.get('phase_deg',0))))
        delays.extend(np.asarray(cir['delays_s'])+bs.get('time_offset_ns',0)*1e-9)
    power = _band_average(np.asarray(coefficients),np.asarray(delays),bandwidth_hz)
    return (reference + 10 * math.log10(power), False) if power else (None, True)


def interference_sources(station, links, project):
    """Per-peer contributions; a missing cochannel path remains unknown."""
    lookup = {link['bs_id']: link for link in links}
    rows = []
    for peer in project['bs']:
        if peer['id'] == station['id']:
            continue
        received = lookup.get(peer['id'], {}).get('incoherent_dbm')
        activity = peer.get('activity', 1.)
        status = ('orthogonal' if peer.get('channel', 0) != station.get('channel', 0)
                  else 'inactive' if activity == 0 else 'cochannel' if _finite(received) else 'unresolved')
        rows.append(dict(bs_id=peer['id'], channel=peer.get('channel', 0), activity=activity,
            received_dbm=received if _finite(received) else None, status=status,
            contribution_dbm=received + 10 * math.log10(activity) if status == 'cochannel' else None))
    return rows


def summarize_network(links, project, include_sources=False):
    settings = project.get('network') or normalize_network()
    noise = 10 * math.log10(1.380649e-23 * settings['temperature_k'] * project['bandwidth_hz']) + 30 + settings['noise_figure_db']
    lookup = {link['bs_id']: link for link in links}
    sources = [(bs, lookup.get(bs['id'], {})) for bs in project['bs']]
    unresolved = [bs['id'] for bs, link in sources if not _finite(link.get('incoherent_dbm'))]
    groups = {}
    for bs, link in sources:
        groups.setdefault(bs.get('channel', 0), []).append((bs, link))
    cooperative = {}
    for channel, group in groups.items():
        powers = [link.get('incoherent_dbm') for _, link in group]
        positive = [v for v in powers if _finite(v)]
        combined = db_sum(positive)
        carrier, carrier_zero = _carrier_joint(group)
        wideband, wideband_zero = _wideband_joint(group, project['bandwidth_hz'])
        carrier_best = max((link['coherent_dbm'] for _, link in group if _finite(link.get('coherent_dbm'))), default=None)
        wideband_best = max((link['wideband_dbm'] for _, link in group if _finite(link.get('wideband_dbm'))), default=None)
        cooperative[channel] = dict(
            cooperative_dbm=combined,
            cooperation_gain_db=combined - max(positive) if combined is not None else None,
            cooperative_snr_db=combined - noise if combined is not None else None,
            carrier_joint_dbm=carrier, carrier_joint_zero=carrier_zero,
            carrier_joint_gain_db=carrier - carrier_best if carrier is not None and carrier_best is not None else None,
            wideband_joint_dbm=wideband, wideband_joint_zero=wideband_zero,
            wideband_joint_gain_db=wideband - wideband_best if wideband is not None and wideband_best is not None else None)
    per_bs = []
    for bs, link in sources:
        channel, wanted = bs.get('channel', 0), link.get('incoherent_dbm')
        interference = db_sum(other.get('incoherent_dbm') + 10 * math.log10(peer.get('activity', 1.))
            for peer, other in groups[channel] if peer['id'] != bs['id']
            and peer.get('activity', 1.) > 0 and _finite(other.get('incoherent_dbm')))
        denominator = db_sum([noise, interference])
        per_bs.append(dict(bs_id=bs['id'], channel=channel,
            signal_dbm=wanted if _finite(wanted) else None, interference_dbm=interference,
            noise_dbm=noise, sinr_db=wanted - denominator if _finite(wanted) else None,
            snr_db=wanted - noise if _finite(wanted) else None,
            unresolved_interferers=[peer['id'] for peer, other in groups[channel]
                if peer['id'] != bs['id'] and peer.get('activity', 1.) > 0
                and not _finite(other.get('incoherent_dbm'))], **cooperative[channel]))
        if include_sources:
            per_bs[-1]['interference_sources'] = interference_sources(bs, links, project)
    best = max((row for row in per_bs if row['signal_dbm'] is not None),
               key=lambda row: row['signal_dbm'], default=None)
    result = dict(best) if best else dict(signal_dbm=None, interference_dbm=None,
        noise_dbm=noise, sinr_db=None, snr_db=None, cooperative_dbm=None,
        cooperation_gain_db=None, carrier_joint_dbm=None, wideband_joint_dbm=None)
    result.update(serving_bs_id=best['bs_id'] if best else None,
                  unresolved_bs_ids=unresolved, per_bs=per_bs,
                  total_received_dbm=db_sum(link.get('incoherent_dbm') for _, link in sources),
                  requested_bs_count=len(sources), resolved_bs_count=len(sources)-len(unresolved))
    return result


def attach_network(result, project):
    result['network_model'] = dict(**project.get('network', normalize_network()),
        calculation_scope='all_project_base_stations', bs_count=len(project['bs']),
        total_power_basis='sum of full configured BS received powers across resources; activity only weights SINR interferers',
        bandwidth_hz=project['bandwidth_hz'], power_basis='incoherent_dbm',
        association='strongest_resolved_received_power',
        interference='independent full-band cochannel signals weighted by interfering BS activity',
        orthogonality='different channel numbers are ideal orthogonal resources; no adjacent-channel leakage',
        cooperation='conditional same-data same-resource full-transmit-power experiment; carrier phase and baseband timing offsets explicit',
        missing='unresolved paths are unknown, not measured zero; SINR uses resolved paths only',
        bs=[{key: bs[key] for key in ('id', 'power_dbm', 'channel', 'activity', 'phase_deg', 'time_offset_ns')} for bs in project['bs']])
    for key in ('points', 'ues'):
        for row in result.get(key, []):
            row['network'] = summarize_network(row.get('links', []), project, include_sources=key=='ues')
    return result
