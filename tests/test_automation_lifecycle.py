"""File publication and queued UI mutation races; no desktop input or GPU."""
from pathlib import Path
from types import SimpleNamespace


def test_concurrent_screenshots_cannot_clobber_without_overwrite(tmp_path, monkeypatch):
    from ran_workbench.desktop import QTimer, Window
    callbacks = []
    monkeypatch.setattr(QTimer, 'singleShot', lambda _ms, callback: callbacks.append(callback))
    class Pixmap:
        def __init__(self, value): self.value = value
        def isNull(self): return False
        def save(self, path, _format): Path(path).write_bytes(self.value); return True
        def width(self): return 100
        def height(self): return 100
    images = iter([Pixmap(b'first image'), Pixmap(b'second image')])
    window = SimpleNamespace(
        bridge=SimpleNamespace(api=SimpleNamespace(_path=lambda data: str(Path(data['path']).resolve()))),
        page=SimpleNamespace(runJavaScript=lambda _script, callback: callback(True)),
        view=SimpleNamespace(grab=lambda: next(images)))
    target = tmp_path / 'same.png'
    first, second = [], []
    Window.capture_view(window, {'path': str(target)}, first.append)
    Window.capture_view(window, {'path': str(target)}, second.append)
    for callback in callbacks: callback()
    assert first[0]['ok'] is True
    assert second[0]['ok'] is False and second[0]['error_code'] == 'FILE_EXISTS'
    assert target.read_bytes() == b'first image'
    assert [p.name for p in tmp_path.iterdir()] == ['same.png']
