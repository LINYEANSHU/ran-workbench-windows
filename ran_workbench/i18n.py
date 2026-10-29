"""Presentation-only translations and a shared Qt/WebEngine language preference.

Scene data, experiment drafts and automation identifiers never pass through this
module. Unknown strings are retained verbatim, including user supplied names.
"""
import json
import weakref

from PySide6.QtCore import QObject, QSettings, QTranslator, QLibraryInfo, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEngineScript

LANGUAGES = ('zh-CN', 'en')
_language = 'zh-CN'

EN = {
    'Modeling': 'Modeling', 'Strategy': 'Strategy', 'Workbench': 'Workbench',
    '保存': 'Save', '保存项目': 'Save project', '保存实验': 'Save experiment',
    'RAN Workbench — 城市三维无线传播': 'RAN Workbench — 3D urban radio propagation',
    'NR 策略仿真': 'NR strategy simulation',
    '当前场景有未保存修改，是否先保存？': 'This scene has unsaved changes. Save first?',
    '保存 RAN 项目': 'Save RAN project', 'RAN 项目 (*.ran.json);;JSON (*.json)': 'RAN project (*.ran.json);;JSON (*.json)',
    'RAN 项目 (*.ran.json *.json)': 'RAN project (*.ran.json *.json)',
    '导入建筑轮廓（创建新场景并重置部署）': 'Import building footprints (create a new scene and reset deployment)',
    '请先完成覆盖或 UE 计算': 'Complete a coverage or UE calculation first',
    '导出传播结果': 'Export propagation results', '打开 RAN 项目': 'Open RAN project',
    '未知操作': 'Unknown action', '三维界面尚未就绪': 'The 3D view is not ready',
    '界面操作等待超时': 'The view operation timed out',
    '策略实验仍在运行；退出须指定 cancel_running:true': 'The experiment is running; specify cancel_running:true to close',
    '策略实验有未保存更改；请保存或显式放弃': 'The experiment has unsaved changes; save or explicitly discard them',
    '计算已取消': 'Calculation cancelled', '截图路径必须以 .png 结尾': 'The screenshot path must end in .png',
    '截图已存在；覆盖须指定 overwrite:true': 'The screenshot exists; specify overwrite:true to replace it',
    '三维界面尚未渲染完成': 'The 3D view has not finished rendering',
    '无法保存应用截图，请检查目标目录': 'Could not save the application screenshot; check the destination directory',
    '打开城市模型': 'Open city model', '打开城市模型…': 'Open city model…',
    '选择已有的 .city.json 清单，在这里检查城市几何。\n模型按需载入；当前精细网格用于查看，尚未参与传播计算。':
        'Select an existing .city.json manifest to inspect city geometry here.\nModels load on demand. Detailed meshes are used for viewing and do not yet participate in propagation calculations.',
    '在 Workbench 场景中放置至少一个 BS 和 UE 后，进入策略实验。':
        'Place at least one BS and UE in the Workbench scene to start a strategy experiment.',
    '打开已有策略实验…': 'Open existing strategy experiment…',
    '从 Workbench 更新场景': 'Update scene from Workbench',
    '建立当前 Workbench 场景的新实验；保存、放弃或取消现有编辑':
        'Create an experiment from the current Workbench scene; save, discard or cancel existing edits',
    '未知工作区': 'Unknown workspace',
    '。请在 Workbench 调整场景，或打开已有策略实验。': '. Adjust the scene in Workbench or open an existing experiment.',
    '退出': 'Exit', '传播计算正在进行，取消并退出？': 'Propagation calculation is running. Cancel it and exit?',
    '计算仍在运行': 'Calculation is still running', '请先取消传播计算并保存，再退出。': 'Cancel the propagation calculation and save before exiting.',
    '策略实验正在运行，请先取消或等待完成': 'The strategy experiment is running. Cancel it or wait for completion',
    '更新实验场景': 'Update experiment scene', '更新将建立一个新实验。先保存当前实验？': 'Updating creates a new experiment. Save the current experiment first?',
    '城市模型 (*.city.json)': 'City model (*.city.json)', '城市模型未能打开': 'Could not open city model',
    '项目未保存': 'Project not saved',
    '城市模型为只读；可在模型视图中保存截图': 'City models are read-only; save a screenshot from the model view',
    '运行中': 'Running', ' · 运行中': ' · Running', '场景副本 · 不自动回写': 'Scene copy · No automatic write-back',
    '模型查看': 'Model viewer', '本机场景与传播': 'Local scene and propagation', 'Modeling — 城市模型': 'Modeling — City model',
    'data 必须是对象': 'data must be an object', 'overwrite 需要布尔值': 'overwrite must be a boolean',
    '窗口尺寸范围为 1100..5120 × 720..2880': 'Window dimensions must be within 1100..5120 × 720..2880',
    '需要新的 .png 路径或显式 overwrite:true': 'Provide a new .png path or explicit overwrite:true',
    '无法保存截图': 'Could not save screenshot', '未知工作区操作': 'Unknown workspace action',
    '未保存的实验': 'Unsaved experiment', '保存当前实验后再继续？': 'Save the current experiment before continuing?',
    '打开 NR 策略实验': 'Open NR strategy experiment', 'NR 实验 (*.ran-sim.json *.json)': 'NR experiment (*.ran-sim.json *.json)',
    '导出实验结果': 'Export experiment results', '保存 NR 策略实验': 'Save NR strategy experiment',
    '退出需保存 / 显式放弃更改，并明确取消运行': 'Save or explicitly discard changes and cancel the running task before closing',
    '未知 NR 宿主操作': 'Unknown NR host action', '退出策略实验': 'Exit strategy experiment',
    '实验正在运行，取消并退出？': 'The experiment is running. Cancel it and exit?',
    '未保存实验': 'Unsaved experiment', '保存更改后退出？': 'Save changes before exiting?',
    '实验仍在运行': 'Experiment is still running', '请先取消实验并保存，再退出。': 'Cancel the experiment and save before exiting.',
    '实验未保存': 'Experiment not saved', '请检查草稿后重试': 'Check the draft and try again',
    '实验需要至少一个 BS 和一个 UE': 'The experiment requires at least one BS and one UE',
    '实验需要 BS 和 UE': 'The experiment requires a BS and UE',
    '当前计算尚未完成': 'The current calculation is still running', '实验正在运行': 'The experiment is running',
    '请先取消运行': 'Cancel the running task first', '有未保存的更改': 'There are unsaved changes',
    '计算进行中，请先取消或等待完成': 'A calculation is running. Cancel it or wait for completion',
    '未知计算模式': 'Unknown calculation mode', '请至少部署一个 BS': 'Deploy at least one BS',
    '请至少部署一个 UE': 'Deploy at least one UE',
    '实验正在运行；请先取消或等待完成': 'The experiment is running. Cancel it or wait for completion',
    '需要明确文件 path': 'Provide an explicit file path',
    '草稿的原修订已过期；请保留草稿内容或放弃草稿并读取最新配置':
        'The draft is based on an outdated revision. Keep its content or discard it and load the latest configuration',
    '未知策略操作': 'Unknown strategy action',
    '实验修订已变化，请重新读取后再编辑': 'The experiment revision changed. Load it again before editing',
    '旧草稿不能覆盖外部更新；请读取最新配置': 'An outdated draft cannot overwrite external updates. Load the latest configuration',
    '草稿需要原始 base_revision': 'The draft requires its original base_revision',
    '草稿需要实验对象': 'The draft requires an experiment object',
    '实验有未保存更改 / 草稿；先保存或显式放弃': 'The experiment has unsaved changes or drafts. Save or explicitly discard them first',
    '请先完成实验': 'Complete an experiment first',
    '请先应用或放弃未应用草稿，再运行实验': 'Apply or discard the unapplied draft before running the experiment',
    '先完成基准实验 A': 'Complete baseline experiment A first',
    '需要基准 A 和当前已完成的实验 B': 'A baseline A and a completed current experiment B are required',
    'A/B 需要相同场景、随机种子、时间与物理资源 / 解码配置':
        'A/B comparison requires the same scene, random seed, time, physical resources and decoder configuration',
    'A/B 需要相同轨迹和信道快照配置': 'A/B comparison requires the same trajectory and channel snapshot configuration',
    '需要有效的城市模型清单；此工作区只读取已有模型数据。':
        'A valid city model manifest is required. This workspace reads existing model data.',
    '城市清单缺少坐标、来源、统计或完整性信息': 'The city manifest is missing coordinates, provenance, statistics or completeness information',
    '城市渲染经纬度超出有效范围': 'City render coordinates are outside the valid longitude/latitude range',
    '城市模型清单没有可显示的几何': 'The city model manifest contains no displayable geometry',
    '城市模型资源路径或分区无效': 'A city model resource path or partition is invalid',
    '城市单体模型的图幅或等级无效': 'A city building model has an invalid tile or level',
}
ZH = {'Modeling': '建模', 'Strategy': '策略', 'Workbench': '工作台'}


