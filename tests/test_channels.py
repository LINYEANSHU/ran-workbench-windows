"""Independent checks for physical channel reduction, without initializing CUDA."""
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import numpy as np
import pytest

from ran_workbench.channels import frequency_response, summarize_channels


def test_equal_in_phase_paths_are_four_p_coherent_and_two_p_incoherent():
    result = summarize_channels([1 + 0j, 1 + 0j], [0, 0], 0, 20e6)
    assert result["path_count"] == 2
    assert result["incoherent_dbm"] == pytest.approx(3.01029995664)
    assert result["coherent_dbm"] == pytest.approx(6.02059991328)
    assert result["wideband_dbm"] == pytest.approx(6.02059991328)
    assert result["rms_delay_ns"] == 0


def test_equal_opposite_paths_cancel_without_inventing_a_power_floor():
    result = summarize_channels([1 + 0j, -1 + 0j], [100e-9, 100e-9], 30, 20e6)
    assert result["path_count"] == 2
    assert result["incoherent_dbm"] == pytest.approx(33.01029995664)
    assert result["coherent_dbm"] is None
    assert result["wideband_dbm"] is None
    assert result["coherent_zero"] is True
    assert result["wideband_zero"] is True
    json.dumps(result, allow_nan=False)


def test_complex_coefficients_already_contain_carrier_phase():
    # A quarter-cycle phase difference is kept exactly as supplied in the CIR.
    result = summarize_channels([1 + 0j, 0 + 1j], [100e-9, 100e-9], 0, 20e6)
    assert result["coherent_dbm"] == pytest.approx(3.01029995664)
    assert result["wideband_dbm"] == pytest.approx(3.01029995664)


def test_wideband_integral_is_separate_from_the_64_plot_samples():
    result = summarize_channels([1, 1], [0, 1 / 64e6], 0, 63e6)
    assert result["coherent_dbm"] == pytest.approx(6.02059991328)
    assert result["wideband_dbm"] == pytest.approx(10 * math.log10(2 + 2 * math.sin(math.pi * 63 / 64) / (math.pi * 63 / 64)))
    response = frequency_response([1, 1], [0, 1 / 64e6], 63e6)
    assert len(response["offsets_hz"]) == 64
    assert len(response["gain_db"]) == 64
    assert response["offsets_hz"][0] == -31_500_000
    assert response["offsets_hz"][-1] == 31_500_000
    # At f=0.5 MHz, |1 + exp(-j*pi/64)|^2 = 2 + 2 cos(pi/64).
    assert response["gain_db"][32] == pytest.approx(10 * math.log10(2 + 2 * math.cos(math.pi / 64)))


def test_wideband_alias_counterexample_has_the_analytic_band_average():
    result = summarize_channels([1e-5, 1e-5], [0, 3150e-9], 30, 20e6)
    assert result['wideband_dbm'] == pytest.approx(-66.98970004336)


@pytest.mark.parametrize('bandwidth,delay', [(20e6, 3147e-9), (1e6, 120e-9), (80e6, 0.7e-6)])
def test_three_path_band_integral_matches_independent_dense_quadrature(bandwidth, delay):
    coefficients = np.array([1, -.7 + .2j, .1j])
    delays = np.array([0, delay, delay * 1.78])
    frequencies = np.linspace(-bandwidth / 2, bandwidth / 2, 100001)
    power = np.abs(np.exp(-2j * np.pi * frequencies[:, None] * delays) @ coefficients) ** 2
    expected = 10 * math.log10(np.trapezoid(power, frequencies) / bandwidth)
    assert summarize_channels(coefficients, delays, 0, bandwidth)['wideband_dbm'] == pytest.approx(expected, abs=2e-7)


def test_nearly_cancelling_paths_keep_small_positive_band_power():
    # For opposite paths sinc expansion gives 2*(1-sinc(x)) ~ pi²*x²/3.
    result = summarize_channels([1, -1], [0, 1e-20], 0, 1e6)
    expected = 10 * math.log10(math.pi ** 2 * 1e-28 / 3)
    assert result['wideband_dbm'] == pytest.approx(expected, abs=1e-10)


def test_large_cir_equal_delay_cancellation_and_zero_bandwidth():
    beta = np.tile([1., -1.], 3000)
    assert summarize_channels(beta, np.zeros(6000), 0, 20e6)['wideband_zero']
    result = summarize_channels([1, 1j, -1], [0, 1e-6, 1e-5], 0, 0)
    assert result['wideband_dbm'] == pytest.approx(0)


def test_large_distinct_delay_cir_has_unit_average_power_per_path():
    result = summarize_channels(np.ones(1024), np.arange(1024) * 1e-6, 0, 20e6)
    assert result['wideband_dbm'] == pytest.approx(30.1029995664, abs=1e-9)


