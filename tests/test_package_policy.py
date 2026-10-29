from tools.package_policy import runtime_resource_required


def test_release_omits_only_webengine_debug_and_unshipped_locale_payloads():
    assert not runtime_resource_required('PySide6/resources/qtwebengine_devtools_resources.debug.pak')
    assert not runtime_resource_required('PySide6/translations/qtwebengine_locales/de.pak')
    assert not runtime_resource_required('PySide6/translations/qtwebengine_locales/fr.pak')


def test_essential_runtime_compute_and_chinese_english_locales_are_kept():
    for path in ['PySide6/resources/qtwebengine_resources.pak',
                 'PySide6/resources/qtwebengine_devtools_resources.pak',
                 'PySide6/resources/icudtl.dat',
                 'PySide6/translations/qtwebengine_locales/en-US.pak',
                 'PySide6/translations/qtwebengine_locales/zh-CN.pak',
                 'PySide6/translations/qtwebengine_locales/zh-TW.pak',
                 'PySide6/QtWebEngineProcess.exe', 'PySide6/opengl32sw.dll',
                 'mitsuba/cuda_ad_mono_polarized.pyd', 'mitsuba/embree3.dll',
                 'THIRD_PARTY_LICENSES/LICENSE', 'resources/cesium/Workers/createGeometry.js',
                 'my_model/qtwebengine_devtools_resources.debug.pak']:
        assert runtime_resource_required(path), path