def normalize_language(language):
    value = str(language or '').replace('_', '-').lower()
    return 'en' if value == 'en' or value.startswith('en-') else 'zh-CN'


def t(source, language=None):
    """Translate a known presentation string without touching arbitrary content."""
    return (EN if normalize_language(language or _language) == 'en' else ZH).get(source, source)


tr = t


class LanguageManager(QObject):
    changed = Signal(str)

    def __init__(self, profile=None, language=None, settings=None):
        super().__init__(profile)
        self.profile = profile
        self.settings = settings if settings is not None else QSettings('RANWorkbench', 'RANWorkbench')
        self.pages = weakref.WeakSet()
        self.script = None
        self.qt_translator = None
        self.language = normalize_language(language if language is not None else self.settings.value('ui/language', 'zh-CN'))
        self.set_language(self.language, persist=language is not None)

    def t(self, source):
        return t(source, self.language)

    def register_page(self, page):
        self.pages.add(page)

    def set_language(self, language, persist=True):
        global _language
        self.language = _language = normalize_language(language)
        if persist:
            self.settings.setValue('ui/language', self.language)
            self.settings.sync()
        if self.profile is not None:
            scripts = self.profile.scripts()
            if self.script is not None:
                scripts.remove(self.script)
            self.script = QWebEngineScript()
            self.script.setName('ran-ui-language')
            self.script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            self.script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            self.script.setRunsOnSubFrames(False)
            self.script.setSourceCode('window.RAN_LANGUAGE = ' + json.dumps(self.language) + ';')
            scripts.insert(self.script)
        app = QApplication.instance()
        if app:
            if self.qt_translator is not None:
                app.removeTranslator(self.qt_translator)
                self.qt_translator.deleteLater()
            self.qt_translator = QTranslator(self)
            if self.qt_translator.load('qtbase_' + ('zh_CN' if self.language == 'zh-CN' else 'en'),
                                       QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
                app.installTranslator(self.qt_translator)
        encoded = json.dumps(self.language)
        for page in list(self.pages):
            try:
                page.runJavaScript(f'window.RAN_LANGUAGE = {encoded}; window.RANI18n?.setLanguage({encoded});')
            except RuntimeError:  # A page can have been deleted by Qt before its wrapper is collected.
                self.pages.discard(page)
        self.changed.emit(self.language)


def get_manager(profile, language=None, settings=None):
    manager = getattr(profile, '_ran_i18n', None)
    if manager is None:
        manager = profile._ran_i18n = LanguageManager(profile, language, settings)
    elif language is not None:
        manager.set_language(language)
    return manager


def install_language_menu(window, manager):
    """Use the same menu in standalone hosts and the containing workspace frame."""
    window.language_menu = window.menuBar().addMenu('Language / 语言')
    group = QActionGroup(window)
    group.setExclusive(True)
    window.language_actions = {}
    for language, label in [('zh-CN', '简体中文'), ('en', 'English')]:
        action = QAction(label, window)
        action.setCheckable(True)
        action.setChecked(language == manager.language)
        action.triggered.connect(lambda _checked=False, code=language: manager.set_language(code))
        group.addAction(action)
        window.language_menu.addAction(action)
        window.language_actions[language] = action
    def update(language):
        window.language_actions[language].setChecked(True)
    manager.changed.connect(update)
    window.language_action_group = group
