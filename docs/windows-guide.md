# Windows guide

[中文](windows-guide.zh-CN.md) · [Home](../README.md)

## First calculation

Start in Workbench with the built-in synthetic scene. Adjust building geometry or BS/UE positions in the left panels. Place devices outside buildings, above or at ground level. Start with a small sampling area and low ray count; choose UE calculation to inspect multipath and channel response before requesting a larger coverage grid. Actual propagation requires a compatible NVIDIA GPU and driver.

The coverage view supports power and network SINR, including resolved co-channel interference. Cooperative modes are conditional experiments; they require the documented synchronization assumptions. View settings do not change the underlying scene. Geometry and radio changes invalidate previous results.

## Files and workspaces

| File | Use |
| --- | --- |
| `.ran.json` | Workbench project: geometry, deployments and solver settings. Save results separately. |
| `.ran-sim.json` | Strategy experiment: scene snapshot, graph, parameters and resource settings. |
| `.city.json` | Modeling manifest (`ran-city-1`, `rf_supported:false`) referencing your own local city assets. |
| `.geojson` | Building import using WGS84 or Hong Kong 1980 Grid. Provide appropriate height/base fields and check material assumptions. |

No model files are shipped. The generated synthetic blocks are a software demonstration, not a surveyed city. GeoJSON height fields do not automatically establish a vertical datum or material measurement.

Modeling displays local assets and does not convert them into Workbench propagation geometry. Strategy initially copies the Workbench scene. Updating from Workbench creates a new experiment and resets its graph and results; save the old experiment first if needed. Draft and running-job guards provide Save, Discard and Cancel choices.

## Strategy modes

The default real channel uses CUDA propagation. The explicitly labelled synthetic demonstration (`reference_fixture`) is for examining the strategy loop without city propagation. It is not an RF substitute. Resource settings, seed, scene, mobility and time must match for a meaningful A/B comparison.

The system model covers a subset of NR Release 18. Inspect the in-app scope notes before interpreting throughput, latency or feedback cost; the link abstraction and reference curves have calibration limitations.

## Language and automation

Use the native **Language / 语言** menu. Language changes update loaded editors without reloading. New editors inherit the selected language. User-provided names and paths remain unchanged. To choose a language at startup, use `--language en` or `--language zh-CN`.

`Ctrl+S` saves the active editable workspace. Modeling is read-only; use its screenshot command instead. Switching workspaces preserves open editors and does not cancel computation.

Run `RANWorkbench.exe --cli --help` for the local control CLI and `RANWorkbench.exe --system-cli --help` for standalone experiments. Local automation records include a private token; do not share or commit them. Use `--no-automation` when local automation is not needed.

## Build and release

Build from a clean Windows Python 3.12 environment and the locked npm dependencies. Keep the output folder intact. The software uses dynamically loaded libraries and bundled Qt WebEngine assets. Replacing or removing `_internal` prevents normal operation.

`tools/publish_source.py` creates a source allowlist export with no Git history. `tools/verify_public_release.py` checks the Windows distribution for private model file types and required licenses. Release ZIPs belong in GitHub Releases rather than Git history. Review both artifacts before publication.
