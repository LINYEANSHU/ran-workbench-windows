# Third-party software

RAN Workbench source is licensed under Apache-2.0. That license does not replace the licenses of bundled dependencies.

| Component | License / upstream |
| --- | --- |
| Python | PSF license; [Python licensing](https://docs.python.org/3/license.html) |
| PyInstaller bootloader | GPL with bootloader exception; [PyInstaller license](https://pyinstaller.org/en/stable/license.html) |
| PySide6 / Qt / Qt WebEngine | LGPL-3.0 / GPL / commercial options; [Qt licensing](https://doc.qt.io/qtforpython-6/licenses.html) |
| CesiumJS | Apache-2.0; [CesiumGS/cesium](https://github.com/CesiumGS/cesium) |
| Sionna RT and NR reference tables | Apache-2.0; [NVlabs/sionna](https://github.com/NVlabs/sionna) |
| Mitsuba 3 / Dr.Jit | BSD-3-Clause; [Mitsuba](https://github.com/mitsuba-renderer/mitsuba3), [Dr.Jit](https://github.com/mitsuba-renderer/drjit) |
| NumPy / SciPy | BSD-3-Clause; [NumPy](https://numpy.org/doc/stable/license.html), [SciPy](https://github.com/scipy/scipy/blob/main/LICENSE.txt) |
| Shapely / GEOS | BSD-3-Clause / LGPL-2.1; [Shapely](https://github.com/shapely/shapely) |
| pyproj / PROJ | MIT-style; [pyproj](https://github.com/pyproj4/pyproj) |
| mapbox-earcut | ISC; [mapbox-earcut-python](https://github.com/skogler/mapbox_earcut_python) |
| Pillow / Matplotlib / pythreejs | Their upstream licenses; copies accompany the binary |

The Windows ZIP retains dependency license files in `THIRD_PARTY_LICENSES/` and package metadata under `_internal/`. Qt libraries remain separate shared libraries. Users may replace compatible LGPL libraries and debug modifications as permitted by their licenses. Qt source and build information are available through the [Qt source archive](https://download.qt.io/official_releases/Qt/) and [Qt for Python source](https://code.qt.io/cgit/pyside/pyside-setup.git/).

The NR lookup tables retain an upstream manifest, exact checksums and license files in `ran_workbench/system/reference/`. Table availability does not establish calibration against commercial equipment. GPU drivers and the NVIDIA CUDA/OptiX platform are provided separately by NVIDIA and are not relicensed by this project.
