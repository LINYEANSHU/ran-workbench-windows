"""Reduce a complex CIR without inventing received power or carrier phase.

``beta`` already includes the propagation phase at the carrier (Sionna's
``Paths.cir`` convention). Only frequency *offsets* are used below. Scaling by
the largest component before squaring also preserves valid very weak paths.
This module has no dependency on the native RT runtime or CUDA.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np


def _inputs(beta, delays_s, bandwidth_hz):
    coefficients = np.asarray(beta, dtype=np.complex128).reshape(-1)
    delays = np.asarray(delays_s, dtype=np.float64).reshape(-1)
    if coefficients.shape != delays.shape:
        raise ValueError("CIR coefficients and delays must have equal lengths")
    bandwidth = float(bandwidth_hz)
    if not math.isfinite(bandwidth) or bandwidth < 0:
        raise ValueError("Bandwidth must be a finite non-negative number")
    valid = (np.isfinite(coefficients.real) & np.isfinite(coefficients.imag)
             & np.isfinite(delays) & (delays >= 0)
             & ((coefficients.real != 0) | (coefficients.imag != 0)))
    coefficients, delays = coefficients[valid], delays[valid]
    offsets = np.linspace(-bandwidth / 2, bandwidth / 2, 64)
    if not len(coefficients):
        return coefficients, delays, offsets, 0.0
    # Component scaling, rather than abs(), avoids magnitude overflow too.
    scale = float(max(np.max(np.abs(coefficients.real)), np.max(np.abs(coefficients.imag))))
    return coefficients / scale, delays, offsets, scale


def _db_power(value, scale, reference_db=0.0):
    """The exact linear zero is JSON null (-infinity dB), never a floor."""
    if value <= 0 or scale <= 0:
        return None
    return float(reference_db + 20 * math.log10(scale) + 10 * math.log10(float(value)))


def _offset_response(coefficients, delays, offsets):
    # Subtracting the earliest delay changes only a common phase per offset,
    # not |H|. It avoids loss of accuracy for long absolute travel delays.
    relative_delays = delays - np.min(delays)
    return np.exp(-2j * np.pi * offsets[:, None] * relative_delays[None, :]) @ coefficients


@lru_cache(maxsize=16)
def _integration_rule(order):
    nodes,weights=np.polynomial.legendre.leggauss(order)
    return nodes/2,weights/2


def _quadrature_band_average(coefficients,delays,bandwidth):
    """Span-aware Gaussian integration with convergence and finite work limits."""
    relative=delays-delays[0]
    span=bandwidth*relative[-1]
    if not math.isfinite(span):raise ValueError('Wideband delay span is not finite')
    order=max(16,math.ceil(math.pi*span/2)+16)
    carrier=complex(math.fsum(coefficients.real),math.fsum(coefficients.imag))
    def integral(size):
        offsets,weights=_integration_rule(size)
        response=np.full(size,carrier,dtype=complex)
        # expm1 retains the small difference in nearly cancelling channels.
        # Memory is bounded independently of both the path and frequency count.
        for f in range(0,size,32):
            for p in range(0,len(coefficients),4096):
                delta=np.expm1(-2j*np.pi*(offsets[f:f+32]*bandwidth)[:,None]*relative[None,p:p+4096])
                response[f:f+32]+=delta@coefficients[p:p+4096]
        return float(np.dot(weights,np.abs(response)**2))
    previous=None;work=0
    while order<=2048 and work+order*len(coefficients)<=128_000_000:
        value=integral(order);work+=order*len(coefficients)
        if previous is not None and abs(value-previous)<=1e-8*max(value,previous):return value
        previous=value;order*=2
    raise ValueError('Wideband integral did not converge within its frequency/path work budget')


def _band_average(coefficients, delays, bandwidth):
    """Exact rectangular-band integral, with bounded pairwise storage/work.

    Equal-delay paths are combined before integration. Tiny delay spans use
    sinc(x)-1 directly so a near-null does not subtract two rounded unit powers.
    Large or ill-conditioned CIRs use converged, span-aware quadrature. Exceeding
    its work budget fails explicitly. The plot remains a separate 64-point grid.
    """
    if bandwidth == 0:
        return abs(complex(math.fsum(coefficients.real), math.fsum(coefficients.imag))) ** 2
    order = np.argsort(delays)
    delays, coefficients = delays[order], coefficients[order]
    starts = np.r_[0, np.flatnonzero(np.diff(delays)) + 1]
    ends = np.r_[starts[1:], len(delays)]
    coefficients = np.array([complex(math.fsum(coefficients[a:b].real),
                                     math.fsum(coefficients[a:b].imag)) for a, b in zip(starts, ends)])
    delays = delays[starts]
    keep = coefficients != 0
    coefficients, delays = coefficients[keep], delays[keep]
    if not len(coefficients):
        return 0.
    if len(coefficients) > 8192:
        return _quadrature_band_average(coefficients,delays,bandwidth)
    span = bandwidth * (delays[-1] - delays[0])
    if not math.isfinite(span):
        raise ValueError('Wideband delay span is not finite')
    small_span = span < 1e-3
    carrier = complex(math.fsum(coefficients.real), math.fsum(coefficients.imag))
    terms = [abs(carrier) ** 2 if small_span else float(np.sum(np.abs(coefficients) ** 2))]
    # Only off-diagonal pairs are evaluated. Peak temporary storage is 128².
    for a in range(0, len(coefficients), 128):
        x_coeff = coefficients[a:a + 128]
        x_delay = delays[a:a + 128]
        for b in range(a, len(coefficients), 128):
            y_coeff = coefficients[b:b + 128]
            difference = bandwidth * (x_delay[:, None] - delays[None, b:b + 128])
            if small_span:
                z = (np.pi * difference) ** 2
                kernel = z * (-1 / 6 + z * (1 / 120 - z / 5040))
            else:
                kernel = np.sinc(difference)
            values = 2 * (x_coeff[:, None] * y_coeff.conj()[None, :]).real * kernel
            if a == b:
                values = values[np.triu_indices(len(x_coeff), 1)]
            terms.append(math.fsum(values.flat))
    value = math.fsum(terms)
    # A quadratic form can round to zero/negative or lose relative accuracy
    # near a high-order null. Direct amplitude quadrature avoids subtracting
    # nearly equal integrated powers in that ill-conditioned region.
    if value <= 1e-8*float(np.sum(np.abs(coefficients)**2)):
        return _quadrature_band_average(coefficients,delays,bandwidth)
    return value


def summarize_channels(beta, delays_s, power_dbm, bandwidth_hz):
    """Return incoherent, carrier-coherent and band-integrated received power.

    Invalid/zero paths are omitted; delay spread is weighted by individual
    path power. Exact destructive cancellation has a null dBm value and the
    corresponding ``*_zero`` flag, whereas an absent channel has path_count=0.
    """
    reference = float(power_dbm)
    if not math.isfinite(reference):
        raise ValueError("Transmit power must be finite")
    coefficients, delays, offsets, scale = _inputs(beta, delays_s, bandwidth_hz)
    result = {"incoherent_dbm": None, "coherent_dbm": None, "wideband_dbm": None,
              "path_count": int(len(coefficients)), "rms_delay_ns": None}
    if not len(coefficients):
        return result
    weights = np.abs(coefficients) ** 2
    incoherent = float(np.sum(weights))
    coherent = float(abs(np.sum(coefficients)) ** 2)
    wideband = _band_average(coefficients, delays, float(bandwidth_hz))
    relative = delays - np.min(delays)
    mean_delay = float(np.sum(weights * relative) / incoherent)
    rms = math.sqrt(float(np.sum(weights * (relative - mean_delay) ** 2) / incoherent))
    result.update(incoherent_dbm=_db_power(incoherent, scale, reference),
                  coherent_dbm=_db_power(coherent, scale, reference),
                  wideband_dbm=_db_power(wideband, scale, reference),
                  rms_delay_ns=float(rms * 1e9))
    carrier = np.sum(coefficients)
    magnitude = abs(carrier)
    result['carrier_phasor'] = [float(carrier.real / magnitude), float(carrier.imag / magnitude)] if magnitude else [0., 0.]
    if coherent == 0:
        result["coherent_zero"] = True
    if wideband == 0:
        result["wideband_zero"] = True
    return result


def frequency_response(beta, delays_s, bandwidth_hz):
    """Return the 64 actual frequency offsets and |H(offset)|² in dB."""
    coefficients, delays, offsets, scale = _inputs(beta, delays_s, bandwidth_hz)
    if not len(coefficients):
        gain = [None] * len(offsets)
    else:
        response = _offset_response(coefficients, delays, offsets)
        gain = [_db_power(abs(value) ** 2, scale) for value in response]
    return {"offsets_hz": offsets.tolist(), "gain_db": gain}


def complex_response(beta, delays_s, power_dbm, bandwidth_hz):
    """Complete CFR on a shared absolute time reference for cross-BS sums.

    A per-link earliest-delay subtraction preserves its own magnitude but
    destroys the relative phase between BSs; never use it for this output.
    Scaling avoids underflow without storing a grid-sized complex tensor.
    """
    coefficients, delays, offsets, scale = _inputs(beta, delays_s, bandwidth_hz)
    if scale:
        response = np.exp(-2j * np.pi * offsets[:, None] * delays[None, :]) @ coefficients
        scale_dbm = float(power_dbm) + 20 * math.log10(scale)
    else:
        response = np.zeros(len(offsets), dtype=np.complex128)
        scale_dbm = None
    return dict(offsets_hz=offsets.tolist(), scale_dbm=scale_dbm,
                cir=dict(real=coefficients.real.tolist(),imag=coefficients.imag.tolist(),delays_s=delays.tolist()),
                real=response.real.tolist(), imag=response.imag.tolist())
