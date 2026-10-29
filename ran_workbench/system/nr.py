"""NR numeric procedures. TS 38.214 V18.4.0, tables 1; normal CP only.

The standard defines no universal SINR-to-CQI thresholds or scheduler.
"""
import math

STANDARD = {'release': 18, 'ts': '38.214', 'version': '18.4.0',
            'url': 'https://www.etsi.org/deliver/etsi_ts/138200_138299/138214/18.04.00_60/ts_138214v180400p.pdf'}
_MCS = [(2,r) for r in (120,157,193,251,308,379,449,526,602,679)] + [
    (4,r) for r in (340,378,434,490,553,616,658)] + [
    (6,r) for r in (438,466,517,567,616,666,719,772,822,873,910,948)]
_CQI = [(0,0)] + [(2,r) for r in (78,120,193,308,449,602)] + [
    (4,r) for r in (378,490,616)] + [(6,r) for r in (466,567,666,772,873,948)]
_TBS = (24,32,40,48,56,64,72,80,88,96,104,112,120,128,136,144,152,160,168,
        176,184,192,208,224,240,256,272,288,304,320,336,352,368,384,408,432,
        456,480,504,528,552,576,608,640,672,704,736,768,808,848,888,928,984,
        1032,1064,1128,1160,1192,1224,1256,1288,1320,1352,1416,1480,1544,1608,
        1672,1736,1800,1864,1928,2024,2088,2152,2216,2280,2408,2472,2536,2600,
        2664,2728,2792,2856,2976,3104,3240,3368,3496,3624,3752,3824)


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be an integer in [{low}, {high}]')
    return value


def mcs(index):
    integer(index, 'MCS', 0, 28)
    qm, r = _MCS[index]
    return {'index': index, 'qm': qm, 'rate_x1024': r}


def cqi(index):
    integer(index, 'CQI', 0, 15)
    qm, r = _CQI[index]
    return {'index': index, 'qm': qm, 'rate_x1024': r}


def slot_duration_s(mu):
    return .001 / (2 ** integer(mu, 'numerology', 0, 3))


def transport_block_size(n_prb, symbols, dmrs_re, overhead_re, mcs_index, layers=1):
    """TS 38.214 §5.1.3.2, xOverhead is per PRB, output is bits."""
    integer(n_prb, 'n_prb', 0, 275)
    integer(symbols, 'symbols', 1, 14)
    integer(dmrs_re, 'dmrs_re', 0, 144)
    integer(overhead_re, 'overhead_re', 0, 144)
    integer(layers, 'layers', 1, 8)
    spec = mcs(mcs_index)
    re = min(156, 12 * symbols - dmrs_re - overhead_re)
    if re <= 0:
        raise ValueError('PDSCH must have positive usable RE')
    if n_prb == 0:
        return 0
    rate = spec['rate_x1024'] / 1024
    info = n_prb * re * rate * spec['qm'] * layers
    if info <= 3824:
        n = max(3, math.floor(math.log2(info)) - 6)
        quantized = max(24, (2 ** n) * math.floor(info / (2 ** n)))
        return next(tbs for tbs in _TBS if tbs >= quantized)
    n = math.floor(math.log2(info - 24)) - 5
    quantized = max(3840, (2 ** n) * math.floor((info - 24) / (2 ** n) + .5))
    blocks = math.ceil((quantized + 24) / (3816 if rate <= .25 else 8424)) if rate <= .25 or quantized > 8424 else 1
    return 8 * blocks * math.ceil((quantized + 24) / (8 * blocks)) - 24


def code_block_layout(tb_bits, mcs_index):
    """TS 38.212 §7.2.1 / §5.2.2 LDPC segmentation (no bit encoding)."""
    integer(tb_bits, 'tb_bits', 24, 2**24)
    rate = mcs(mcs_index)['rate_x1024'] / 1024
    bg = 2 if tb_bits <= 292 or (tb_bits <= 3824 and rate <= .67) or rate <= .25 else 1
    b = tb_bits + (16 if tb_bits <= 3824 else 24)
    kcb = 3840 if bg == 2 else 8448
    c = 1 if b <= kcb else math.ceil(b / (kcb - 24))
    return {'base_graph': bg, 'blocks': c, 'cb_bits': math.ceil((b + (24*c if c > 1 else 0)) / c)}
