"""Conservative release-only WebEngine resource policy; never trims compute libraries."""
from pathlib import PurePosixPath


def runtime_resource_required(path):
    name = str(PurePosixPath(str(path).replace('\\', '/'))).lower()
    if name == 'pyside6/resources/qtwebengine_devtools_resources.debug.pak':
        return False
    prefix = 'pyside6/translations/qtwebengine_locales/'
    if name.startswith(prefix) and name.endswith('.pak'):
        return name[len(prefix):] in {'en-us.pak', 'zh-cn.pak', 'zh-tw.pak'}
    return True