def test_large_diffuse_cir_uses_converged_integration_without_quadratic_work():
    count=9000;spacing=1e-12;bandwidth=20e6
    frequencies=np.linspace(-bandwidth/2,bandwidth/2,100001)
    # Independent geometric-series magnitude, continuously extended at zero.
    response=count*np.sinc(frequencies*count*spacing)/np.sinc(frequencies*spacing)
    expected=10*math.log10(np.trapezoid(response**2,frequencies)/bandwidth)
    actual=summarize_channels(np.ones(count),np.arange(count)*spacing,0,bandwidth)
    assert actual['wideband_dbm']==pytest.approx(expected,abs=1e-8)


def test_higher_order_close_delay_cancellation_is_positive_not_a_false_null():
    frequencies=np.linspace(-.5e6,.5e6,100001)
    expected=10*math.log10(np.trapezoid((2*np.sin(np.pi*frequencies*1e-9))**6,frequencies)/1e6)
    actual=summarize_channels([1,-3,3,-1],[0,1e-9,2e-9,3e-9],0,1e6)
    assert actual['wideband_dbm']==pytest.approx(expected,abs=1e-7)
    assert not actual.get('wideband_zero',False)


def test_excessive_integration_work_fails_explicitly_without_an_alias_estimate():
    with pytest.raises(ValueError,match='work budget'):
        summarize_channels(np.ones(9000),np.arange(9000)/20e6,0,20e6)


def test_delay_spread_is_weighted_by_path_power():
    result = summarize_channels([1, math.sqrt(3)], [0, 10e-9], 0, 20e6)
    assert result["rms_delay_ns"] == pytest.approx(4.330127018922193)


@pytest.mark.parametrize("beta,delays", [([], []), ([0, 0], [0, 1e-9]), ([complex(float('nan'), 0)], [0]), ([1], [-1])])
def test_no_valid_positive_path_has_null_metrics(beta, delays):
    result = summarize_channels(beta, delays, 30, 20e6)
    assert result["path_count"] == 0
    for field in ("incoherent_dbm", "coherent_dbm", "wideband_dbm", "rms_delay_ns"):
        assert result[field] is None
    assert not result.get("coherent_zero", False)
    json.dumps(result, allow_nan=False)


def test_positive_tiny_coefficients_remain_real_power_without_a_floor():
    result = summarize_channels([1e-200 + 0j], [1e-7], 30, 20e6)
    assert result["path_count"] == 1
    assert result["incoherent_dbm"] == pytest.approx(-3970)
    assert result["coherent_dbm"] == pytest.approx(-3970)
    assert result["wideband_dbm"] == pytest.approx(-3970)


def test_invalid_entries_do_not_poison_valid_paths():
    result = summarize_channels([1e-4, 1, complex(float('inf'), 0), 1], [1e-7, float('nan'), 0, -1], 30, 20e6)
    assert result["path_count"] == 1
    assert result["incoherent_dbm"] == pytest.approx(-50)
    assert result["rms_delay_ns"] == 0


def test_mismatched_inputs_and_invalid_bandwidth_are_explicit_errors():
    with pytest.raises(ValueError):
        summarize_channels([1, 2], [0], 30, 20e6)
    with pytest.raises(ValueError):
        summarize_channels([1], [0], 30, -1)
    with pytest.raises(ValueError):
        summarize_channels([1], [0], float('nan'), 20e6)


def test_desktop_engine_import_does_not_initialize_native_rt_runtime():
    source = ("import sys; import ran_workbench.engine; import ran_workbench.worker; "
              "assert not {'sionna.rt', 'mitsuba', 'drjit'}.intersection(sys.modules)")
    completed = subprocess.run([sys.executable, "-c", source], cwd=Path(__file__).resolve().parents[1],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_engine_rejects_unknown_mode_before_loading_cuda():
    from ran_workbench.engine import simulate
    with pytest.raises(ValueError, match="mode"):
        simulate({}, mode="unknown")


def test_worker_invalid_request_emits_json_error_and_preserves_old_result(tmp_path, capsys):
    from ran_workbench.worker import main
    request = tmp_path / "request.json"
    output = tmp_path / "result.json"
    request.write_text(json.dumps({"project": {}, "mode": "unknown"}), encoding="utf-8")
    output.write_text('{"old":true}', encoding="utf-8")
    assert main(["--input", str(request), "--output", str(output)]) == 1
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["type"] == "error"
    assert "mode" in events[-1]["message"]
    assert json.loads(output.read_text(encoding="utf-8")) == {"old": True}


def test_native_scene_staging_can_write_and_cleanup_in_windows_sandbox_temp():
    from ran_workbench.engine import _staged_scene
    root = Path(tempfile.gettempdir()) / f"ran-staging-test-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        with _staged_scene(root) as folder:
            mesh = folder / "probe.ply"
            mesh.write_text("probe", encoding="ascii")
            assert mesh.read_text(encoding="ascii") == "probe"
        assert not folder.exists()
    finally:
        root.rmdir()

