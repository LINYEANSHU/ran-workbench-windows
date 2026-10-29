"""Independent reference vectors for the NR subset (TS 38.214 Rel-18)."""
import math
import pytest


def test_nr_tables_and_slot_units():
    from ran_workbench.system.nr import mcs, cqi, slot_duration_s
    assert mcs(0) == {'index': 0, 'qm': 2, 'rate_x1024': 120}
    assert mcs(28)['rate_x1024'] == 948
    assert cqi(0)['qm'] == 0
    assert cqi(15)['qm'] == 6
    assert slot_duration_s(3) == .000125
    for value in [29, -1, True, 1.5]:
        with pytest.raises(ValueError): mcs(value)


@pytest.mark.parametrize('prbs,mcs_index,expected', [(1, 0, 32), (1, 3, 72), (10, 0, 368), (10, 28, 8712), (100, 0, 3752)])
def test_tbs_reference_vectors(prbs, mcs_index, expected):
    # 14 PDSCH symbols, 12 DMRS RE/PRB, N_RE=min(156,168-12).
    from ran_workbench.system.nr import transport_block_size
    assert transport_block_size(prbs, 14, 12, 0, mcs_index) == expected


def test_no_resource_has_no_transport_block():
    from ran_workbench.system.nr import transport_block_size
    assert transport_block_size(0, 14, 12, 0, 3) == 0
    with pytest.raises(ValueError): transport_block_size(1, 1, 12, 0, 3)


def test_bler_uses_published_reference_and_preserves_zero_sinr():
    from ran_workbench.system.link import LinkModel
    model = LinkModel()
    assert model.tbler([0, 0], 3, 72)['tbler'] == 1
    low = model.tbler([.1, .1], 3, 72)
    high = model.tbler([10, 10], 3, 72)
    assert low['tbler'] > high['tbler']
    assert high['reference_commit'] == '2f0a3820269b53fd022780a6f2f5f720f7ceb9b4'
    assert high['beta_provenance'] == 'upstream_historical_unverified'
    with pytest.raises(ValueError, match='MCS'): model.tbler([1], 0, 32)


def test_bler_rejects_uncovered_cbs_and_invalid_sinr():
    from ran_workbench.system.link import LinkModel
    model = LinkModel()
    with pytest.raises(ValueError, match='CBS'): model.tbler([10], 3, 4000)
    with pytest.raises(ValueError): model.tbler([math.nan], 3, 72)
    # Uniform SINR must be preserved by EESM, including resource erasures.
    assert model.effective_sinr([5, 5], 10) == pytest.approx(5)
    assert model.effective_sinr([0, 5], 10) < 5


def test_cqi_is_selected_from_link_performance_not_a_fabricated_threshold():
    from ran_workbench.system.link import LinkModel
    model = LinkModel()
    poor = model.report([0], reference_prbs=1, symbols=12, dmrs_re=12)
    good = model.report([100], reference_prbs=1, symbols=12, dmrs_re=12)
    assert poor['cqi'] == 0
    assert good['cqi'] > poor['cqi']
    assert 3 <= good['mcs'] <= 28
