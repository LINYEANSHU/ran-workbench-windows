import copy
import json

import pytest

from ran_workbench import model


def test_default_project_has_outdoor_devices_and_independent_copies():
    from ran_workbench.geometry import contains_point

    first = model.default_project()
    second = model.default_project()
    assert first["schema_version"] == 2
    assert len(first["buildings"]) == 6
    assert {item["material"] for item in first["buildings"]} == {"glass", "concrete"}
    for device in first["bs"] + first["ues"]:
        assert not contains_point(first["buildings"], device["position_m"])
    first["buildings"][0]["footprint_m"][0][0] = 999
    assert second["buildings"][0]["footprint_m"][0][0] == -78


def test_validation_normalizes_closed_rings_without_mutating_input():
    project = model.default_project()
    ring = project["buildings"][0]["footprint_m"]
    ring.append(ring[0][:])
    before = copy.deepcopy(project)
    normalized = model.validate_project(project)
    assert len(normalized["buildings"][0]["footprint_m"]) == 4
    assert project == before
    normalized["origin"]["latitude_deg"] = 0
    assert project["origin"]["latitude_deg"] == 22.295


def test_legacy_project_cannot_reenable_diffraction_in_face_only_mode():
    project = model.default_project()
    project['solver']['diffraction'] = True
    normalized = model.validate_project(project)
    assert 'diffraction' not in normalized['solver']
    assert project['solver']['diffraction'] is True


def test_removed_diffraction_setting_is_not_saved_or_exposed(tmp_path):
    project = model.default_project()
    assert 'diffraction' not in project['solver']
    project['solver']['diffraction'] = True  # Legacy full projects still load.
    target = tmp_path / 'migrated.ran.json'
    model.save_project(project, target)
    assert 'diffraction' not in model.load_project(target)['solver']
    assert 'diffraction' not in json.loads(target.read_text(encoding='utf-8'))['solver']


@pytest.mark.parametrize("group", ["buildings", "bs", "ues"])
def test_validation_rejects_ids_duplicated_across_groups(group):
    project = model.default_project()
    project[group][0]["id"] = project["buildings"][1]["id"]
    with pytest.raises(ValueError, match="[Ii][Dd]|unique|duplicate"):
        model.validate_project(project)


@pytest.mark.parametrize("field,value", [("frequency_hz", float("nan")), ("bandwidth_hz", 0), ("frequency_hz", True)])
def test_validation_rejects_bad_radio_values(field, value):
    project = model.default_project()
    project[field] = value
    with pytest.raises(ValueError):
        model.validate_project(project)


def test_validation_rejects_nonfinite_metadata():
    project = model.default_project()
    project["buildings"][0]["source"]["raw_height"] = float("inf")
    with pytest.raises(ValueError, match="finite"):
        model.validate_project(project)


@pytest.mark.parametrize("position", [[-60, 22, 1.5], [-78, 22, 42], [0, -20, -0.1]])
def test_validation_rejects_devices_inside_or_on_buildings_and_below_ground(position):
    project = model.default_project()
    project["ues"][0]["position_m"] = position
    with pytest.raises(ValueError, match="UE|ground|building"):
        model.validate_project(project)
    assert project["ues"][0]["position_m"] == position


@pytest.mark.parametrize("group,limit", [("bs", 5), ("ues", 64)])
def test_validation_enforces_device_count_caps(group, limit):
    project = model.default_project()
    template = project[group][0]
    project[group] = [dict(copy.deepcopy(template), id=f"device-{i}") for i in range(limit + 1)]
    with pytest.raises(ValueError, match="5|64|count|maximum"):
        model.validate_project(project)


def test_validation_defaults_material_and_keeps_thickness_separate_from_height():
    project = model.default_project()
    building = project["buildings"][0]
    del building["material"]
    del building["thickness_m"]
    normalized = model.validate_project(project)
    assert normalized["buildings"][0]["material"] == "concrete"
    assert normalized["buildings"][0]["thickness_m"] == 0.2
    assert normalized["buildings"][0]["height_m"] == 42


@pytest.mark.parametrize("field,maximum", [("max_depth", 10), ("samples_per_src", 10_000_000), ("batch_size", 1024)])
def test_validation_rejects_solver_work_above_caps(field, maximum):
    project = model.default_project()
    project["solver"][field] = maximum + 1
    with pytest.raises(ValueError, match=field):
        model.validate_project(project)


@pytest.mark.parametrize("field,maximum", [("max_depth", 10), ("samples_per_src", 10_000_000), ("batch_size", 1024)])
def test_validation_accepts_solver_work_at_caps(field, maximum):
    project = model.default_project()
    project["solver"][field] = maximum
    assert model.validate_project(project)["solver"][field] == maximum


def test_validation_accepts_one_source_sample_for_analytical_cases():
    project = model.default_project()
    project["solver"]["samples_per_src"] = 1
    assert model.validate_project(project)["solver"]["samples_per_src"] == 1


def test_fingerprint_canonicalizes_key_order_and_detects_physical_edits():
    project = model.default_project()
    reordered = dict(reversed(list(project.items())))
    assert model.project_fingerprint(project) == model.project_fingerprint(reordered)
    reordered["buildings"][0]["height_m"] += 1
    assert model.project_fingerprint(project) != model.project_fingerprint(model.default_project())
    assert len(model.project_fingerprint(project)) == 64


def test_project_round_trip_and_invalid_save_preserve_previous_file(tmp_path):
    path = tmp_path / "街区.json"
    project = model.default_project()
    model.save_project(project, path)
    assert model.load_project(path) == model.validate_project(project)
    previous = path.read_bytes()
    project["sampling"]["spacing_m"][2] = 0
    with pytest.raises(ValueError):
        model.save_project(project, path)
    assert path.read_bytes() == previous
    assert "合成街区" in path.read_text(encoding="utf-8")


def test_atomic_replace_failure_does_not_destroy_existing_project(tmp_path, monkeypatch):
    path = tmp_path / "project.json"
    original = model.default_project()
    model.save_project(original, path)
    previous = path.read_bytes()

    def fail_replace(*args):
        raise OSError("simulated file replacement failure")

    monkeypatch.setattr(model.os, "replace", fail_replace)
    changed = copy.deepcopy(original)
    changed["name"] = "另一个项目"
    with pytest.raises(OSError):
        model.save_project(changed, path)
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("text", ['{"schema_version": NaN}', '{"name":"one","name":"two"}', '[]'])
def test_load_rejects_invalid_json_payloads(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        model.load_project(path)
