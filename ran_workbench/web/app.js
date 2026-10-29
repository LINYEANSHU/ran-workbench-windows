/* Offline Qt workbench. ENU metres are the single source for geometry and results. */
(function () {
  'use strict';
  const uiBindings = new WeakMap();
  const uiBindingNodes = new Set();
  let uiCleanupQueued = false;
  const tr = (source, ...args) => window.RANI18n.t(source, ...args);
  const uiString = value => window.RANI18n.rendered(value);
  function prunePresentationBindings() {
    uiCleanupQueued = false;
    for (const reference of uiBindingNodes) {
      const element = reference.deref();
      if (!element || !element.isConnected || !uiBindings.get(element)?.size) {
        if (element) uiBindings.delete(element);
        uiBindingNodes.delete(reference);
      }
    }
  }
  function bindPresentation(element, key, render) {
    let bindings = uiBindings.get(element);
    if (!bindings) { bindings = new Map(); uiBindings.set(element, bindings); uiBindingNodes.add(new WeakRef(element)); }
    bindings.set(key, render); render();
    // New nodes are often bound before being appended. Sweep after the render
    // turn, while weak references ensure detached nodes never retain results.
    if (!uiCleanupQueued) { uiCleanupQueued = true; Promise.resolve().then(prunePresentationBindings); }
  }
  function localizedText(element, factory, preserveUserText = true) {
    element.setAttribute('data-i18n-ignore', '');
    let ownedNode, initialized = false;
    bindPresentation(element, 'text', () => {
      // A later renderer may replace a scalar caption with structured details.
      // Its old caption must not overwrite those children on a language change.
      if (initialized && element.firstChild !== ownedNode) {
        uiBindings.get(element)?.delete('text'); return;
      }
      const value = factory();
      element.textContent = preserveUserText ? value : window.RANI18n.rendered(value);
      ownedNode = element.firstChild; initialized = true;
    });
  }
  function localizedAttribute(element, name, factory) {
    bindPresentation(element, name, () => element.setAttribute(name, window.RANI18n.rendered(factory())));
  }
  window.RANI18n.onChange(() => {
    for (const reference of [...uiBindingNodes]) {
      const element = reference.deref();
      if (!element || !element.isConnected) { if (element) uiBindings.delete(element); uiBindingNodes.delete(reference); continue; }
      const bindings = uiBindings.get(element);
      if (!bindings) continue;
      for (const render of bindings.values()) render();
    }
  });
  const C = window.Cesium;
  const $ = id => document.getElementById(id);
  const clone = value => JSON.parse(JSON.stringify(value));
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const format = (value, digits = 2) => finite(value) ? value.toLocaleString(window.RANI18n.language, { maximumFractionDigits: digits }) : '—';
  const coord = p => `E ${format(p[0])} / N ${format(p[1])} / U ${format(p[2])} m`;
  const powerNames = { incoherent_dbm: '路径功率非相干和', coherent_dbm: '载频相干功率', wideband_dbm: '带宽平均功率' };
  const networkNames = {sinr_db:'全网 SINR（含同频干扰）',interference_dbm:'同信道干扰功率',total_received_dbm:'全站已解析接收功率和（含干扰）',snr_db:'单站 SNR',cooperative_dbm:'协作功率和（条件试验）',cooperation_gain_db:'协作功率合并增益',carrier_joint_dbm:'同步载频相干功率'};
  let coverageMetricExplicit = false;
  const kindNames = { los: '直达', specular: '镜面反射', diffuse: '漫反射', transmission: '透射', mixed: '混合交互' };
  const pathColors = { los: '#59a77f', specular: '#c9b054', diffuse: '#a372c9', transmission: '#d98548', mixed: '#c95e82' };
  const rampHex = ['#25354e', '#265c69', '#2d8076', '#59a77f', '#c9b054', '#e67e50'];

  const PARAM_HELP = {
    'project-name': { title: '项目名称', tag: '基本信息', desc: '工程标识名称，随项目保存并展示于顶栏与导出记录。', meta: '标称文本，上限 120 字符。', impact: '仅修改显示标签，不影响物理电波传播计算。' },
    'frequency': { title: '载波频率 (Carrier Frequency)', tag: '电磁参数', desc: '仿真射频载波中心频率。自由空间损耗、波长及材料复介电常数均基于此频率。', meta: '输入单位：GHz；存储/计算单位：Hz。', impact: '频率越高波长越短，空间损耗与外墙吸收衰减增大，多径干涉条纹更密集。' },
    'bandwidth': { title: '信道带宽 (Bandwidth)', tag: '信道参数', desc: '用于计算信道频率响应 (CFR) 与带内多载波平均接收功率的信道频带宽度。', meta: '输入单位：MHz；存储/计算单位：Hz。', impact: '决定带内频率响应曲线采样区间，平滑多径造成的频率选择性深度衰落。' },
    'longitude': { title: '局部原点经度', tag: '大地基准', desc: '局部 ENU (East-North-Up) 笛卡尔坐标系原点 (0, 0, 0) 所处的大地经度。', meta: '单位：度 (°)；坐标基准：WGS84 椭球体 (EPSG:4326)。', impact: '改变场景在大地坐标系中的绝对经度，不影响场景内相对几何与多径计算。' },
    'latitude': { title: '局部原点纬度', tag: '大地基准', desc: '局部 ENU 坐标系原点所处的大地纬度。', meta: '单位：度 (°)；坐标基准：WGS84 椭球体 (EPSG:4326)。', impact: '改变场景大地绝对纬度，不影响局部相对几何。' },
    'origin-height': { title: '局部原点椭球高', tag: '高程基准', desc: '局部 ENU 原点相对于 WGS84 椭球面的高程。地面 U=0 平面以此为基准。', meta: '单位：米 (m)；基准：WGS84 椭球面高（非香港主水平基准 HKPD 标高）。', impact: '决定场景大地垂直基准，不改变场景内部物体相对高度。' },
    'import-crs': { title: '源数据坐标系', tag: '数据导入', desc: '导入 GeoJSON 矢量建筑轮廓的原始投影或地理坐标系。', meta: '支持 WGS84 (EPSG:4326) 与香港 1980 网格 (EPSG:2326)。', impact: '导入时自动完成坐标反算并重投影为局部 ENU 米制坐标。' },
    'height-field': { title: '建筑高度字段名', tag: '属性映射', desc: 'GeoJSON 要素属性中用于读取建筑物拔地净高度的字段名。', meta: '默认 height；匹配不到时采用下方默认高度。', impact: '正确匹配可保留真实建筑高度；否则回退为统一标称高度。' },
    'base-field': { title: '底面高度字段名', tag: '属性映射', desc: 'GeoJSON 要素属性中读取建筑物底面起算高度的字段名。', meta: '留空取 0m；不自动进行 HKPD 垂直基准转换。', impact: '决定建筑物底面离地垂直间隙。' },
    'default-height': { title: '缺失高度默认值', tag: '几何默认', desc: '当导入矢量缺失高度属性或高度值无效时赋予建筑的标称高度。', meta: '单位：米 (m)；须大于 0.01m。', impact: '直接改变无高度属性建筑的拉伸高度与遮挡区域。' },
    'building-picker': { title: '选择编辑建筑', tag: '场景拾取', desc: '从当前场景建筑列表中选取一栋建筑物以查看或修改其几何与电磁参数。', meta: '标识符格式：B-01, B-02 等。', impact: '三维视图中高亮选中建筑物轮廓，右下角展示其几何与材质参数。' },
    'building-name': { title: '建筑名称', tag: '几何标识', desc: '建筑物的显示名称，用于界面列表与导出报表标记。', meta: '上限 120 字符。', impact: '仅修改显示标签，不改变几何形状或电磁属性。' },
    'building-east': { title: '建筑中心 E 坐标', tag: '局部平移', desc: '建筑物底面外轮廓几何中心在局部 ENU 中的东向 (East) 位置。', meta: '单位：米 (m)；局部右手 ENU 笛卡尔坐标。', impact: '沿东西向平移建筑整体（含内部孔洞），改变视距遮挡与反射面位置。' },
    'building-north': { title: '建筑中心 N 坐标', tag: '局部平移', desc: '建筑物底面外轮廓几何中心在局部 ENU 中的北向 (North) 位置。', meta: '单位：米 (m)；局部右手 ENU 笛卡尔坐标。', impact: '沿南北向平移建筑整体，改变电波遮挡与反射面相对角度。' },
    'building-width': { title: '建筑平面宽 (Width)', tag: '尺寸缩放', desc: '建筑物沿自身局部主轴方向的水平包络宽度。', meta: '单位：米 (m)；须大于 0.01m。', impact: '按比例水平缩放建筑底面顶点，改变建筑外墙跨度与阴影区大小。' },
    'building-depth': { title: '建筑平面深 (Depth)', tag: '尺寸缩放', desc: '建筑物沿自身局部次轴方向的水平包络深度。', meta: '单位：米 (m)；须大于 0.01m。', impact: '按比例水平缩放建筑底面纵深，改变侧墙反射面积与遮挡深度。' },
    'building-rotation': { title: '建筑平面旋转角', tag: '平面朝向', desc: '建筑轮廓绕其底面几何中心逆时针旋转的角度。', meta: '单位：度 (°)；以东向为 0°，向北逆时针旋转为正。', impact: '改变外立面法向量朝向，直接决定镜面反射出射方向与阴影边界。' },
    'building-base': { title: '建筑底面高度 (Base U)', tag: '底面标高', desc: '建筑物底部下表面相对于局部地面 U=0 平面的垂直高度。', meta: '单位：米 (m)；须大于或等于 0m。', impact: '接地通常设为 0m；大于 0m 时建筑悬空，底部空隙允许电波穿过。' },
    'building-height': { title: '建筑高度 (Height)', tag: '垂直拉伸', desc: '建筑物从底面到屋顶的垂直净高度。', meta: '单位：米 (m)；与外墙薄层厚度独立。', impact: '决定反射立面、屋顶高度和视距阻挡范围。' },
    'building-thickness': { title: '材料层厚度 (Wall Thickness)', tag: '薄层透射', desc: '外墙物理等效介质厚度，专用于 ITU-R P.2040 薄介质板透射衰减计算。', meta: '单位：米 (m)；默认 0.2m；与建筑平面尺寸完全独立！', impact: '决定射线穿透单层墙体的衰减损耗；透射保持沿原方向直线穿出，无内部折射角弯折。' },
    'building-material': { title: '假定电磁材质', tag: '电磁参数', desc: '赋予建筑外墙与屋顶的理论电磁介质模型（ITU 经验公式）。', meta: '支持 ITU 混凝土与 ITU 玻璃；为理论假定值，未经现场实测校准。', impact: '决定材料复介电常数与电导率，进而控制反射率、透射衰减与吸收损耗。' },
    'building-delete': { title: '删除建筑', tag: '几何编辑', desc: '从当前场景中永久移除当前选中的建筑物。', meta: '快捷操作；移除后将重新构建三维碰撞网格。', impact: '彻底移除该建筑阻挡与反射；使现有计算结果立即失效。' },
    'create-shape': { title: '新建筑轮廓类型', tag: '建模方式', desc: '选择在场景中交互式绘制任意多边形，或生成规则圆柱形建筑。', meta: '选项：多边形绘制 / 圆形（48 段多边形离散）。', impact: '圆形建筑将自动离散为 48 边形以满足射线追踪平面三角面片求交。' },
    'circle-east': { title: '圆心 E 坐标', tag: '圆柱中心', desc: '待创建圆形建筑几何中心在局部 ENU 中的东向位置。', meta: '单位：米 (m)。', impact: '确定圆形建筑在场景中的东西位置。' },
    'circle-north': { title: '圆心 N 坐标', tag: '圆柱中心', desc: '待创建圆形建筑几何中心在局部 ENU 中的北向位置。', meta: '单位：米 (m)。', impact: '确定圆形建筑在场景中的南北位置。' },
    'circle-radius': { title: '圆形建筑半径', tag: '几何尺寸', desc: '待创建圆形建筑底面的外接几何半径。', meta: '单位：米 (m)；须大于 0.01m。', impact: '决定圆柱建筑的水平截面大小与反射弧面覆盖范围。' },
    'create-height': { title: '新建筑高度', tag: '垂直拉伸', desc: '待创建建筑物从底面到顶部的拔地净高度。', meta: '单位：米 (m)；须大于 0.01m。', impact: '决定新建建筑的立面高度与顶部棱边高度。' },
    'create-base': { title: '新建筑底面 U', tag: '底面标高', desc: '待创建建筑物底面相对于局部地面 U=0 的垂直高度。', meta: '单位：米 (m)；须大于或等于 0m。', impact: '通常置于地面 0m。' },
    'create-thickness': { title: '新建筑材料层厚度', tag: '薄层参数', desc: '待创建建筑外墙的等效介质薄层厚度。', meta: '单位：米 (m)；默认 0.2m；与建筑尺寸完全独立。', impact: '用于计算薄板透射衰减损耗。' },
    'create-material': { title: '新建筑假定材质', tag: '电磁参数', desc: '新建建筑所赋予的理论电磁材质。', meta: '支持 ITU 混凝土与 ITU 玻璃；理论假定值。', impact: '决定反射损耗与穿透损耗特性。' },
    'create-building': { title: '创建建筑操作', tag: '建模动作', desc: '根据所选轮廓类型在场景中添加新建筑物，或进入多边形地面绘制状态。', meta: '多边形需至少绘制 3 个点。', impact: '生成新建筑实体并使既有计算结果失效。' },
    'finish-drawing': { title: '完成多边形绘制', tag: '绘制控制', desc: '闭合当前在地面上绘制的所有顶点，完成新建筑物创建。', meta: '至少需要 3 个非共线地面顶点。', impact: '退出绘制状态，在场景中生成新建筑实体。' },
    'undo-drawing': { title: '撤销上一个顶点', tag: '绘制控制', desc: '按倒序撤销刚刚在地面上点击添加的最新顶点。', meta: '实时更新已绘顶点数。', impact: '撤回手误点选的顶点。' },
    'cancel-drawing': { title: '取消绘制', tag: '绘制控制', desc: '放弃当前正在绘制的折线，退出绘制状态。', meta: '快捷键：Esc。', impact: '清空已绘制顶点，不产生新实体。' },
    'device-kind': { title: '设备类型', tag: '设备分类', desc: '切换查看或配置基站 (BS) 或用户终端 (UE)。', meta: '上限：5 个基站 BS，64 个用户终端 UE。', impact: '基站具备发射功率与天线朝向；终端作为接收采样点。' },
    'device-name': { title: '设备名称', tag: '设备标识', desc: '设备的显示名称。', meta: '上限 120 字符。', impact: '用于界面列表、图例及链路多径表中的标识展示。' },
    'device-east': { title: '设备 E 坐标', tag: '空间位置', desc: '设备天线在局部 ENU 坐标系下的东向 (East) 位置。', meta: '单位：米 (m)；必须位于所有建筑物外部！', impact: '改变设备水平位置及与建筑物、对端设备的相对几何视距。' },
    'device-north': { title: '设备 N 坐标', tag: '空间位置', desc: '设备天线在局部 ENU 坐标系下的北向 (North) 位置。', meta: '单位：米 (m)；必须位于所有建筑物外部！', impact: '改变设备平面位置及反射角、入射视线。' },
    'device-up': { title: '设备 U 坐标 (天线高度)', tag: '垂直标高', desc: '设备天线相位中心离地面的垂直高度。', meta: '单位：米 (m)；必须大于或等于 0m（地面）。', impact: '直接决定天线垂直俯仰视角、视距直达阻挡及地面反射点位置。' },
    'bs-power': { title: '基站发射功率 (Tx Power)', tag: '射频参数', desc: '基站天线端口输入功率 (Antenna-port power)，非等效全向辐射功率 (EIRP)。', meta: '单位：dBm；当前模型采用单天线各向同性天线。', impact: '按比例等额提升/降低该基站所有路径的接收功率（线性缩放）。' },
    'bs-yaw': { title: '天线偏航角 (Yaw)', tag: '天线姿态', desc: '基站天线水平方位朝向角，绕局部 U 轴逆时针旋转。', meta: '单位：度 (°)；姿态随项目保存。', impact: '控制发射天线空间极化基向量；姿态随项目保存并影响天线空间极化耦合。' },
    'bs-pitch': { title: '天线俯仰角 (Pitch)', tag: '天线姿态', desc: '基站天线垂直仰角/下倾角，绕横轴旋转。', meta: '单位：度 (°)。', impact: '控制天线垂直俯仰，改变极化基向量方向与天线空间极化耦合。' },
    'bs-roll': { title: '天线滚转角 (Roll)', tag: '天线姿态', desc: '基站天线极化滚转角。', meta: '单位：度 (°)。', impact: '控制天线横滚，改变极化基向量方向与天线空间极化耦合。' },
    'new-device': { title: '新增设备', tag: '部署操作', desc: '准备部署新的基站或终端，自动分配下一个可用 ID。', meta: '基站上限 5 个，终端上限 64 个。', impact: '清空表单进入新设备录入状态。' },
    'apply-device': { title: '应用设备修改', tag: '部署操作', desc: '将表单中的设备坐标与参数保存至项目。', meta: '必须位于建筑物外部。', impact: '更新场景中设备位置与高度杆，使现有计算结果失效。' },
    'place-device': { title: '在场景中放置', tag: '交互部署', desc: '进入场景交互放置模式，直接在地面点击拾取设备的水平位置。', meta: '高度沿用表单输入的 U 坐标；十字光标提示。', impact: '直观快速调整基站或终端的部署位置。' },
    'delete-device': { title: '删除设备', tag: '部署操作', desc: '从场景中移除当前选中的设备。', meta: '不可撤销操作。', impact: '移除该设备及其所有关联链路；使现有结果失效。' },

    'sampling-mode-grid': { title: '笛卡尔空间体网格', tag: '采样模式', desc: '在三维直角空间长方体内按 E/N/U 轴向等距采样。适合街区全景场强分布评估。', meta: '支持 3 轴独立间距；点数上限 250,000。', impact: '切换至经典 Cartesian 离散网格；计算结果包含相干与宽带平均功率。' },
    'sampling-mode-sphere': { title: '扩展球面测地网格', tag: '采样模式', desc: '以基站或指定原点为球心生成等距多层二十面体测地球面。每面片为物理接收积分单元。', meta: 'Sionna RadioMapSolver 原生支持；每球 20×4^级 三角形。', impact: '切换至 GPU 密集球面射频表面；结果为各向同性面积平均非相干功率。' },
    'surface-center-mode': { title: '球心定位方式', tag: '球面几何', desc: '决定球面测量簇的中心坐标来源：绑定基站或手工指定局部 ENU 坐标。', meta: '选项：跟随基站 / 手动输入 ENU。', impact: '跟随基站时球心随该基站坐标联动更新；手工模式支持任意室外空间点。' },
    'surface-center-bs': { title: '球心绑定基站', tag: '关联基站', desc: '选择作为球面展开中心的基站。球心将严格对准该基站天线相位中心。', meta: '随所选基站位置实时联动。', impact: '变更绑定基站将移动整体同心球簇空间位置。' },
    'surface-center-e': { title: '球心 E 坐标', tag: '空间位置', desc: '手动指定测量球面几何中心在局部 ENU 坐标中的东向位置。', meta: '单位：米 (m)。', impact: '改变球面在场景中的东西向绝对位置。' },
    'surface-center-n': { title: '球心 N 坐标', tag: '空间位置', desc: '手动指定测量球面几何中心在局部 ENU 坐标中的北向位置。', meta: '单位：米 (m)。', impact: '改变球面在场景中的南北向绝对位置。' },
    'surface-center-u': { title: '球心 U 坐标', tag: '空间位置', desc: '手动指定测量球面几何中心在局部 ENU 坐标中的垂直标高。必须大于等于 0。', meta: '单位：米 (m)；须大于等于 0m。', impact: '改变球面离地垂直高度。' },
    'surface-radii': { title: '展开半径序列', tag: '球面尺寸', desc: '以逗号分隔的扩展球面半径列表。正数、升序、唯一，最多支持 16 个半径。', meta: '单位：米 (m)；例如 25, 50, 75, 100。', impact: '按半径由近及远求解空间能量球面扩散与建筑阻挡衰减包络。' },
    'preset-radii-dense': { title: '近场精细半径预设', tag: '半径预设', desc: '将半径序列设置为 15, 30, 45, 60 m，专注于基站近场与相邻建筑边缘分析。', meta: '4 层球壳；适合微小区近距离场强分析。', impact: '更新表单半径并重新计算请求面片总数。' },
    'preset-radii-standard': { title: '标准街区半径预设', tag: '半径预设', desc: '将半径序列设置为 25, 50, 75, 100 m，标准街区典型覆盖分析配置。', meta: '4 层球壳；平衡计算速度与覆盖半径。', impact: '更新表单半径并刷新估算。' },
    'preset-radii-wide': { title: '开阔覆盖半径预设', tag: '半径预设', desc: '将半径序列设置为 50, 100, 150, 200 m，适合中远距离视距与宏站广域覆盖。', meta: '4 层球壳；范围覆盖至 200m。', impact: '更新表单半径并刷新估算。' },
    'surface-subdivisions': { title: '测地细分等级 (Subdivisions)', tag: '空间角分辨率', desc: '正二十面体测地球面的三角形细分递归层数。每增加一级，面片数变为 4 倍。', meta: '范围：0..6；每球 20×4^level 面片（3级对应 1280 面片/球）。', impact: '等级越高球面角分辨率越精细，计算面片数翻倍增加。总面片上限 250,000。' },
    'surface-replicates': { title: '独立重复样本数 (Replicates)', tag: '蒙特卡洛方差', desc: '采用不同随机种子独立求解的完整轮次。用于评估蒙特卡洛光线抽样的数值波动。', meta: '范围：1..8；默认 2 次。', impact: '线性倍增发射总射线数与计算时间，提供抽样方差估计。' },
    'surface-chunk-size': { title: 'GPU 射线分块上限 (Ray Chunk Size)', tag: '显存调度', desc: '每轮 GPU 求解的射线数量上限。总射线数较大时分块发射并按实际射线数加权累加。', meta: '范围：1024..2,000,000；默认 1,000,000。', impact: '控制单轮射线状态和显存开销。复杂场景或显存不足仍可能导致计算失败，可尝试减小分块。' },

    'sample-min-e': { title: '采样 E 下界', tag: '点云范围', desc: '三维点云采样在东向 (East) 的最小边界坐标。', meta: '单位：米 (m)；局部 ENU。', impact: '决定三维采样立方体的西侧物理边界。' },
    'sample-max-e': { title: '采样 E 上界', tag: '点云范围', desc: '三维点云采样在东向 (East) 的最大边界坐标。', meta: '单位：米 (m)；须大于或等于下界。', impact: '决定三维采样立方体的东侧物理边界。' },
    'sample-step-e': { title: '采样 E 间距', tag: '空间分辨率', desc: '三维网格在东向相邻两点的物理空间距离。', meta: '单位：米 (m)；与接收批大小完全独立！', impact: '间距越小空间点云越密集，点数呈反比增加；与批大小无关。' },
    'sample-min-n': { title: '采样 N 下界', tag: '点云范围', desc: '三维点云采样在北向 (North) 的最小边界坐标。', meta: '单位：米 (m)；局部 ENU。', impact: '决定三维采样立方体的南侧物理边界。' },
    'sample-max-n': { title: '采样 N 上界', tag: '点云范围', desc: '三维点云采样在北向 (North) 的最大边界坐标。', meta: '单位：米 (m)；须大于或等于下界。', impact: '决定南北向空间采样分辨率。' },
    'sample-step-n': { title: '采样 N 间距', tag: '空间分辨率', desc: '三维网格在北向相邻两点的物理空间距离。', meta: '单位：米 (m)；与接收批大小完全独立！', impact: '决定南北向空间采样分辨率。' },
    'sample-min-u': { title: '采样 U 下界', tag: '点云范围', desc: '三维点云采样在天向 (Up) 的最低标高。', meta: '单位：米 (m)；须大于或等于 0m（地面）。', impact: '通常设为近地面天线高度（如 1.5m）。' },
    'sample-max-u': { title: '采样 U 上界', tag: '点云范围', desc: '三维点云采样在天向 (Up) 的最高标高。', meta: '单位：米 (m)；须大于或等于下界。', impact: '决定立体空间采样的最高楼层截断高度。' },
    'sample-step-u': { title: '采样 U 间距', tag: '空间分辨率', desc: '三维网格在天向垂直层间的高度采样间隔。', meta: '单位：米 (m)；与接收批大小完全独立！', impact: '较小间距可密集观察不同楼层立面场强的立体分层。' },
    'preset-grid-20': { title: '20m 稀疏网格预设', tag: '间距预设', desc: '将 E、N、U 三轴网格间距快速设为 20m。点数较少，计算迅速。', meta: '仅更改表单输入并刷新点数预览；点击“应用采样”才保存。', impact: '用于宏观街区覆盖粗测与全景快速评估。' },
    'preset-grid-10': { title: '10m 标准网格预设', tag: '间距预设', desc: '将 E、N、U 三轴网格间距设为 10m（系统标准默认推荐值）。', meta: '仅更改表单输入并刷新点数预览；点击“应用采样”才保存。', impact: '均衡覆盖分辨率与 GPU 计算开销的标准街区采样方案。' },
    'preset-grid-5': { title: '5m 精细网格预设', tag: '间距预设', desc: '将 E、N、U 三轴网格间距设为 5m。采样更为密实，点数约为 10m 标准网格的 8 倍。', meta: '仅更改表单输入并刷新点数预览；点击“应用采样”才保存。', impact: '将三轴间距缩减至 5m 密集空间采样网格，点数约为 10m 标准网格的 8 倍。' },

    'solver-los': { title: '直达径计算 (LoS)', tag: '物理机制', desc: '发射机到接收机视距直达光线的传播计算开关。', meta: '自由空间 Friis 衰减模型。', impact: '关闭后视距直达径不计入接收功率，仅计算已启用的反射和透射路径。' },
    'solver-specular': { title: '镜面反射 (Specular)', tag: '物理机制', desc: '光滑建筑物外墙及地面反射计算开关，遵循入射角等于反射角反射定律。', meta: '基于 SBR 射线弹跳结合镜像法精确求解。', impact: '关闭后忽略镜面反射径，外墙强反射回波消失。' },
    'solver-diffuse': { title: '漫反射散射 (Diffuse)', tag: '物理机制', desc: '粗糙表面蒙特卡洛随机漫散射计算开关。', meta: '折点遵循粗糙面散射概率密度抽样，不受镜面反射镜像定律约束。', impact: '开启后产生随机散射多径；需配合较高每源射线数以保证能量积分收敛。' },
    'solver-refraction': { title: '薄层透射 (Transmission)', tag: '物理机制', desc: '电磁波穿透建筑物薄外墙的传播计算开关。', meta: '薄板近似：沿原方向直线穿出，无内部折射角弯折！', impact: '开启后射线可穿透外墙进入或穿出建筑物内部。' },
    'solver-depth': { title: '最大交互深度 (Max Depth)', tag: '求解深度', desc: '光线到达接收端前允许经历的最大反射/透射累计总阶数。', meta: '取值范围：0..10；默认 3。', impact: '增加深度会增加路径状态、折点与计算开销。' },
    'solver-samples': { title: '每源射线数 (Samples per Source)', tag: '光线密度', desc: 'Sionna RT 发射端射出的蒙特卡洛随机采样射线总数。', meta: '范围：1..10,000,000；默认 1,000,000。', impact: '射线越多，狭窄缝隙捕获率越高，漫反射能量收敛越平稳，但计算耗时线性增加。' },
    'solver-batch': { title: '分批点数 (Batch Size)', tag: '显存调度', desc: 'GPU 单次并发求解的接收点数。批量决定计算调度与显存开销，不改变点云物理密度。', meta: '范围：1..1024；默认 256。候选缓冲饱和时后端会自动折半缩批重算。', impact: '改变批量不改变网格点数、发射射线数或物理模型。' },
    'solver-seed': { title: '随机数种子 (Seed)', tag: '可复现性', desc: '伪随机数发生器种子，决定 SBR 发射射线与漫反射角度抽样的初始状态。', meta: '非负整数。固定种子可提升对比可重复性。', impact: '固定种子可提升对比可重复性，但不保证多线程位级严格一致。' },
    'solver-scattering': { title: '漫反射散射系数 (S)', tag: '粗糙度参数', desc: '反射总功率中漫反射所占比例为 S²；S 为电场散射系数。', meta: '范围：0..1；S=0.1 对应漫反射功率仅占反射波的 1% (S²=0.01)。', impact: 'S 增大将降低镜面反射能量并增加漫反射随机离散散射分量。' },

    'surface-display-mode': { title: '球面呈现形式', tag: '视图表现', desc: '在三维场景中以连续彩色三角网格面片渲染球面，或仅绘制各面片的空间几何质心点。', meta: '选项：彩色面片表面 (Mesh) / 质心空间点云 (Points)。', impact: '面片模式展示真实的能量积分积分面；点云模式减少几何重叠。' },
    'display-radius': { title: '聚焦球面半径', tag: '切片观察', desc: '选择观察特定半径计算完成的单层扩展球面，或同时展示所有半径的同心扩展球面。', meta: '基于已完成计算结果筛选，无需重新求解。', impact: '聚焦观察单层球面的反射阴影与电磁包络。' },
    'radius-play-btn': { title: '播放/暂停扩展动画', tag: '波前演变', desc: '按半径从小到大循环切换显示已计算的球面层，直观演示无线电波前随距离扩展过程。', meta: '仅轮播已计算结果；拖动或播放不发起任何后台求解。', impact: '启动或暂停球面扩展演示动画。' },
    'radius-slider': { title: '半径选择滑块', tag: '交互切片', desc: '通过滑动快速在已计算的离散扩展半径之间无缝切换。', meta: '只在已计算半径间平滑切片；无求解开销。', impact: '快速观察不同扩散距离处的场强衰减对比。' },

    'color-mode': { title: '点云功率口径', tag: '显示模式', desc: '选择空间点云颜色映射所依据的物理接收功率合成方式。球面结果仅提供非相干面积平均和。', meta: '非相干和 / 载频相干 / 带宽平均功率 (dBm)。', impact: '非相干和忽略相位直接叠加功率；相干考虑载频相位干涉；带宽平均平滑带内衰落。' },
    'display-bs': { title: '全网与单站查看', tag: '结果筛选', desc: '全部基站一起计算；可全网自动选站，或查看指定站作为服务站的结果。', meta: '单站查看不会移除其他站的同频干扰。', impact: '全站功率和始终取全部基站的满功率参考，不作为可用信号功率。' },
    'point-size': { title: '点大小 (px)', tag: '点云渲染', desc: '三维视图中空间采样点云每个像素的渲染直径。', meta: '范围：1..20 px；默认 5 px。', impact: '放大可增强远距离观察时的连续色带感，缩小可避免密集点相互遮挡。' },
    'point-opacity': { title: '点云与网格透明度', tag: '视觉通透', desc: '空间采样点云及球面网格面片的半透明混合系数。', meta: '范围：0.05..1.0；默认 0.8。', impact: '降低透明度可透过外部球面观察被包围的建筑物与内部信道折线。' },
    'display-zmin': { title: '高度下切过滤', tag: '剖面切片', desc: '仅显示垂直高度大于或等于此阈值的空间点或球面面片。', meta: '单位：米 (m)；留空表示不限制下界。', impact: '切除地表点云，专注观察中高层建筑立面场强分布。' },
    'display-zmax': { title: '高度上切过滤', tag: '剖面切片', desc: '仅显示垂直高度小于或等于此阈值的空间点或球面面片。', meta: '单位：米 (m)；留空表示不限制上界。', impact: '切除高空几何，专注地表与底层街道峡谷覆盖。' },
    'minimum-power': { title: '最低接收功率门限', tag: '弱信号截断', desc: '弱信号过滤阈值，低于此门限的点或面片不予着色渲染。', meta: '单位：dBm；默认 -120 dBm。', impact: '滤除低于接收机灵敏度的深度衰落点或无有效路径暗区。' },
    'show-points': { title: '空间采样覆盖图层', tag: '图层控制', desc: '控制是否在三维场景中渲染接收网格点云或球面质心点。', meta: '布尔开关。', impact: '关闭后隐藏点云，便于专注观察几何建筑与传播路径。' },
    'show-surface-mesh': { title: '球面测量面片网格', tag: '图层控制', desc: '控制是否在三维场景中渲染测地球面的闭合彩色三角几何面片。', meta: '布尔开关；采用 GPU 密集渲染。', impact: '关闭后隐藏球面网格面片。' },
    'show-paths': { title: '传播路径与单点探测射线', tag: '图层控制', desc: '控制是否在场景中绘制当前选中 UE 或单点精算探测的多径折线。', meta: '布尔开关。', impact: '按类型以不同颜色绘制直达、反射、透射及漫反射折线。' },
    'show-buildings': { title: '建筑实体网格', tag: '图层控制', desc: '控制是否在三维场景中渲染建筑物实体闭合网格。', meta: '布尔开关。', impact: '隐藏后可透视观察穿过建筑内部的直达或透射路径。' },
    'show-devices': { title: '基站/终端高度杆', tag: '图层控制', desc: '控制基站 (BS)、终端 (UE) 标点及垂直离地高度杆的显示。', meta: '布尔开关。', impact: '直观指示设备空间物理位置与天线高度。' },
    'show-sampling': { title: '接收采样范围框', tag: '图层控制', desc: '绿色线框限定接收点的采样范围，不是反射面。传播路径可经过框外已建模的地面。', meta: '地面按实际计算范围显示；框外折点可在路径详情中查看。', impact: '核验接收点范围；开关不会改变传播路径或计算地面。' },
    'show-axes': { title: 'ENU 局部坐标轴', tag: '图层控制', desc: '控制原点 (0,0,0) 及东 (E)、北 (N)、天 (U) 三向指示箭头的显示。', meta: '布尔开关。', impact: '指示局部右手笛卡尔坐标系的方位方向。' },
    'show-grid': { title: '地面参考网格', tag: '图层控制', desc: '控制地面局部水平平面的参考标尺网格显示。', meta: '布尔开关。', impact: '提供地表空间距离的视觉参考标尺。' },
    'project-new': { title: '新建项目', tag: '工程管理', desc: '清除当前场景，建立一个空白的新无线传播仿真工程。', meta: '若有未保存更改将予以提示。', impact: '重置所有建筑、设备与计算结果。' },
    'project-open': { title: '打开项目', tag: '工程管理', desc: '通过系统原生文件对话框载入已保存的 .ran.json 项目工程文件。', meta: '原生文件系统读取。', impact: '完全恢复工程中所有建筑轮廓、电磁参数及设备部署。' },
    'project-save': { title: '保存项目', tag: '工程管理', desc: '将当前工程保存到原文件（若未命名则弹出另存为对话框）。', meta: '原子化落盘，防意外断电损坏。', impact: '持久化所有场景参数。' },
    'project-save-as': { title: '另存为', tag: '工程管理', desc: '选择新的文件路径和名称保存当前工程副本。', meta: '原生保存对话框。', impact: '创建工程独立备份。' },
    'export-result': { title: '导出结果', tag: '数据输出', desc: '将当前计算完成的点云覆盖数据、球面测量网格或 UE 多径数据导出为 JSON 或 CSV。', meta: '仅在计算完成后可用。', impact: '供外部科研、论文绘图或 MATLAB/Python 后处理分析。' },
    'run-coverage': { title: '计算三维覆盖', tag: 'GPU 求解', desc: '启动后台进程，基于当前选定几何模式求解三维体空间或扩展球面的场强分布。', meta: '调用 NVIDIA Sionna RT GPU 加速内核。', impact: '计算完成后生成全景空间覆盖图层。' },
    'run-ue': { title: '计算 UE', tag: 'GPU 求解', desc: '针对部署的所有用户终端 (UE) 计算精细的多径信道冲击响应 (CIR)。', meta: '需要场景中至少部署一个 UE。', impact: '生成每个 UE 的复数路径列表、时延分布与带内频率响应。' },
    'cancel-job': { title: '取消计算', tag: '进程控制', desc: '向后台计算子进程发送中断信号，安全终止当前 GPU 运算。', meta: '不会使工作台崩溃。', impact: '释放 GPU 显存并恢复界面操作。' },
    'mode-select': { title: '选择模式', tag: '场景交互', desc: '普通鼠标浏览与对象点选模式。', meta: '快捷键：Esc 退出其他模式。', impact: '点击建筑、设备、网格面片或点云可拾取并查看详情。' },
    'mode-place-bs': { title: '放置基站模式', tag: '场景交互', desc: '点击地面直接部署或移动基站位置。', meta: '十字光标提示。', impact: '点击地面完成基站定位。' },
    'mode-place-ue': { title: '放置终端模式', tag: '场景交互', desc: '点击地面直接部署或移动 UE 终端位置。', meta: '十字光标提示。', impact: '点击地面完成终端定位。' },
    'mode-draw': { title: '绘制建筑模式', tag: '场景交互', desc: '在地面连续点击添加顶点，构建自定义多边形建筑。', meta: '需在建筑面板点击“完成建筑”。', impact: '直接交互式绘制建筑物底面。' },
    'overview': { title: '三维全景视角', tag: '相机控制', desc: '将相机平滑移动至俯角 43° 的三维透视鸟瞰视角，完整包容所有几何要素与球壳。', meta: '双轴立体全景。', impact: '快速找回场景全局视图。' },
    'top-view': { title: '俯视正交视角', tag: '相机控制', desc: '将相机平滑调整为正上方 90° 垂直向下俯视视角。', meta: '类似于 2D 电子地图。', impact: '便于检查建筑平面间距与设备水平坐标。' },
    'result-ue': { title: '选择查看 UE 终端', tag: '结果切换', desc: '从已计算完成的终端列表中选择一个终端查看其具体多径与信道指标。', meta: '下拉选择 UE。', impact: '更新链路功率表、多径列表与频响图。' },
    'result-link': { title: '选择基站链路', tag: '结果切换', desc: '选择特定基站与当前终端之间的链路。', meta: '下拉选择 BS。', impact: '在三维视图中绘制该基站到该终端的全部传播路径。' },
    'clear-probe': { title: '清除单点探测', tag: '探测交互', desc: '从三维视图与结果面板中清除单点多径精算探测结果，恢复全景覆盖视图。', meta: '只移除探测临时结果，不改变覆盖数据。', impact: '清除场景中的探测射线与探测点标示。' }
  };

  for (const [id,title,desc,meta] of [
    ['bs-channel','资源信道编号','同编号 BS 在同一资源上发送并互扰；异编号按理想正交资源处理，不计邻道泄漏。','整数 0–1023；全站共用项目载频和带宽。'],
    ['bs-activity','干扰占用率','该站作为干扰源时的平均占用比例。服务站和条件协作试验按全功率发送。','0–1；这是长期平均干扰模型。'],
    ['bs-phase','发送载频相位','同波形同步试验中的额外发送相位，包含发送机时钟的载频相位偏差。传播相位仍来自完整 CIR。','度；不改变普通独立信号的 SINR。'],
    ['bs-time-offset','基带发送时偏','同波形同步试验中对频偏施加额外时延相位，保留不同站的绝对传播时延。','ns；载频相位独立由发送相位控制。'],
    ['network-temperature','噪声温度','接收机等效热噪声温度，与项目带宽及噪声系数一起决定 kTB 噪声。','K；默认 290。'],
    ['network-noise-figure','接收机噪声系数','对 kTB 热噪声乘以线性噪声因子，进入 SINR 的噪声项。','dB；默认 7。'],
    ['coverage-mode','覆盖呈现方式','在三维采样点云与投影到地面的网格热力图之间切换，复用当前计算结果。','球面数据需另算单层体网格。'],
    ['coverage-metric','覆盖与网络指标','普通部署统计同频干扰；协作与同步结果需要同数据及支持合并的接收机。','dBm 表示功率，dB 表示比值。'],
    ['heatmap-aggregation','热力图高度统计','默认单层最容易看出楼后盲区；均值在线性功率域计算，P10 反映较弱样本。','范围统计仅针对已计算的室外体网格。'],
    ['heatmap-height','接收高度','复用数据时选择最近的实际计算层并报告实际高度；计算单层则精确采用此高度。','m；推荐行人接收高度 1.5。'],
    ['heatmap-spacing','重新计算每格大小','每个格子使用该位置的真实接收采样值着色；更细网格需重新运行传播计算，不能用平滑插值代替。','支持 10、5、2、1 m 或沿用项目 E/N 间距；不代表物理误差保证。'],
    ['heatmap-zmin','统计高度下界','选择压缩到平面的室外样本高度区间。','m；包含端点。'],
    ['heatmap-zmax','统计高度上界','均值与 P10 不把缺测当零；达标比例是含未解析样本的保守下界。','m；默认 50。'],
    ['heatmap-threshold','达标门限','统计实际室外采样点中有证据达到该门限的比例。未知样本计为尚未达标。','量纲随覆盖指标变化。'],
    ['run-heatmap-plane','计算平面热力图','按输入高度和当前 E/N 网格运行真实 CUDA 射线追踪，并替换当前覆盖结果。','会将采样 U 上下界设为同一高度。'],
    ['run-heatmap','计算热力图','按当前接收高度和已应用的项目参数计算，完成后自动显示俯视热力图。','默认接收高度 1.5 m，无需进入计算设置。'],
    ['heatmap-view','查看热力图','已有体网格时直接显示；尚无结果时自动计算接收高度处的热力图。','建筑投影留空，庭院保留。']
  ]) PARAM_HELP[id]={title,tag:'覆盖与多站',desc,meta,impact:'修改后按对应应用或计算操作生效；显示参数只影响视图。'};

  // Help and lookup captions are read lazily so existing views change language.
  for (const dictionary of [powerNames, networkNames, kindNames, ...Object.values(PARAM_HELP)]) {
    for (const [key, value] of Object.entries(dictionary)) {
      if (typeof value === 'string') Object.defineProperty(dictionary, key, {enumerable: true, get: () => uiString(value)});
    }
  }
  const groups = new Map();
  const sceneCaptions = new Map();
  const deviceMemory = { bs: null, ues: null };
  let bridge = null, appState = null, pendingCommands = 0, runtimeError = '';
  let viewer = null, frame = null, inverseFrame = null;
  let workspaceActive = true;
  window.ranSetWorkspaceActive = active => {
    workspaceActive = Boolean(active);
    if (viewer && !viewer.isDestroyed()) {
      viewer.useDefaultRenderLoop = workspaceActive;
      if (workspaceActive) { viewer.resize(); viewer.scene.requestRender(); }
    }
  };

  let buildingPrimitive = null, groundPrimitive = null, pointCloud = null, surfaceMeshPrimitive = null;
  let pointRefs = [], selectedPointIndex = null, lastProjectJson = '', lastResultKey = '';
  let projectApplied = false, hasRenderedProject = false, shouldFrameNext = true;
  let mode = 'select', drawing = [], selectedBuildingId = null, activeDeviceKind = 'bs', selectedDeviceId = null;
  let selection = null, selectedUeId = null, selectedLinkId = null, selectedPath = null;
  let cloudUpdateQueued = false, sceneBounds = null;
  let samplingMode = 'grid'; // 'grid' | 'sphere'
  let currentRadius = 'all'; // 'all' | number
  let isPlayingRadius = false, radiusPlayTimer = null;
  let probeEntity = null;
  let heatmapPrimitive = null, heatmapMaterial = null, heatmapProjection = null, heatmapCanvas = null, selectedHeatmapCell = null;
  const originalConsoleError = console.error.bind(console);

  function setMessage(text, kind = 'error') {
    const value = String(text || tr('未知错误'));
    localizedText($('global-message-text'), () => uiString(value));
    $('global-message').className = `global-message ${kind === 'error' ? 'error' : ''}`;
    $('global-message').setAttribute('role', kind === 'error' ? 'alert' : 'status');
    $('global-message').hidden = false;
    if (kind === 'error') runtimeError = value;
    updateRuntime();
  }

  function clearMessage() {
    $('global-message').hidden = true;
    runtimeError = '';
    updateRuntime();
  }

  function errorText(error) { return error?.message || String(error); }
  function guard(handler) {
    return event => {
      if (event?.type === 'submit') event.preventDefault();
      return Promise.resolve().then(() => handler(event)).catch(error => setMessage(errorText(error)));
    };
  }

  window.addEventListener('error', event => {
    const resource = event.target !== window && (event.target?.src || event.target?.href);
    setMessage(resource ? tr("本地资源加载失败：{0}", resource) : tr("界面错误：{0}", event.message || errorText(event.error)));
  }, true);
  window.addEventListener('unhandledrejection', event => { event.preventDefault(); setMessage(errorText(event.reason)); });
  console.error = function (...args) {
    originalConsoleError(...args);
    setMessage(tr("显示诊断：{0}", args.map(arg => arg instanceof Error ? arg.message : String(arg)).join(' ').slice(0, 1600)));
  };

  function project() {
    if (!appState?.project) throw new Error(tr('本机项目尚未就绪。'));
    return appState.project;
  }
  function locked() { return !appState?.project || !bridge || !!appState.busy || pendingCommands > 0; }
  function number(id) {
    const input = $(id);
    const value = input.value.trim() === '' ? NaN : Number(input.value);
    if (!finite(value)) throw new Error(tr("{0}需要有效数字。", input.getAttribute('aria-label') || input.closest('label')?.childNodes[0]?.textContent?.trim() || id));
    return value;
  }
  function setValue(id, value) { $(id).value = value ?? ''; }
  function requireForm(id) { if (!$(id).reportValidity()) throw new Error(tr('请修正表单中标出的值。')); }
  function option(value, text) { const el = document.createElement('option'); el.value = value; localizedText(el, typeof text === 'function' ? text : () => text); return el; }
  function fillOptions(id, entries, selected) {
    const target = $(id);
    let initialized = false;
    bindPresentation(target, 'options', () => {
      const current = initialized ? target.value : selected ?? '';
      const items = typeof entries === 'function' ? entries() : entries;
      target.replaceChildren(...items.map(([value, text]) => option(value, text)));
      target.value = current;
      if (target.selectedIndex < 0 && target.options.length) target.selectedIndex = 0;
      initialized = true;
    });
  }
  function values(id, entries) {
    const target = $(id);
    bindPresentation(target, 'values', () => {
      target.replaceChildren();
      for (const [term, value] of typeof entries === 'function' ? entries() : entries) {
        const row = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
        localizedText(dt, () => term); localizedText(dd, () => value); row.append(dt, dd); target.append(row);
      }
    });
  }
  function powerText(link, key, unit = true, compact = false) {
    if (finite(link?.[key])) return `${format(link[key])}${unit ? ' dBm' : ''}`;
    if (key === 'coherent_dbm' && link?.coherent_zero && link.path_count > 0) return compact ? tr('−∞（相消）') : tr("相消为零（−∞{0}）", unit ? ' dBm' : '');
    return tr('无有效路径');
  }

  async function command(action, data = {}) {
    if (!bridge) throw new Error(tr('没有本机 Qt 桥接。请从 RANWorkbench.exe 启动工作台。'));
    if (appState?.busy && ['set_project', 'open', 'new', 'import_geojson', 'run'].includes(action)) throw new Error(tr('计算正在运行。请等待完成或先取消。'));
    pendingCommands += 1; updateControls();
    try {
      const response = await new Promise((resolve, reject) => {
        try {
          bridge.command(JSON.stringify({ action, data }), raw => {
            try { resolve(typeof raw === 'string' ? JSON.parse(raw) : raw); }
            catch (error) { reject(new Error(tr("本机返回了无法读取的数据：{0}", errorText(error)))); }
          });
        } catch (error) { reject(error); }
      });
      if (!response || typeof response.ok !== 'boolean') throw new Error(tr('本机返回数据缺少操作状态。'));
      if (response.state) applyState(response.state);
      if (!response.ok) throw new Error(response.error || tr('本机操作失败。'));
      return response;
    } finally { pendingCommands -= 1; updateControls(); }
  }

  async function commit(nextProject) {
    if (locked()) throw new Error(tr('当前不能修改项目，请等待本机操作完成。'));
    clearMessage();
    return command('set_project', { project: nextProject, expected_revision: appState.revision });
  }

  function updateRuntime() {
    const dot = $('status-dot'); dot.className = 'status-dot';
    let text = tr('正在连接本机工作台…');
    if (runtimeError) { dot.classList.add('error'); text = tr('操作未完成 · 查看上方诊断'); }
    else if (appState?.busy) { dot.classList.add('busy'); text = tr('本机 GPU 正在计算'); }
    else if (pendingCommands > 0) text = tr('正在处理本机操作…');
    else if (bridge && appState?.project && hasRenderedProject) { dot.classList.add('ready'); text = tr('本机已就绪 · 离线三维场景'); }
    else if (bridge && appState?.project) text = tr('正在准备三维场景…');
    localizedText($('runtime-status'), () => uiString(text));
    localizedAttribute($('runtime-status'), 'title', () => runtimeError || text);
  }

  function updateControls() {
    const disabled = locked();
    for (const el of document.querySelectorAll('[data-mutation], [data-edit-fieldset]')) el.disabled = disabled;
    for (const el of document.querySelectorAll('[data-native]')) el.disabled = !bridge || !appState?.project || pendingCommands > 0 || !!appState.busy;
    $('export-result').disabled ||= !appState?.result;
    $('building-editor-fields').disabled = disabled || !projectBuilding();
    $('delete-device').disabled = disabled || !currentDevice();
    $('building-delete').disabled = disabled || !projectBuilding();
    $('run-coverage').disabled = disabled;
    $('run-heatmap').disabled = disabled;
    $('heatmap-view').disabled = disabled;
    $('run-heatmap-plane').disabled = disabled;
    $('run-ue').disabled = disabled || !appState?.project?.ues?.length;
    $('cancel-job').hidden = !appState?.busy;
    $('cancel-job').disabled = !bridge || pendingCommands > 0;
    $('finish-drawing').disabled = disabled || drawing.length < 3;
    $('undo-drawing').disabled = disabled || drawing.length === 0;
    $('cancel-drawing').disabled = disabled;
    if (appState?.busy && mode !== 'select') setMode('select');
    updateProgress(); updateRuntime();
  }

  function updateProgress() {
    const progress = appState?.progress || {}, busy = !!appState?.busy;
    const labels = { starting: tr('启动计算进程'), startup: tr('启动计算进程'), initializing_cuda: tr('初始化 GPU'), building_scene: tr('准备传播场景'), loading: tr('加载传播场景'), scene: tr('准备场景'), coverage: tr('计算三维覆盖'), ue: tr('计算 UE 路径'), probe: tr('单点精算探测'), tracing: tr('追踪传播路径'), complete: tr('计算完成'), done: tr('计算完成'), cancelling: tr('正在取消计算，等待进程退出…'), cancelled: tr('已取消') };
    const stage = labels[progress.stage] || progress.stage || (busy ? tr('正在计算') : appState?.result ? tr('计算完成') : bridge && appState?.project ? tr('可运行本机传播计算') : tr('本机计算尚未就绪'));
    const counted = finite(progress.completed) && finite(progress.total) && progress.total > 0;
    const station=progress.bs_id,stations=appState?.project?.bs || [];
    localizedText($('job-stage'), () => `${uiString(stage)}${busy && station ? tr(" · {0}（{1}/{2} 站）", station, stations.findIndex(b=>b.id===station)+1, stations.length) : ''}${busy && counted ? ` · ${format(progress.completed, 0)} / ${format(progress.total, 0)}` : ''}`);
    if (busy && !counted) $('job-progress').removeAttribute('value');
    else { $('job-progress').max = counted ? progress.total : 1; $('job-progress').value = counted ? Math.min(progress.completed, progress.total) : appState?.result ? 1 : 0; }
  }

  function applyState(next) {
    if (!next || !next.project) throw new Error(tr('本机状态缺少项目数据。'));
    const oldError = appState?.error;
    const oldProbe = appState?.probe_result;
    appState = { ...appState, ...next };
    const encoded = JSON.stringify(appState.project), changed = encoded !== lastProjectJson;
    const result = appState.result;
    const probe = appState.probe_result;
    const resultKey = [
      result ? [result.scene_sha256, result.mode, result.elapsed_s, result.points?.length, result.ues?.length].join('|') : '',
      probe ? [probe.scene_sha256, probe.elapsed_s, probe.ues?.[0]?.position_m?.join(',')].join(':') : ''
    ].join('__');

    localizedText($('project-title'), () => appState.project.name, true);
    localizedText($('project-path'), () => appState.project_path || tr('尚未保存'));
    localizedAttribute($('project-path'), 'title', () => appState.project_path || tr('尚未保存到文件'));
    localizedText($('dirty-state'), () => appState.dirty ? tr('未保存') : '');

    if (changed) {
      lastProjectJson = encoded;
      $('point-inspector').hidden = true; selectedPointIndex = null; selectedHeatmapCell = null;
      populateProjectForms();
      if (viewer) {
        renderProject(); projectApplied = true;
        if (shouldFrameNext) { camera(); shouldFrameNext = false; }
      }
    }
    if (changed || resultKey !== lastResultKey) {
      lastResultKey = resultKey;
      renderResult();
      rebuildCoveragePrimitives();
      renderSelection();
      renderProbeInScene();
    }
    localizedText($('footer-state'), () => tr("{0} 栋建筑 · {1} 个 BS · {2} 个 UE · 项目修订 {3}", appState.project.buildings.length, appState.project.bs.length, appState.project.ues.length, appState.revision ?? '-'));
    if (appState.error && appState.error !== oldError) setMessage(appState.error, appState.error === '计算已取消' ? 'info' : 'error');
    else if (!appState.error && oldError) clearMessage();
    updateControls(); requestRender();
  }

  function handleEvent(raw) {
    try {
      const event = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (event.type === 'state') applyState(event.state);
      else if (event.type === 'progress' && appState) { appState.progress = event; updateProgress(); }
      else if (event.type === 'error') setMessage(event.error || event.message || tr('本机计算失败。'));
    } catch (error) { setMessage(tr("本机事件处理失败：{0}", errorText(error))); }
  }

  function tab(side, name, focus = false) {
    const attr = side === 'left' ? 'leftTab' : 'rightTab';
    for (const button of document.querySelectorAll(`[data-${side}-tab]`)) {
      const selected = button.dataset[attr] === name;
      button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
      $(button.getAttribute('aria-controls')).hidden = !selected;
      if (selected && focus) button.focus();
    }
  }

  function world(p) { return C.Matrix4.multiplyByPoint(frame, new C.Cartesian3(p[0], p[1], p[2]), new C.Cartesian3()); }
  function requestRender() { viewer?.scene.requestRender(); }
  function addEntity(group, options) {
    const caption = typeof options.label?.text === 'function' ? options.label.text : null;
    if (caption) options = {...options, label: {...options.label, text: caption()}};
    const entity = viewer.entities.add(options);
    if (caption) sceneCaptions.set(entity, caption);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(entity);
    return entity;
  }
  function clearGroup(group) { for (const entity of groups.get(group) || []) { sceneCaptions.delete(entity); viewer.entities.remove(entity); } groups.set(group, []); }
  function groupVisible(group, visible) { for (const entity of groups.get(group) || []) entity.show = visible; }
  function line(group, positions, color, width = 1, id) {
    return addEntity(group, { ...(id ? { id } : {}), polyline: { positions: positions.map(world), width, material: C.Color.fromCssColorString(color), arcType: C.ArcType.NONE } });
  }
  function label(group, position, text, color = '#e3e2da', offset = [0, 0]) {
    addEntity(group, { position: world(position), label: { text: () => uiString(text), font: '11px "Switzer",sans-serif', fillColor: C.Color.fromCssColorString(color), showBackground: true, backgroundColor: C.Color.fromCssColorString('#1b2321').withAlpha(0.92), backgroundPadding: new C.Cartesian2(4, 3), pixelOffset: new C.Cartesian2(...offset), disableDepthTestDistance: Number.POSITIVE_INFINITY } });
  }

  function initScene() {
    if (!C) throw new Error(tr('本地 CesiumJS 未能加载。请检查程序目录中的 vendor/cesium 资源。'));
    if (!C.PolygonPipeline?.triangulate) throw new Error(tr('本地 CesiumJS 缺少多边形三角剖分能力。'));
    C.Ion.defaultAccessToken = '';
    viewer = new C.Viewer('cesium', {
      baseLayer: false, terrainProvider: new C.EllipsoidTerrainProvider(), animation: false, timeline: false,
      baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false,
      navigationHelpButton: false, fullscreenButton: false, infoBox: false, selectionIndicator: false,
      skyBox: false, skyAtmosphere: false, sun: false, moon: false, requestRenderMode: true,
      maximumRenderTimeChange: Number.POSITIVE_INFINITY,
      contextOptions: { webgl: { preserveDrawingBuffer: true, alpha: false } }
    });
    window.ranSetWorkspaceActive(workspaceActive);
    viewer.scene.globe.show = false;
    viewer.scene.backgroundColor = C.Color.fromCssColorString('#161c1a');
    viewer.scene.fog.enabled = false;
    viewer.scene.screenSpaceCameraController.minimumZoomDistance = 2;
    viewer.scene.screenSpaceCameraController.maximumZoomDistance = 10000000;
    viewer.scene.screenSpaceCameraController.enableCollisionDetection = false;
    viewer.scene.renderError.addEventListener((_scene, error) => {
      document.body.dataset.ready = 'false';
      setMessage(tr("三维渲染失败：{0}", errorText(error)));
    });
    viewer.scene.postRender.addEventListener(() => {
      if (projectApplied && !hasRenderedProject) {
        hasRenderedProject = true; document.body.dataset.ready = 'true';
        $('map-placeholder').hidden = true; updateRuntime();
      }
    });
    const handler = new C.ScreenSpaceEventHandler(viewer.scene.canvas);
    handler.setInputAction(click => { try { sceneClick(click.position); } catch (error) { setMessage(errorText(error)); } }, C.ScreenSpaceEventType.LEFT_CLICK);
    handler.setInputAction(() => {}, C.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
    viewer.cesiumWidget.screenSpaceEventHandler.removeInputAction(C.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
  }

  function localBounds(p) {
    let minE = p.sampling.min_m[0], maxE = p.sampling.max_m[0], minN = p.sampling.min_m[1], maxN = p.sampling.max_m[1], maxU = p.sampling.max_m[2];
    for (const b of p.buildings) { for (const [e, n] of b.footprint_m) { minE = Math.min(minE, e); maxE = Math.max(maxE, e); minN = Math.min(minN, n); maxN = Math.max(maxN, n); } maxU = Math.max(maxU, b.base_m + b.height_m); }
    for (const d of [...p.bs, ...p.ues]) { const [e, n, u] = d.position_m; minE = Math.min(minE, e); maxE = Math.max(maxE, e); minN = Math.min(minN, n); maxN = Math.max(maxN, n); maxU = Math.max(maxU, u); }
    if (p.surface_sampling?.enabled) {
      const radii = p.surface_sampling.radii_m || [];
      const maxR = radii.length ? Math.max(...radii) : 50;
      const center = p.surface_sampling.center_bs_id
        ? (p.bs.find(b => b.id === p.surface_sampling.center_bs_id)?.position_m || p.surface_sampling.center_m || [0, 0, 25])
        : (p.surface_sampling.center_m || [0, 0, 25]);
      minE = Math.min(minE, center[0] - maxR);
      maxE = Math.max(maxE, center[0] + maxR);
      minN = Math.min(minN, center[1] - maxR);
      maxN = Math.max(maxN, center[1] + maxR);
      maxU = Math.max(maxU, center[2] + maxR);
    }
    const width = Math.max(maxE - minE, 40), depth = Math.max(maxN - minN, 40);
    return { minE, maxE, minN, maxN, maxU: Math.max(maxU, 10), width, depth, center: [(minE + maxE) / 2, (minN + maxN) / 2, Math.max(maxU, 10) / 3] };
  }

  function meshGeometry(building) {
    const rings = [building.footprint_m, ...(building.holes_m || [])], positions2 = [], holes = [];
    for (const [i, ring] of rings.entries()) { if (i) holes.push(positions2.length); for (const p of ring) positions2.push(new C.Cartesian2(p[0], p[1])); }
    const triangles = C.PolygonPipeline.triangulate(positions2, holes);
    const vertices = [], normals = [], indices = [], base = building.base_m, top = base + building.height_m;
    function triangle(a, b, c, normal) {
      const first = vertices.length / 3; vertices.push(...a, ...b, ...c);
      for (let i = 0; i < 3; i += 1) normals.push(...normal);
      indices.push(first, first + 1, first + 2);
    }
    for (let i = 0; i < triangles.length; i += 3) {
      let [a, b, c] = triangles.slice(i, i + 3).map(index => positions2[index]);
      if ((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x) < 0) [b, c] = [c, b];
      triangle([a.x, a.y, top], [b.x, b.y, top], [c.x, c.y, top], [0, 0, 1]);
      triangle([a.x, a.y, base], [c.x, c.y, base], [b.x, b.y, base], [0, 0, -1]);
    }
    for (const ring of rings) for (let i = 0; i < ring.length; i += 1) {
      const a = ring[i], b = ring[(i + 1) % ring.length], dx = b[0] - a[0], dy = b[1] - a[1], length = Math.hypot(dx, dy);
      const normal = [dy / length, -dx / length, 0];
      triangle([a[0], a[1], base], [b[0], b[1], base], [b[0], b[1], top], normal);
      triangle([a[0], a[1], base], [b[0], b[1], top], [a[0], a[1], top], normal);
    }
    return new C.Geometry({
      attributes: { position: new C.GeometryAttribute({ componentDatatype: C.ComponentDatatype.DOUBLE, componentsPerAttribute: 3, values: new Float64Array(vertices) }), normal: new C.GeometryAttribute({ componentDatatype: C.ComponentDatatype.FLOAT, componentsPerAttribute: 3, values: new Float32Array(normals) }) },
      indices: vertices.length / 3 > 65535 ? new Uint32Array(indices) : new Uint16Array(indices),
      primitiveType: C.PrimitiveType.TRIANGLES, boundingSphere: C.BoundingSphere.fromVertices(vertices)
    });
  }

  function renderProject() {
    const p = project(), origin = p.origin;
    frame = C.Transforms.eastNorthUpToFixedFrame(C.Cartesian3.fromDegrees(origin.longitude_deg, origin.latitude_deg, origin.ellipsoid_height_m));
    inverseFrame = C.Matrix4.inverseTransformation(frame, new C.Matrix4());
    sceneBounds = localBounds(p);
    if (buildingPrimitive) viewer.scene.primitives.remove(buildingPrimitive);
    buildingPrimitive = null;
    if (p.buildings.length) {
      buildingPrimitive = viewer.scene.primitives.add(new C.Primitive({
        geometryInstances: p.buildings.map(b => new C.GeometryInstance({ id: { kind: 'building', id: b.id }, geometry: meshGeometry(b), modelMatrix: frame, attributes: { color: C.ColorGeometryInstanceAttribute.fromColor(C.Color.fromCssColorString(b.material === 'glass' ? '#374b48' : '#2f3d39')) } })),
        asynchronous: false, appearance: new C.PerInstanceColorAppearance({ closed: true, translucent: false, flat: false })
      }));
    }
    const { minE, maxE, minN, maxN, width, depth } = sceneBounds;
    if (groundPrimitive) viewer.scene.primitives.remove(groundPrimitive);
    const groundBounds = appState.scene_geometry?.ground_bounds_m;
    if (!groundBounds) throw new Error(tr('计算地面范围缺失，请重新加载项目。'));
    const [gx0, gy0] = groundBounds.min_m, [gx1, gy1] = groundBounds.max_m;
    const ground = { footprint_m: [[gx0, gy0], [gx1, gy0], [gx1, gy1], [gx0, gy1]], holes_m: [], base_m: -0.1, height_m: 0.1 };
    groundPrimitive = viewer.scene.primitives.add(new C.Primitive({ geometryInstances: new C.GeometryInstance({ geometry: meshGeometry(ground), modelMatrix: frame, attributes: { color: C.ColorGeometryInstanceAttribute.fromColor(C.Color.fromCssColorString('#1b2320')) } }), releaseGeometryInstances: false, asynchronous: false, appearance: new C.PerInstanceColorAppearance({ flat: true, closed: true, translucent: false }), allowPicking: false }));
    clearGroup('grid');
    const span = Math.max(width, depth), roughStep = span / 10, magnitude = 10 ** Math.floor(Math.log10(roughStep));
    const gridStep = [1, 2, 5, 10].map(v => v * magnitude).find(v => v >= roughStep) || roughStep;
    for (let e = Math.ceil(minE / gridStep) * gridStep; e <= maxE + 1e-7; e += gridStep) line('grid', [[e, minN, 0.02], [e, maxN, 0.02]], '#263430');
    for (let n = Math.ceil(minN / gridStep) * gridStep; n <= maxN + 1e-7; n += gridStep) line('grid', [[minE, n, 0.02], [maxE, n, 0.02]], '#263430');
    label('grid', [maxE, minN - gridStep * 0.2, 0.1], tr("参考标尺 {0} m", format(gridStep)), '#8e948d');
    renderAxes(); renderSamplingBox(); renderDevices(); renderSelection(); renderDrawing(); renderProbeInScene(); applyLayerVisibility();
    if (C.DirectionalLight) viewer.scene.light = new C.DirectionalLight({ direction: C.Matrix4.multiplyByPointAsVector(frame, new C.Cartesian3(-0.4, -0.5, -1), new C.Cartesian3()) });
  }

  function renderAxes() {
    clearGroup('axes');
    const length = Math.max(12, Math.min(Math.max(sceneBounds.width, sceneBounds.depth) / 5, 100));
    const origin = [0, 0, 0.12];
    for (const [text, endpoint, color] of [[tr('E / 东'), [length, 0, 0.12], '#e06c75'], [tr('N / 北'), [0, length, 0.12], '#68b69e'], [tr('U / 上'), [0, 0, length], '#61afef']]) {
      addEntity('axes', { polyline: { positions: [world(origin), world(endpoint)], width: 3, arcType: C.ArcType.NONE, material: new C.PolylineArrowMaterialProperty(C.Color.fromCssColorString(color)) } });
      label('axes', endpoint, text, color, [0, -12]);
    }
    addEntity('axes', { position: world(origin), point: { pixelSize: 5, color: C.Color.fromCssColorString('#9fc7b8'), outlineColor: C.Color.WHITE, outlineWidth: 1 } });
    label('axes', origin, tr('原点'), '#9fc7b8', [0, 16]);
  }

  function renderSamplingBox() {
    clearGroup('sampling');
    const p = project();
    if (p.surface_sampling?.enabled) {
      const center = p.surface_sampling.center_bs_id
        ? (p.bs.find(b => b.id === p.surface_sampling.center_bs_id)?.position_m || p.surface_sampling.center_m || [0, 0, 25])
        : (p.surface_sampling.center_m || [0, 0, 25]);
      addEntity('sampling', { position: world(center), point: { pixelSize: 6, color: C.Color.fromCssColorString('#9fc7b8'), outlineColor: C.Color.WHITE, outlineWidth: 1 } });
      label('sampling', center, tr("球心 · {0}", p.surface_sampling.center_bs_id || tr('手动')), '#9fc7b8', [0, -16]);
      return;
    }
    const { min_m: lo, max_m: hi } = p.sampling;
    const corners = [];
    for (let i = 0; i < 8; i += 1) corners.push([i & 1 ? hi[0] : lo[0], i & 2 ? hi[1] : lo[1], i & 4 ? hi[2] : lo[2]]);
    for (let i = 0; i < 8; i += 1) for (let axis = 0; axis < 3; axis += 1) { const j = i ^ (1 << axis); if (i < j) line('sampling', [corners[i], corners[j]], '#436b5c', 1); }
    label('sampling', [hi[0], hi[1], hi[2]], tr('接收采样范围（非反射面）'), '#9fc7b8', [0, -14]);
  }

  function renderDevices() {
    if (!viewer || !frame || !appState?.project) return;
    clearGroup('devices');
    for (const kind of ['bs', 'ues']) for (const device of project()[kind]) {
      const chosen = selection?.kind === kind && selection.id === device.id;
      const color = kind === 'bs' ? '#d9825b' : '#6ec4ad';
      const [e, n, u] = device.position_m;
      line('devices', [[e, n, 0], [e, n, u]], color, chosen ? 3 : 2);
      addEntity('devices', { id: `device:${kind}:${device.id}`, position: world(device.position_m), point: { pixelSize: chosen ? 13 : kind === 'bs' ? 10 : 8, color: C.Color.fromCssColorString(color), outlineColor: C.Color.fromCssColorString('#161c1a'), outlineWidth: chosen ? 3 : 2, disableDepthTestDistance: Number.POSITIVE_INFINITY }, label: { text: `${device.id} · ${format(u)} m`, font: '11px "Switzer",sans-serif', fillColor: C.Color.fromCssColorString(color), showBackground: true, backgroundColor: C.Color.fromCssColorString('#1b2321').withAlpha(0.92), backgroundPadding: new C.Cartesian2(4, 3), pixelOffset: new C.Cartesian2(0, -20), disableDepthTestDistance: Number.POSITIVE_INFINITY } });
    }
    groupVisible('devices', $('show-devices').checked); requestRender();
  }

  function renderProbeInScene() {
    clearGroup('probe');
    const probe = appState?.probe_result;
    if (!probe || !probe.ues?.length) return;
    const ue = probe.ues[0];
    const pos = ue.position_m;
    line('probe', [[pos[0], pos[1], 0], [pos[0], pos[1], pos[2]]], '#9fc7b8', 2);
    addEntity('probe', {
      position: world(pos),
      point: { pixelSize: 12, color: C.Color.fromCssColorString('#9fc7b8'), outlineColor: C.Color.WHITE, outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY },
      label: { text: () => tr("单点探测 · {0} m", format(pos[2])), font: '11px "Switzer",sans-serif', fillColor: C.Color.fromCssColorString('#9fc7b8'), showBackground: true, backgroundColor: C.Color.fromCssColorString('#161c1a').withAlpha(0.95), backgroundPadding: new C.Cartesian2(4, 3), pixelOffset: new C.Cartesian2(0, -20), disableDepthTestDistance: Number.POSITIVE_INFINITY }
    });
    const paths = (ue.links?.[0]?.paths || []).slice(0, 30);
    for (const [index, path] of paths.entries()) {
      const vertices = path.vertices_m || [];
      if (vertices.length < 2) continue;
      addEntity('probe', { polyline: { positions: vertices.map(world), arcType: C.ArcType.NONE, width: 2.5, material: C.Color.fromCssColorString(pathColors[path.kind] || '#9fc7b8').withAlpha(0.9) } });
    }
    groupVisible('probe', $('show-paths').checked); requestRender();
  }

  function renderSelection() {
    if (!viewer || !frame) return;
    clearGroup('selection');
    if (selectedPointIndex == null) {
      const item = selection?.kind === 'building' ? project().buildings.find(b => b.id === selection.id) : ['bs', 'ues'].includes(selection?.kind) ? project()[selection.kind].find(d => d.id === selection.id) : null;
      localizedText($('footer-selection'), () => item ? selection.kind === 'building' ? tr("选中 {0} · 高 {1} m · 厚 {2} m", item.id, format(item.height_m), format(item.thickness_m)) : tr("选中 {0} · {1}", item.id, coord(item.position_m)) : tr('选择模式'));
    }
    if (selection?.kind !== 'building') { requestRender(); return; }
    const building = project().buildings.find(b => b.id === selection.id);
    if (!building) return;
    for (const ring of [building.footprint_m, ...(building.holes_m || [])]) {
      const top = ring.map(([e, n]) => [e, n, building.base_m + building.height_m + 0.03]);
      line('selection', [...top, top[0]], '#9fc7b8', 2.5);
      for (const [e, n] of ring) line('selection', [[e, n, building.base_m], [e, n, building.base_m + building.height_m]], '#9fc7b8', 1.5);
    }
    groupVisible('selection', $('show-buildings').checked); requestRender();
  }

  function camera(top = false) {
    if (!viewer || !frame || !sceneBounds) return;
    const extent = Math.max(sceneBounds.width, sceneBounds.depth, sceneBounds.maxU * 1.5, 70);
    const aspect = Math.max(viewer.scene.canvas.clientWidth / Math.max(viewer.scene.canvas.clientHeight, 1), 0.4);
    const range = extent * (top ? 1.5 : 1.7) * Math.max(1, 0.85 / aspect);
    if (!top) viewer.camera.switchToPerspectiveFrustum();
    viewer.camera.lookAt(world(sceneBounds.center), new C.HeadingPitchRange(C.Math.toRadians(top ? 0 : 22), C.Math.toRadians(top ? -90 : -43), range));
    if (top) { viewer.camera.switchToOrthographicFrustum(); viewer.camera.frustum.width = Math.max(sceneBounds.width * 1.15, sceneBounds.depth * aspect * 1.15, 80); }
    viewer.camera.lookAtTransform(C.Matrix4.IDENTITY); requestRender();
  }

  function applyLayerVisibility() {
    if (buildingPrimitive) buildingPrimitive.show = $('show-buildings').checked;
    const heat = $('coverage-mode').value === 'heatmap';
    if (pointCloud) pointCloud.show = !heat && $('show-points').checked && (!appState?.result?.surface_meshes?.length || $('surface-display-mode').value !== 'mesh');
    if (heatmapPrimitive) heatmapPrimitive.show = heat && $('show-points').checked;
    if (surfaceMeshPrimitive) surfaceMeshPrimitive.show = !heat && $('show-surface-mesh').checked && $('surface-display-mode').value === 'mesh';
    for (const [group, id] of [['selection', 'show-buildings'], ['devices', 'show-devices'], ['sampling', 'show-sampling'], ['axes', 'show-axes'], ['grid', 'show-grid'], ['paths', 'show-paths'], ['probe', 'show-paths']]) groupVisible(group, $(id).checked);
    $('map-legend').hidden = !$('show-points').checked && !($('show-surface-mesh').checked && appState?.result?.surface_meshes?.length);
    requestRender();
  }

  function projectBuilding() { return appState?.project?.buildings.find(b => b.id === selectedBuildingId) || null; }
  function currentDevice() { return appState?.project?.[activeDeviceKind]?.find(d => d.id === selectedDeviceId) || null; }
  function nextId(prefix) {
    const p = project(), ids = new Set([...p.buildings, ...p.bs, ...p.ues].map(item => item.id));
    let index = 1; while (ids.has(`${prefix}-${index < 10 ? '0' + index : index}`)) index += 1;
    return `${prefix}-${index < 10 ? '0' + index : index}`;
  }
  function centroid(ring) {
    let area = 0, east = 0, north = 0;
    for (let i = 0; i < ring.length; i += 1) {
      const a = ring[i], b = ring[(i + 1) % ring.length], cross = a[0] * b[1] - b[0] * a[1];
      area += cross; east += (a[0] + b[0]) * cross; north += (a[1] + b[1]) * cross;
    }
    return Math.abs(area) > 1e-12 ? [east / (3 * area), north / (3 * area)] : [ring.reduce((s, p) => s + p[0], 0) / ring.length, ring.reduce((s, p) => s + p[1], 0) / ring.length];
  }
  function buildingDimensions(building) {
    const center = centroid(building.footprint_m), rotation = finite(building.source?.editor_rotation_deg) ? building.source.editor_rotation_deg : 0;
    const angle = -rotation * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle);
    const local = building.footprint_m.map(([e, n]) => [(e - center[0]) * c - (n - center[1]) * s, (e - center[0]) * s + (n - center[1]) * c]);
    return { center, rotation, width: Math.max(...local.map(p => p[0])) - Math.min(...local.map(p => p[0])), depth: Math.max(...local.map(p => p[1])) - Math.min(...local.map(p => p[1])) };
  }

  function setSamplingMode(newMode) {
    samplingMode = newMode === 'sphere' ? 'sphere' : 'grid';
    $('sampling-mode-grid').setAttribute('aria-pressed', String(samplingMode === 'grid'));
    $('sampling-mode-sphere').setAttribute('aria-pressed', String(samplingMode === 'sphere'));
    $('grid-sampling-container').hidden = samplingMode !== 'grid';
    $('sphere-sampling-container').hidden = samplingMode !== 'sphere';
    previewSampling();
  }

  function populateProjectForms() {
    const p = project();
    setValue('project-name', p.name); setValue('frequency', p.frequency_hz / 1e9); setValue('bandwidth', p.bandwidth_hz / 1e6);
    setValue('longitude', p.origin.longitude_deg); setValue('latitude', p.origin.latitude_deg); setValue('origin-height', p.origin.ellipsoid_height_m);
    const synthetic = p.buildings.filter(b => b.source?.kind === 'synthetic').length;
    const sourceReport = p.source_report;
    localizedText($('source-notice'), () => synthetic ? tr("{0} 栋建筑为合成示例，未还原真实香港街区。缺失材质使用假定参数。", synthetic) : sourceReport ? tr("已导入 {0} 栋建筑，其中 {1} 栋使用默认高度。材质和材料层参数为假定值，未经现场实测校准。", sourceReport.imported_count, sourceReport.default_height_count) : tr('本地建筑模型。建筑材质参数为假定值，未经现场实测校准。'));
    values('scene-counts', () => [[tr('建筑'), tr("{0} 栋", p.buildings.length)], [tr('基站 / 终端'), `${p.bs.length} / ${p.ues.length}`], [tr('设备上限'), tr('5 个 BS / 64 个 UE')]]);
    if (!p.buildings.some(b => b.id === selectedBuildingId)) selectedBuildingId = p.buildings[0]?.id || null;
    fillOptions('building-picker', () => p.buildings.length ? p.buildings.map(b => [b.id, `${b.id} · ${b.name}`]) : [['', tr('尚无建筑，可绘制或导入')]], selectedBuildingId);
    populateBuildingForm();
    if (!p[activeDeviceKind].some(d => d.id === selectedDeviceId)) selectedDeviceId = p[activeDeviceKind].find(d => d.id === deviceMemory[activeDeviceKind])?.id || p[activeDeviceKind][0]?.id || null;
    deviceMemory[activeDeviceKind] = selectedDeviceId;
    populateDeviceForm(); populateDeviceList();

    // Cartesian Grid values
    const axes = ['e', 'n', 'u'];
    for (let axis = 0; axis < 3; axis += 1) { setValue(`sample-min-${axes[axis]}`, p.sampling.min_m[axis]); setValue(`sample-max-${axes[axis]}`, p.sampling.max_m[axis]); setValue(`sample-step-${axes[axis]}`, p.sampling.spacing_m[axis]); }
    updatePresetButtonState(p.sampling.spacing_m);

    // Spherical Surface values
    const bsOptions = p.bs.length ? p.bs.map(b => [b.id, `${b.id} · ${b.name}`]) : [['', tr('尚无基站')]];
    const surface = p.surface_sampling;
    const isSphere = surface?.enabled === true;
    setSamplingMode(isSphere ? 'sphere' : 'grid');

    fillOptions('surface-center-bs', () => bsOptions, surface?.center_bs_id || p.bs[0]?.id || '');
    $('surface-center-mode').value = surface?.center_bs_id ? 'bs' : 'manual';
    updateSurfaceCenterVisibility();
    const centerM = surface?.center_m || [0, 0, 25];
    setValue('surface-center-e', centerM[0]); setValue('surface-center-n', centerM[1]); setValue('surface-center-u', centerM[2]);
    setValue('surface-radii', (surface?.radii_m || [25, 50, 75, 100]).join(', '));
    setValue('surface-subdivisions', surface?.subdivisions ?? 3);
    setValue('surface-replicates', surface?.replicates ?? 2);
    setValue('surface-chunk-size', surface?.ray_chunk_size ?? 1000000);

    const solver = p.solver;
    $('solver-los').checked = solver.los !== false; $('solver-specular').checked = solver.specular_reflection !== false;
    $('solver-diffuse').checked = !!solver.diffuse_reflection; $('solver-refraction').checked = !!solver.refraction;
    setValue('solver-depth', solver.max_depth); setValue('solver-samples', solver.samples_per_src); setValue('solver-batch', solver.batch_size); setValue('solver-seed', solver.seed); setValue('solver-scattering', solver.scattering_coefficient);
    setValue('network-temperature', p.network?.temperature_k ?? 290); setValue('network-noise-figure', p.network?.noise_figure_db ?? 7);
    const displayBs = $('display-bs').value;
    fillOptions('display-bs', () => [['', tr("全部 {0} 基站 · 自动选择服务站", p.bs.length)], ...p.bs.map(b => [b.id, tr("{0} · {1}（仅查看）", b.id, b.name)])], p.bs.some(b => b.id === displayBs) ? displayBs : '');
    localizedText($('calculation-scope'), () => tr("计算范围：全部 {0} 个基站{1}。同频站互相计入干扰。", p.bs.length, p.bs.length ? ' · '+p.bs.map(b=>b.id).join('、') : ''));
    $('coverage-metric').value=window.RANMultiBS.initialMetric(p.bs.length,$('coverage-metric').value,coverageMetricExplicit);
    if (selection?.kind === 'building' && !p.buildings.some(b => b.id === selection.id)) selection = null;
    if (['bs', 'ues'].includes(selection?.kind) && !p[selection.kind].some(d => d.id === selection.id)) selection = null;
    previewSampling(); updateModeInstruction();
  }

  function updateSurfaceCenterVisibility() {
    const isBs = $('surface-center-mode').value === 'bs';
    $('surface-center-bs-wrap').hidden = !isBs;
    $('surface-center-manual-wrap').hidden = isBs;
  }

  function populateBuildingForm() {
    const b = projectBuilding();
    if (!b) { localizedText($('building-source'), () => tr('先选择已有建筑，或在下方创建新的轮廓。')); localizedText($('building-geometry-info'), () => ''); return; }
    const dimensions = buildingDimensions(b);
    setValue('building-name', b.name); setValue('building-east', dimensions.center[0]); setValue('building-north', dimensions.center[1]);
    setValue('building-width', dimensions.width); setValue('building-depth', dimensions.depth); setValue('building-rotation', dimensions.rotation);
    setValue('building-base', b.base_m); setValue('building-height', b.height_m); setValue('building-thickness', b.thickness_m);
    const materials = [['concrete', () => tr('ITU 混凝土（假定）')], ['glass', () => tr('ITU 玻璃（假定）')]];
    if (!materials.some(([key]) => key === b.material)) materials.push([b.material, () => tr("{0}（假定）", b.material)]);
    fillOptions('building-material', () => materials, b.material);
    const source = () => ({ synthetic: tr('合成示例'), geojson: tr('GeoJSON 导入'), manual: tr('手工建模') }[b.source?.kind] || b.source?.kind || tr('本地项目'));
    localizedText($('building-source'), () => tr("{0} · {1} · 电磁材质为假定值", b.id, source()));
    localizedText($('building-geometry-info'), () => tr("外轮廓 {0} 个顶点 · {1} 个孔洞。旋转以轮廓中心为基准，正值从东向北逆时针。", b.footprint_m.length, b.holes_m?.length || 0));
  }

  function populateDeviceList() {
    const list = $('device-list'); list.replaceChildren();
    if (!appState?.project) return;
    const devices = project()[activeDeviceKind];
    for (const device of devices) {
      const button = document.createElement('button'); button.type = 'button'; button.setAttribute('role', 'listitem');
      button.setAttribute('aria-pressed', String(device.id === selectedDeviceId));
      const name = document.createElement('span'), height = document.createElement('span');
      localizedText(name, () => `${device.id} · ${device.name}`, true); localizedText(height, () => `${format(device.position_m[2])} m`);
      button.append(name, height); button.addEventListener('click', () => selectDevice(activeDeviceKind, device.id)); list.append(button);
    }
    if (!devices.length) { const empty = document.createElement('p'); empty.className = 'help'; localizedText(empty, () => tr('尚无此类设备。输入坐标添加，或在场景中放置。')); list.append(empty); }
  }

  function populateDeviceForm() {
    const device = currentDevice(), isBs = activeDeviceKind === 'bs';
    $('device-kind').value = activeDeviceKind;
    localizedText($('device-editor-title'), () => device ? isBs ? tr('编辑基站 BS') : tr('编辑终端 UE') : isBs ? tr('新增基站 BS') : tr('新增终端 UE'));
    localizedText($('device-id'), () => device?.id || (appState?.project ? nextId(isBs ? 'BS' : 'UE') : ''));
    setValue('device-name', device?.name || `${isBs ? '基站' : '终端'} ${appState?.project ? project()[activeDeviceKind].length + 1 : 1}`);
    const position = device?.position_m || [0, 0, isBs ? 25 : 1.5];
    setValue('device-east', position[0]); setValue('device-north', position[1]); setValue('device-up', position[2]);
    setValue('bs-power', device?.power_dbm ?? 30);
    for (const [id,key,defaultValue] of [['bs-channel','channel',0],['bs-activity','activity',1],['bs-phase','phase_deg',0],['bs-time-offset','time_offset_ns',0]]) setValue(id,device?.[key] ?? defaultValue);
    for (const [i, id] of ['bs-yaw', 'bs-pitch', 'bs-roll'].entries()) setValue(id, device?.orientation_deg?.[i] ?? 0);
    $('bs-fields').hidden = !isBs; localizedText($('apply-device'), () => device ? tr('应用设备修改') : tr("添加{0}", isBs ? tr('基站') : tr('终端')));
    localizedText($('place-device'), () => device ? tr('在场景中移动') : tr('在场景中放置'));
  }

  function selectDevice(kind, id) {
    activeDeviceKind = kind; selectedDeviceId = id; deviceMemory[kind] = id;
    selection = { kind, id }; selectedPointIndex = null; selectedHeatmapCell = null; $('point-inspector').hidden = true;
    populateDeviceForm(); populateDeviceList(); tab('left', 'devices');
    renderDevices(); renderSelection(); updateModeInstruction(); updateControls();
    localizedText($('footer-selection'), () => id ? tr("选中 {0} · {1}", id, coord(currentDevice().position_m)) : tr("新增 {0}", kind === 'bs' ? 'BS' : 'UE'));
    if (kind === 'ues' && appState?.result?.ues?.some(ue => ue.ue_id === id)) { selectedUeId = id; selectedPath = null; renderUeResult(); }
  }

  function selectBuilding(id) {
    selectedBuildingId = id; selection = { kind: 'building', id }; $('building-picker').value = id;
    selectedPointIndex = null; selectedHeatmapCell = null; $('point-inspector').hidden = true;
    populateBuildingForm(); renderSelection(); renderDevices(); tab('left', 'buildings'); updateControls();
    const b = projectBuilding();
    localizedText($('footer-selection'), () => b ? tr("选中 {0} · 高 {1} m · 厚 {2} m", b.id, format(b.height_m), format(b.thickness_m)) : tr('选择模式'));
  }

  async function saveProjectForm() {
    requireForm('project-form');
    const next = clone(project());
    next.name = $('project-name').value.trim(); next.frequency_hz = number('frequency') * 1e9; next.bandwidth_hz = number('bandwidth') * 1e6;
    next.origin = { longitude_deg: number('longitude'), latitude_deg: number('latitude'), ellipsoid_height_m: number('origin-height') };
    await commit(next);
  }

  async function saveBuildingForm() {
    requireForm('building-form'); const original = projectBuilding(); if (!original) return;
    const dimensions = buildingDimensions(original), center = [number('building-east'), number('building-north')];
    const width = number('building-width'), depth = number('building-depth'), rotation = number('building-rotation');
    const oldAngle = -dimensions.rotation * Math.PI / 180, newAngle = rotation * Math.PI / 180;
    function transform(ring) {
      return ring.map(([e, n]) => {
        const x = e - dimensions.center[0], y = n - dimensions.center[1];
        const localX = (x * Math.cos(oldAngle) - y * Math.sin(oldAngle)) * width / dimensions.width;
        const localY = (x * Math.sin(oldAngle) + y * Math.cos(oldAngle)) * depth / dimensions.depth;
        return [center[0] + localX * Math.cos(newAngle) - localY * Math.sin(newAngle), center[1] + localX * Math.sin(newAngle) + localY * Math.cos(newAngle)];
      });
    }
    const next = clone(project()), b = next.buildings.find(item => item.id === original.id);
    Object.assign(b, { name: $('building-name').value.trim(), footprint_m: transform(original.footprint_m), holes_m: (original.holes_m || []).map(transform), height_m: number('building-height'), base_m: number('building-base'), thickness_m: number('building-thickness'), material: $('building-material').value, source: { ...b.source, editor_rotation_deg: rotation } });
    await commit(next);
  }

  function readDevice(position) {
    const original = currentDevice(), isBs = activeDeviceKind === 'bs';
    const device = { ...(original ? clone(original) : {}), id: original?.id || nextId(isBs ? 'BS' : 'UE'), name: $('device-name').value.trim(), position_m: position || [number('device-east'), number('device-north'), number('device-up')] };
    if (isBs) Object.assign(device, { power_dbm: number('bs-power'), orientation_deg: [number('bs-yaw'), number('bs-pitch'), number('bs-roll')], pattern: original?.pattern || 'iso',channel:number('bs-channel'),activity:number('bs-activity'),phase_deg:number('bs-phase'),time_offset_ns:number('bs-time-offset') });
    return device;
  }
  async function saveDevice(position) {
    requireForm('device-form');
    const device = readDevice(position), next = clone(project()), index = next[activeDeviceKind].findIndex(item => item.id === device.id);
    if (index < 0) next[activeDeviceKind].push(device); else next[activeDeviceKind][index] = device;
    const previousId = selectedDeviceId;
    selectedDeviceId = device.id; deviceMemory[activeDeviceKind] = device.id; selection = { kind: activeDeviceKind, id: device.id };
    try { await commit(next); } catch (error) { selectedDeviceId = previousId; deviceMemory[activeDeviceKind] = previousId; throw error; }
  }

  function updatePresetButtonState(spacing) {
    const current = spacing || ['e', 'n', 'u'].map(axis => {
      const v = Number($(`sample-step-${axis}`)?.value);
      return finite(v) ? v : null;
    });
    for (const preset of [20, 10, 5]) {
      const btn = $(`preset-grid-${preset}`);
      if (btn) {
        const matches = current.length === 3 && current.every(v => v === preset);
        btn.setAttribute('aria-pressed', String(matches));
      }
    }
  }
  function applyGridPreset(spacing) {
    for (const axis of ['e', 'n', 'u']) setValue(`sample-step-${axis}`, spacing);
    previewSampling();
    updatePresetButtonState([spacing, spacing, spacing]);
  }

  function parseRadiiString(str) {
    const parts = str.split(',').map(s => s.trim()).filter(Boolean);
    if (!parts.length || parts.length > 16) throw new Error(tr('半径序列需要 1–16 个半径值，以逗号分隔。'));
    const radii = parts.map(s => {
      const v = Number(s);
      if (!finite(v) || v <= 0) throw new Error(tr("半径值 \"{0}\" 无效，须为大于 0 的数字。", s));
      return v;
    });
    for (let i = 1; i < radii.length; i += 1) {
      if (radii[i] <= radii[i - 1]) throw new Error(tr('半径序列须按升序严格递增排列，且不能重复。'));
    }
    return radii;
  }

  function readSampling() {
    return { min_m: ['e', 'n', 'u'].map(axis => number(`sample-min-${axis}`)), max_m: ['e', 'n', 'u'].map(axis => number(`sample-max-${axis}`)), spacing_m: ['e', 'n', 'u'].map(axis => number(`sample-step-${axis}`)), limit: 250000 };
  }
  function samplingCount(sampling) {
    if (sampling.min_m[2] < 0) throw new Error(tr('采样 U 下界不能低于局部地面 0 m。'));
    const counts = sampling.min_m.map((min, axis) => {
      if (sampling.max_m[axis] < min) throw new Error(tr('各轴采样上界必须大于或等于下界。'));
      if (sampling.spacing_m[axis] <= 0) throw new Error(tr('E、N、U 采样间距均须大于 0。'));
      const ratio = (sampling.max_m[axis] - min) / sampling.spacing_m[axis];
      return Math.floor(ratio + Math.min(1e-7, 1e-9 * Math.max(1, ratio))) + 1;
    });
    const requested = counts.reduce((a, b) => a * b, 1);
    if (!Number.isSafeInteger(requested)) throw new Error(tr('请求点数过大，请增大间距或缩小范围。'));
    return { counts, requested };
  }

  function previewSampling() {
    if (!appState?.project) return;
    const target = $('sampling-preview'); target.classList.remove('invalid');
    const estimate = $('surface-work-estimate'); localizedText(estimate, () => '');
    if (samplingMode === 'sphere') {
      try {
        const radii = parseRadiiString($('surface-radii').value);
        const sub = Number($('surface-subdivisions').value);
        if (!Number.isInteger(sub) || sub < 0 || sub > 6) throw new Error(tr('细分等级须为 0 到 6 之间的整数。'));
        const facesPerSphere = 20 * (4 ** sub);
        const totalFaces = facesPerSphere * radii.length;
        const replicates = Number($('surface-replicates').value) || 2;
        const chunkSize = Number($('surface-chunk-size').value) || 1000000;
        const samplesPerSrc = Number($('solver-samples')?.value) || 1000000;

        localizedText(target, () => tr("{0} 个扩展球面 × {1} 面片/球 = {2} 个请求面片{3}", radii.length, format(facesPerSphere, 0), format(totalFaces, 0), totalFaces > 250000 ? tr('，超过 250,000 面片上限') : tr(' (计算前排除地下及穿插面片)')));
        target.classList.toggle('invalid', totalFaces > 250000);

        const totalRays = radii.length * replicates * samplesPerSrc;
        const launchesPerRep = Math.ceil(samplesPerSrc / chunkSize);
        const totalLaunches = radii.length * replicates * launchesPerRep;
        localizedText(estimate, () => tr("GPU 工作量预估：每块 {0} 条射线；每个半径独立重复 {1} 次；共计 {2} 轮 GPU 分块求解，累计追踪 {3} 条射线。", format(chunkSize, 0), replicates, totalLaunches, format(totalRays, 0)));
      } catch (error) { localizedText(target, () => errorText(error)); target.classList.add('invalid'); }
    } else {
      try {
        const sampling = readSampling(), { counts, requested } = samplingCount(sampling);
        localizedText(target, () => tr("{0} = {1} 个请求点{2}", counts.map(n => format(n, 0)).join(' × '), format(requested, 0), requested > sampling.limit ? tr("，超过 {0} 点上限", format(sampling.limit, 0)) : tr('，计算前将排除建筑内部点')));
        target.classList.toggle('invalid', requested > sampling.limit);
        updatePresetButtonState(sampling.spacing_m);
      } catch (error) { localizedText(target, () => errorText(error)); target.classList.add('invalid'); }
    }
  }

  function simulationProject(checkCount = true) {
    requireForm('simulation-form');
    const next = clone(project());
    if (samplingMode === 'sphere') {
      const radii = parseRadiiString($('surface-radii').value);
      const sub = Number($('surface-subdivisions').value);
      if (!Number.isInteger(sub) || sub < 0 || sub > 6) throw new Error(tr('细分等级须为 0 到 6 之间的整数。'));
      const totalFaces = 20 * (4 ** sub) * radii.length;
      if (checkCount && totalFaces > 250000) throw new Error(tr("请求 {0} 个球面面片，超过 250,000 上限。请降低细分等级或减少半径。", format(totalFaces, 0)));

      const replicates = Number($('surface-replicates').value);
      if (!Number.isInteger(replicates) || replicates < 1 || replicates > 8) throw new Error(tr('重复样本数须在 1 到 8 之间。'));
      const chunkSize = Number($('surface-chunk-size').value);
      if (!finite(chunkSize) || chunkSize < 1024 || chunkSize > 2000000) throw new Error(tr('射线分块上限须在 1,024 到 2,000,000 之间。'));

      const isBs = $('surface-center-mode').value === 'bs';
      let centerBsId = null, centerM = [0, 0, 25];
      if (isBs) {
        centerBsId = $('surface-center-bs').value;
        const bs = next.bs.find(b => b.id === centerBsId);
        if (!bs) throw new Error(tr('所选球心基站不存在，请先添加基站。'));
        centerM = clone(bs.position_m);
      } else {
        centerM = [number('surface-center-e'), number('surface-center-n'), number('surface-center-u')];
        if (centerM[2] < 0) throw new Error(tr('球心 U 坐标不能低于地面 0 m。'));
      }

      next.surface_sampling = {
        enabled: true,
        center_m: centerM,
        center_bs_id: centerBsId,
        radii_m: radii,
        subdivisions: sub,
        replicates,
        ray_chunk_size: chunkSize
      };
    } else {
      const sampling = readSampling(), { requested } = samplingCount(sampling);
      if (checkCount && requested > sampling.limit) throw new Error(tr("请求 {0} 个采样点，超过 {1} 点上限。请增大间距或缩小范围。", format(requested, 0), format(sampling.limit, 0)));
      next.sampling = sampling;
      if (next.surface_sampling) next.surface_sampling.enabled = false;
    }

    next.solver = {
      ...next.solver,
      max_depth: number('solver-depth'),
      samples_per_src: number('solver-samples'),
      batch_size: number('solver-batch'),
      seed: number('solver-seed'),
      scattering_coefficient: number('solver-scattering'),
      los: $('solver-los').checked,
      specular_reflection: $('solver-specular').checked,
      diffuse_reflection: $('solver-diffuse').checked,
      refraction: $('solver-refraction').checked
    };
    next.network = {...next.network,temperature_k:number('network-temperature'),noise_figure_db:number('network-noise-figure')};
    return next;
  }

  function activateAllStations() {
    $('display-bs').value=''; selectedLinkId=''; selectedPath=null;
    $('coverage-metric').value=window.RANMultiBS.initialMetric(project().bs.length,$('coverage-metric').value,coverageMetricExplicit);
  }

  async function runSimulation(runMode) {
    if (locked()) return;
    const next = simulationProject(runMode === 'coverage');
    if (!next.bs.length) throw new Error(tr('请先部署至少一个基站。'));
    if (runMode === 'ue' && !next.ues.length) throw new Error(tr('请先部署至少一个 UE。'));
    if (JSON.stringify(next) !== JSON.stringify(project())) await commit(next);
    activateAllStations();
    if(runMode==='coverage') {$('coverage-mode').value='cloud';syncCoverageControls();camera();}
    clearMessage(); setMode('select'); tab('right', 'results'); await command('run', { mode: runMode });
  }

  async function runExactProbe(position) {
    if (locked()) return;
    if (!Array.isArray(position) || position.length !== 3 || !position.every(finite)) throw new Error(tr('单点精算探测需要有效的三维坐标。'));
    clearMessage();
    setMode('select');
    tab('right', 'results');
    await command('run', { mode: 'probe', position_m: position });
  }

  async function runHeatmapPlane() {
    if (locked()) return;
    const height = number('heatmap-height');
    if (height < 0) throw new Error(tr('接收高度不能低于地面。'));
    if(!project().bs.length) throw new Error(tr('请先在设备页放置至少一个基站。'));
    // The primary action uses applied project settings, so an incomplete
    // hidden advanced form cannot block this one-click workflow.
    const next=clone(project());next.sampling.min_m[2]=height;next.sampling.max_m[2]=height;
    if($('heatmap-spacing').value!=='project') {
      const spacing=Number($('heatmap-spacing').value);
      if(![1,2,5,10].includes(spacing))throw new Error(tr('请选择有效的热力图网格大小。'));
      next.sampling.spacing_m[0]=spacing;next.sampling.spacing_m[1]=spacing;
    }
    const {requested}=samplingCount(next.sampling);
    if(requested>next.sampling.limit)throw new Error(tr("当前网格需要 {0} 个点，超过 {1} 点上限；请增大每格大小或缩小 E/N 范围。", format(requested,0), format(next.sampling.limit,0)));
    if(next.surface_sampling) next.surface_sampling.enabled=false;
    $('heatmap-aggregation').value = 'slice'; $('coverage-mode').value = 'heatmap';
    activateAllStations();$('show-points').checked=true;
    const opacity=Number($('point-opacity').value);
    if(!finite(opacity) || opacity<.05 || opacity>1) setValue('point-opacity',.8);
    if(JSON.stringify(next)!==JSON.stringify(project())) await commit(next);
    clearMessage();setMode('select');syncCoverageControls();camera(true);tab('right','display');
    document.querySelector('.results-panel .panel-scroll').scrollTop=0;
    await command('run',{mode:'coverage'});
  }

  async function showHeatmap() {
    if(locked())return;
    if(!appState?.result?.points?.length || appState.result.surface_meshes?.length) {await runHeatmapPlane();return;}
    $('coverage-mode').value='heatmap';$('show-points').checked=true;
    tab('right','display');switchCoverageMode();
    document.querySelector('.results-panel .panel-scroll').scrollTop=0;
  }

  function creationBuilding(footprint, shape = 'polygon') {
    requireForm('create-building-form');
    const id = nextId('B');
    return { id, name: `${shape === 'cylinder' ? '圆形建筑' : '建筑'} ${project().buildings.length + 1}`, footprint_m: footprint, holes_m: [], base_m: number('create-base'), height_m: number('create-height'), thickness_m: number('create-thickness'), material: $('create-material').value, source: { kind: 'manual', shape, ...(shape === 'cylinder' ? { segments: 48 } : {}) } };
  }
  async function addBuilding(building) {
    const next = clone(project()); next.buildings.push(building);
    const previous = selectedBuildingId; selectedBuildingId = building.id; selection = { kind: 'building', id: building.id };
    try { await commit(next); setMode('select'); selectBuilding(building.id); }
    catch (error) { selectedBuildingId = previous; throw error; }
  }
  async function finishDrawing() {
    if (drawing.length < 3) throw new Error(tr('建筑轮廓至少需要三个顶点。'));
    await addBuilding(creationBuilding(drawing.map(p => [p[0], p[1]])));
  }
  async function createBuilding() {
    requireForm('create-building-form');
    if ($('create-shape').value === 'polygon') { setMode('draw'); return; }
    const e = number('circle-east'), n = number('circle-north'), radius = number('circle-radius');
    if (radius <= 0) throw new Error(tr('圆形建筑半径须大于 0。'));
    const ring = Array.from({ length: 48 }, (_, i) => [e + radius * Math.cos(i * 2 * Math.PI / 48), n + radius * Math.sin(i * 2 * Math.PI / 48)]);
    await addBuilding(creationBuilding(ring, 'cylinder'));
  }

  function setMode(nextMode) {
    if (nextMode !== 'select' && locked()) throw new Error(tr('当前不能编辑场景，请等待本机操作结束。'));
    if (nextMode === 'draw') { tab('left', 'buildings'); $('create-shape').value = 'polygon'; updateCreateShape(); }
    if (nextMode.startsWith('place-')) {
      const kind = nextMode.slice(6);
      if (activeDeviceKind !== kind) selectDevice(kind, project()[kind].find(d => d.id === deviceMemory[kind])?.id || project()[kind][0]?.id || null);
      tab('left', 'devices');
    }
    if (nextMode !== 'draw') drawing = [];
    mode = nextMode;
    for (const button of document.querySelectorAll('[data-mode]')) button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
    updateModeInstruction(); renderDrawing();
  }
  function updateModeInstruction() {
    let text = tr('拖动浏览 · 右键旋转 · 滚轮缩放 · 点击选择对象');
    if (mode === 'draw') text = tr("绘制建筑：点击地面添加顶点（已 {0} 个），在建筑页完成；Esc 取消", drawing.length);
    if (mode.startsWith('place-')) {
      const u = $('device-up').value, device = currentDevice();
      text = tr("{0}：点击地面确定 E、N，U = {1} m；Esc 取消", device ? tr("移动 {0}", device.id) : tr("新增 {0}", activeDeviceKind === 'bs' ? 'BS' : 'UE'), u || tr('待输入'));
    }
    localizedText($('mode-instruction'), () => uiString(text)); $('mode-instruction').classList.toggle('active', mode !== 'select');
    $('cesium').style.cursor = mode === 'select' ? '' : 'crosshair';
    $('drawing-controls').hidden = mode !== 'draw';
    localizedText($('drawing-summary'), () => tr("已绘制 {0} 个顶点。至少三个顶点；不重复点击首点。", drawing.length));
    $('finish-drawing').disabled = locked() || drawing.length < 3; $('undo-drawing').disabled = locked() || !drawing.length;
    if (mode !== 'select') localizedText($('footer-selection'), () => mode === 'draw' ? tr("多边形绘制 · {0} 顶点", drawing.length) : tr("设备放置 · U {0} m", $('device-up').value || '-'));
  }
  function updateCreateShape() {
    const circle = $('create-shape').value === 'circle';
    $('circle-fields').hidden = !circle;
    for (const id of ['circle-east', 'circle-north', 'circle-radius']) $(id).required = circle;
    localizedText($('create-building'), () => circle ? tr('创建圆形建筑') : tr('开始绘制多边形'));
    if (circle && mode === 'draw') setMode('select');
  }
  function renderDrawing() {
    if (!viewer || !frame) return;
    clearGroup('drawing');
    for (const [i, p] of drawing.entries()) {
      addEntity('drawing', { position: world(p), point: { pixelSize: 6, color: C.Color.fromCssColorString('#9fc7b8'), outlineColor: C.Color.WHITE, outlineWidth: 1, disableDepthTestDistance: Number.POSITIVE_INFINITY } });
      label('drawing', p, String(i + 1), '#9fc7b8', [0, -17]);
    }
    if (drawing.length > 1) line('drawing', drawing.length >= 3 ? [...drawing, drawing[0]] : drawing, '#9fc7b8', 2.5);
    updateModeInstruction(); requestRender();
  }

  function groundHit(position) {
    const ray = viewer.camera.getPickRay(position); if (!ray) return null;
    const origin = C.Matrix4.multiplyByPoint(inverseFrame, ray.origin, new C.Cartesian3());
    const direction = C.Matrix4.multiplyByPointAsVector(inverseFrame, ray.direction, new C.Cartesian3());
    if (Math.abs(direction.z) < 1e-10) return null;
    const t = -origin.z / direction.z; if (t < 0) return null;
    const e = origin.x + t * direction.x, n = origin.y + t * direction.y;
    return finite(e) && finite(n) ? [Math.round(e * 1000) / 1000, Math.round(n * 1000) / 1000, 0.12] : null;
  }
  function sceneClick(position) {
    if (!frame || !appState?.project) return;
    if (mode !== 'select') {
      if (locked()) return;
      const hit = groundHit(position); if (!hit) { setMessage(tr('当前视线未与局部地面相交，请先调整视角。')); return; }
      if (mode === 'draw') { drawing.push(hit); renderDrawing(); return; }
      if (mode.startsWith('place-')) {
        const up = number('device-up');
        setValue('device-east', hit[0]); setValue('device-north', hit[1]);
        saveDevice([hit[0], hit[1], up]).then(() => setMode('select')).catch(error => setMessage(errorText(error))); return;
      }
    }
    const picked = viewer.scene.pick(position), pickedId = picked?.id;
    if (pickedId?.kind === 'building') { selectBuilding(pickedId.id); return; }
    if (pickedId?.kind === 'point') { inspectPoint(pickedId.index); return; }
    if (pickedId?.kind === 'cell') { inspectPoint(pickedId.index); return; }

    const id = typeof pickedId === 'string' ? pickedId : pickedId?.id;
    if (id?.startsWith('device:')) { const [, kind, ...rest] = id.split(':'); selectDevice(kind, rest.join(':')); return; }

    if ($('coverage-mode').value === 'heatmap' && $('show-points').checked && heatmapProjection) {
      const hit = groundHit(position), cell = hit && window.RANHeatmap.cellAt(heatmapProjection,hit[0],hit[1]);
      if (cell) { inspectHeatmap(cell); return; }
    }

    // If spherical result is loaded, attempt ray-sphere pick against active meshes
    if (appState?.result?.surface_meshes?.length) {
      const cellIndex = pickSphericalCell(position);
      if (cellIndex != null) { inspectPoint(cellIndex); return; }
    }

    selection = null; selectedPointIndex = null; selectedHeatmapCell = null; $('point-inspector').hidden = true; renderSelection(); renderDevices(); localizedText($('footer-selection'), () => tr('选择模式'));
  }

  function pickSphericalCell(screenPos) {
    if (!viewer || !frame) return null;
    const meshVisible = $('show-surface-mesh') ? $('show-surface-mesh').checked : true;
    const pointsVisible = $('show-points') ? $('show-points').checked : true;
    const displayMode = $('surface-display-mode')?.value ?? 'mesh';
    const layerVisible = displayMode === 'mesh' ? meshVisible : (meshVisible || pointsVisible);
    if (!layerVisible) return null;

    let options;
    try { options = displayOptions(); } catch { return null; }

    const ray = viewer.camera.getPickRay(screenPos); if (!ray) return null;
    const rayOrigin = C.Matrix4.multiplyByPoint(inverseFrame, ray.origin, new C.Cartesian3());
    const rayDir = C.Matrix4.multiplyByPointAsVector(inverseFrame, ray.direction, new C.Cartesian3());
    C.Cartesian3.normalize(rayDir, rayDir);

    const meshes = appState?.result?.surface_meshes || [];
    const activeMeshes = currentRadius === 'all' ? meshes : meshes.filter(m => String(m.radius_m) === String(currentRadius));
    let nearestPointIndex = null, minDistance = Infinity;

    for (const mesh of activeMeshes) {
      const faces = mesh.faces || [];
      const vertices = mesh.vertices_m || [];
      const pointIndices = mesh.point_indices || [];
      for (let i = 0; i < faces.length; i += 1) {
        const [i0, i1, i2] = faces[i];
        const v0 = vertices[i0], v1 = vertices[i1], v2 = vertices[i2];
        const pIndex = pointIndices[i];
        if (pIndex == null) continue;
        const pt = appState.result.points[pIndex];
        if (!pt) continue;

        // Apply same visible-cell predicate as rendering
        const link = bestLink(pt.links || [], options.key, options.bsId);
        const val = displayValue(pt,options);
        const height = pt.position_m[2];
        const isVisible = finite(val) && val >= options.threshold &&
          (options.lower == null || height >= options.lower) &&
          (options.upper == null || height <= options.upper);
        if (!isVisible) continue;

        // Moller-Trumbore intersection
        const e1x = v1[0] - v0[0], e1y = v1[1] - v0[1], e1z = v1[2] - v0[2];
        const e2x = v2[0] - v0[0], e2y = v2[1] - v0[1], e2z = v2[2] - v0[2];
        const px = rayDir.y * e2z - rayDir.z * e2y, py = rayDir.z * e2x - rayDir.x * e2z, pz = rayDir.x * e2y - rayDir.y * e2x;
        const det = e1x * px + e1y * py + e1z * pz;
        if (Math.abs(det) < 1e-7) continue;
        const invDet = 1.0 / det;
        const tx = rayOrigin.x - v0[0], ty = rayOrigin.y - v0[1], tz = rayOrigin.z - v0[2];
        const u = (tx * px + ty * py + tz * pz) * invDet;
        if (u < 0 || u > 1) continue;
        const qx = ty * e1z - tz * e1y, qy = tz * e1x - tx * e1z, qz = tx * e1y - ty * e1x;
        const v = (rayDir.x * qx + rayDir.y * qy + rayDir.z * qz) * invDet;
        if (v < 0 || u + v > 1) continue;
        const t = (e2x * qx + e2y * qy + e2z * qz) * invDet;
        if (t > 0 && t < minDistance) {
          minDistance = t;
          nearestPointIndex = pIndex;
        }
      }
    }
    return nearestPointIndex;
  }

  function resultUe() { return appState?.result?.ues?.find(ue => ue.ue_id === selectedUeId) || null; }
  function resultLink() { return resultUe()?.links?.find(link => link.bs_id === selectedLinkId) || null; }
  function resultLinks() { return window.RANMultiBS.linksFor(resultUe(),selectedLinkId); }
  function resultPaths() { return window.RANMultiBS.pathEntries(resultLinks()); }
  function bestLink(links, key, bsId = '') {
    if (bsId) return links.find(link => link.bs_id === bsId) || null;
    let best = null;
    for (const link of links) if (finite(link[key]) && (!best || link[key] > best[key])) best = link;
    return best || links.find(link => key === 'coherent_dbm' && link.coherent_zero && link.path_count > 0) || null;
  }
  function resultCell(row, text, className = '') { const cell = document.createElement('td'); localizedText(cell, typeof text === 'function' ? text : () => text); if (className) cell.className = className; row.append(cell); return cell; }

  function renderNetwork(point,targetId,bsId='') {
    const target = $(targetId); target.replaceChildren();
    if (!point?.network) {target.hidden=true;return;}
    target.hidden=false;
    const data = bsId ? point.network.per_bs.find(r=>r.bs_id===bsId) || point.network : point.network;
    const title = document.createElement('h3'); localizedText(title, () => tr('全部基站 · 接收信号与互扰')); target.append(title);
    const dl=document.createElement('dl');dl.id=`${targetId}-values`;dl.className='compact-values';target.append(dl);
    const metric=(v,unit)=>finite(v)?`${format(v)} ${unit}`:tr('未解析');
    values(dl.id, () => [[tr('服务 / 所选基站'),bsId || point.network.serving_bs_id || tr('未解析')],
      [tr('已解析 / 参与基站'),`${point.network.resolved_bs_count ?? point.links?.filter(l=>finite(l.incoherent_dbm)).length} / ${point.network.requested_bs_count ?? point.links?.length}`],
      [tr('全站满功率接收和'),metric(point.network.total_received_dbm,'dBm')],
      [tr('接收机噪声'),metric(data.noise_dbm,'dBm')],[tr('同信道干扰'),finite(data.interference_dbm)?metric(data.interference_dbm,'dBm'):tr('无已解析干扰')],
      [tr('普通部署 SINR'),metric(data.sinr_db,'dB')],[tr('单站 SNR'),metric(data.snr_db,'dB')]]);
    const table=document.createElement('table');table.className='network-table';
    const head=document.createElement('thead'),hr=document.createElement('tr');
    for (const text of [tr('基站 / 资源'),tr('信号 dBm'),tr('干扰 dBm'),'SINR dB']) {const th=document.createElement('th');th.scope='col';localizedText(th, () => uiString(text));hr.append(th);}head.append(hr);table.append(head);
    const body=document.createElement('tbody');
    for (const n of point.network.per_bs) {
      const row=document.createElement('tr');row.classList.toggle('selected',n.bs_id===(bsId || point.network.serving_bs_id));
      for (const text of [`${n.bs_id} / ${n.channel}`,format(n.signal_dbm),format(n.interference_dbm),format(n.sinr_db)]) resultCell(row,() => text);
      body.append(row);
    }
    table.append(body);const scroll=document.createElement('div');scroll.className='table-scroll';scroll.append(table);target.append(scroll);
    const sources=data.interference_sources || [];
    if(sources.length) {
      const heading=document.createElement('h4');localizedText(heading, () => tr("对 {0} 的逐站干扰贡献", bsId || point.network.serving_bs_id));target.append(heading);
      const sourceTable=document.createElement('table');sourceTable.className='network-table interference-sources';
      const sourceHead=sourceTable.createTHead().insertRow();
      for(const text of [tr('来源基站'),tr('资源 / 占用率'),tr('计入干扰 dBm')]) {const th=document.createElement('th');localizedText(th, () => text);th.scope='col';sourceHead.append(th);}
      const sourceBody=sourceTable.createTBody();
      for(const source of sources) {
        const row=sourceBody.insertRow();resultCell(row,() => source.bs_id);resultCell(row,() => `${source.channel} / ${format(source.activity*100,0)}%`);
        resultCell(row,() => source.status==='cochannel'?format(source.contribution_dbm):({orthogonal:tr('正交 · 不计入'),inactive:tr('未占用 · 不计入'),unresolved:tr('未解析 · 未知')}[source.status] || tr('未知')));
      }
      const sourceScroll=document.createElement('div');sourceScroll.className='table-scroll';sourceScroll.append(sourceTable);target.append(sourceScroll);
    }
    const note=document.createElement('p');note.className='help';
    localizedText(note, () => tr('普通部署：最强站服务，同信道站按占用率干扰。协作功率与同步功率是同数据、同资源、全功率发送的条件试验；同步值含实际传播相位、设置的发送相位及基带时偏。') +
      (point.network.unresolved_bs_ids.length?tr(" 未解析站：{0}；干扰与协作仅基于已解析路径，存在漏检不确定性。", point.network.unresolved_bs_ids.join('、')):''));
    target.append(note);
    const experiment=document.createElement('details');experiment.className='advanced-section';
    const summary=document.createElement('summary');localizedText(summary, () => tr('协作与同步条件试验'));experiment.append(summary);
    const conditional=document.createElement('dl');conditional.id=`${targetId}-cooperation-values`;conditional.className='compact-values';experiment.append(conditional);target.append(experiment);
    values(conditional.id, () => [[tr('协作功率和'),metric(data.cooperative_dbm,'dBm')],[tr('合并增益 / 最强同频站'),metric(data.cooperation_gain_db,'dB')],
      [tr('理想协作 SNR'),metric(data.cooperative_snr_db,'dB')],
      [tr('同步载频相干功率'),data.carrier_joint_zero?tr('完全相消（−∞ dBm）'):metric(data.carrier_joint_dbm,'dBm')],
      [tr('同步宽带平均'),data.wideband_joint_zero?tr('完全相消（−∞ dBm）'):metric(data.wideband_joint_dbm,'dBm')]]);
  }

  function warningText(message) {
    const text = String(message);
    if (text.startsWith('Flat ground at ENU z=0;')) return tr('地面为局部 U=0 平面；材质和单天线垂直极化参数为假定值，未用现场测量校准。');
    if (text.startsWith('Finite stochastic ray sampling')) return tr('有限射线采样可能漏检传播路径。“无有效路径”表示本次求解未找到路径，不代表实测信号严格为零。');
    if (text.startsWith('Face-only propagation:')) return tr('仅计算面上的交互：棱边和顶角周围 5 厘米内的交互已排除。');
    if (text.startsWith('Transmission uses the Sionna thin-layer')) return tr('透射采用薄材料层近似，射线方向不弯折；进入和离开建筑分别通过一层墙体。材料层厚度与建筑平面尺寸不同。');
    return text;
  }

  function renderResult() {
    const result = appState?.result;
    $('result-content').hidden = !result;
    selectedPath = null; selectedPointIndex = null; $('point-inspector').hidden = true;
    if (!result) {
      localizedText($('result-summary'), () => tr('点击左下方“计算热力图”，即可查看建筑对信号的遮挡效果。'));
      selectedUeId = null; selectedLinkId = null; if (viewer) clearGroup('paths');
      $('surface-result-section').hidden = true;
      $('probe-result-section').hidden = true;
      return;
    }
    const count = result.points?.length || 0;
    const solver = result.solver || {};
    const reqBatch = solver.batch_size;
    const effBatch = solver.effective_batch_size;
    const batchReduced = finite(effBatch) && finite(reqBatch) && effBatch < reqBatch;
    const batchText = batchReduced
      ? tr("{0} → 实际调度 {1} 点 (候选缓冲饱和自动折半)", format(reqBatch, 0), format(effBatch, 0))
      : finite(effBatch) ? tr("{0} 点", format(effBatch, 0)) : finite(reqBatch) ? tr("{0} 点", format(reqBatch, 0)) : tr('未报告');

    const isSpherical = !!result.surface_meshes?.length;

    localizedText($('result-summary'), () => isSpherical
      ? tr("球面无线电覆盖已完成，生成 {0} 层测地展开球壳，返回 {1} 个有效测量面片。", result.surface_meshes.length, format(count, 0))
      : result.mode === 'coverage'
        ? tr("三维覆盖已完成，返回 {0} 个实际空间采样点。切换显示页可筛选高度和功率。", format(count, 0))
        : tr("UE 传播计算已完成，返回 {0} 个终端结果。", format(result.ues?.length || 0, 0)));

    values('result-metadata', () => [
      [tr('计算范围'),tr("全部 {0} 个基站 · 已计入同频干扰", result.network_model?.bs_count ?? result.network_model?.bs?.length ?? project().bs.length)],
      [tr('计算后端'), result.backend || tr('未报告')],
      [tr('计算耗时'), finite(result.elapsed_s) ? `${format(result.elapsed_s, 3)} s` : tr('未报告')],
      ...(finite(effBatch) || finite(reqBatch) ? [[tr('分批点数 (配置 / 实际)'), uiString(batchText)]] : []),
      ...(finite(solver.candidate_path_budget) ? [[tr('候选路径预算'), tr("{0} 条", format(solver.candidate_path_budget, 0))]] : []),
      ...(result.mode === 'coverage' && !isSpherical ? [[tr('请求 / 计算点'), `${format(result.sampling?.requested ?? count, 0)} / ${format(result.sampling?.computed ?? count, 0)}`], [tr('建筑内排除点'), format(result.sampling?.excluded, 0)]] : [])
    ]);

    // Spherical coverage section
    $('surface-result-section').hidden = !isSpherical;
    $('sphere-display-controls').hidden = !isSpherical;
    $('show-surface-mesh-wrap').hidden = !isSpherical;
    if (isSpherical) {
      const s = result.surface || {};
      localizedText($('surface-mesh-summary'), () => tr("{0} 个半径 · {1} 面片", (result.surface_meshes || []).length, format(count, 0)));
      localizedText($('surface-mesh-note'), () => tr('每面片代表理想各向同性接收机在三角形区域内的非相干面积平均功率 (Area-averaged Noncoherent RSS)。不假定各向同性等于极化天线单点测量。'));
      values('surface-metadata', () => [
        [tr('球面中心'), coord(s.center_m || [0, 0, 25])],
        [tr('测地细分'), tr("等级 {0} ({1} 面片/球)", s.subdivisions ?? 3, 20 * 4**(s.subdivisions ?? 3))],
        [tr('计算半径 / m'), (s.radii_m || []).map(r => format(r)).join(', ')],
        [tr('请求 / 排除面片'), tr("{0} / 排除 {1} (地下 {2}，建筑 {3})", format(result.sampling?.requested ?? 0, 0), format(result.sampling?.excluded ?? 0, 0), s.below_ground_cells ?? 0, s.building_cells ?? 0)],
        [tr('独立重复样本'), tr("{0} 次 (评估抽样方差)", s.replicates ?? 1)],
        [tr('显存分块上限'), tr("{0} 射线/块", format(s.ray_chunk_size ?? 1000000, 0))]
      ]);

      // Populate radius selector in display tab
      const radii = result.surface_meshes.map(m => m.radius_m);
      const radEntries = [['all', () => tr('全部计算半径')], ...radii.map(r => [String(r), () => tr("半径 {0} m", format(r))])];
      fillOptions('display-radius', () => radEntries, String(currentRadius));
      $('radius-slider').max = Math.max(0, radii.length - 1);
      if (currentRadius !== 'all' && radii.includes(Number(currentRadius))) {
        setRadiusFilter(currentRadius);
      } else {
        setRadiusFilter('all');
      }

      // Disable coherent modes for spherical result
      $('color-mode').value = 'incoherent_dbm';
      $('color-mode').options[1].disabled = true;
      $('color-mode').options[2].disabled = true;
      $('color-mode-note').hidden = false;
    } else {
      $('color-mode').options[1].disabled = false;
      $('color-mode').options[2].disabled = false;
      $('color-mode-note').hidden = true;
    }

    // Exact probe result section
    renderProbeResultSection();

    const ues = result.ues || [];
    if (!ues.some(ue => ue.ue_id === selectedUeId)) selectedUeId = ues.find(ue => activeDeviceKind === 'ues' && ue.ue_id === selectedDeviceId)?.ue_id || ues[0]?.ue_id || null;
    fillOptions('result-ue', () => ues.length ? ues.map(ue => [ue.ue_id, `${ue.ue_id} · ${project().ues.find(d => d.id === ue.ue_id)?.name || tr('终端')}`]) : [['', tr('无 UE 计算结果')]], selectedUeId);
    $('result-ue').disabled = !ues.length;
    const warnings = result.warnings || [];
    $('result-warnings').hidden = !warnings.length; localizedText($('result-warnings'), () => warnings.map(warningText).join('\n'));
    localizedText($('scene-hash'), () => tr("场景 SHA-256：{0}", result.scene_sha256 || tr('未报告')));
    localizedAttribute($('scene-hash'), 'title', () => result.scene_sha256 || '');
    renderUeResult();
  }

  function renderProbeResultSection() {
    const probe = appState?.probe_result;
    $('probe-result-section').hidden = !probe;
    if (!probe || !probe.ues?.length) return;
    const ue = probe.ues[0];
    renderNetwork(ue,'probe-network');
    localizedText($('probe-ue-position'), () => tr("探测点坐标：{0}", coord(ue.position_m)));
    const links = ue.links || [];
    const body = $('probe-link-table'); body.replaceChildren();
    for (const link of links) {
      const row = document.createElement('tr');
      resultCell(row, () => link.bs_id);
      resultCell(row, () => powerText(link, 'incoherent_dbm', false, true));
      resultCell(row, () => powerText(link, 'coherent_dbm', false, true));
      resultCell(row, () => powerText(link, 'wideband_dbm', false, true));
      body.append(row);
    }
    const link = links[0];
    values('probe-link-metrics', () => link ? [
      [tr('非相干和'), powerText(link, 'incoherent_dbm')],
      [tr('载频相干'), powerText(link, 'coherent_dbm')],
      [tr('带宽平均'), powerText(link, 'wideband_dbm')],
      [tr('有效路径'), tr("{0} 条", format(link.path_count, 0))],
      [tr('RMS 时延扩展'), finite(link.rms_delay_ns) ? `${format(link.rms_delay_ns)} ns` : tr('无有效路径')],
      [tr('视距直达 (LoS)'), link.has_los ? tr('存在') : tr('阻挡')]
    ] : []);

    const paths = (link?.paths || []).slice(0, 30);
    const pathBody = $('probe-path-table'); pathBody.replaceChildren();
    localizedText($('probe-path-count'), () => link ? tr("{0} / {1} 条", paths.length, link.path_count) : '');
    for (const [idx, path] of paths.entries()) {
      const row = document.createElement('tr');
      resultCell(row, () => kindNames[path.kind] || path.kind);
      resultCell(row, () => finite(path.power_dbm) ? format(path.power_dbm) : tr('无'));
      resultCell(row, () => format(path.delay_ns));
      resultCell(row, () => format(path.phase_deg));
      pathBody.append(row);
    }
    localizedText($('probe-path-detail'), () => tr('单点探测由 Sionna PathSolver 精确求解，具有完整载频相位与极化响应。'));
    renderCfr(link?.cfr, link, 'probe-cfr-chart');
  }

  function renderUeResult() {
    const ue = resultUe();
    $('ue-result').hidden = !ue; $('no-ue-result').hidden = !!ue;
    if (!ue) { if (viewer) clearGroup('paths'); requestRender(); return; }
    $('result-ue').value = ue.ue_id; localizedText($('result-ue-position'), () => coord(ue.position_m));
    const links = ue.links || [], body = $('link-table'); body.replaceChildren();
    if (selectedLinkId!=='' && !links.some(link => link.bs_id === selectedLinkId)) selectedLinkId='';
    for (const link of links) {
      const row = document.createElement('tr'); row.tabIndex = 0; row.classList.toggle('selected', selectedLinkId === link.bs_id);
      localizedAttribute(row, 'aria-label', () => tr("{0}，选择此基站的路径和频率响应", link.bs_id));
      resultCell(row, () => link.bs_id); resultCell(row, () => powerText(link, 'incoherent_dbm', false, true)); resultCell(row, () => powerText(link, 'coherent_dbm', false, true)); resultCell(row, () => powerText(link, 'wideband_dbm', false, true));
      const select = () => { selectedLinkId = link.bs_id; selectedPath = null; renderUeResult(); };
      row.addEventListener('click', select); row.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } }); body.append(row);
    }
    if (!links.length) { const row = document.createElement('tr'), cell = resultCell(row, () => tr('没有基站链路结果。'), 'empty-cell'); cell.colSpan = 4; body.append(row); }
    fillOptions('result-link', () => links.length ? [['',tr("全部 {0} 基站 · 路径与频响对比", links.length)],...links.map(link => [link.bs_id, tr("{0} · {1} 条路径", link.bs_id, format(link.path_count, 0))])] : [['', tr('没有基站链路')]], selectedLinkId);
    $('result-link').disabled = !links.length;
    const link = resultLink();
    renderNetwork(ue,'ue-network',selectedLinkId);
    const displayedCount = finite(link?.displayed_path_count) ? link.displayed_path_count : (link?.paths?.length ? Math.min(link.paths.length, 50) : 0);
    const powerFractionStr = finite(link?.displayed_power_fraction) ? `${(link.displayed_power_fraction * 100).toFixed(1)}%` : null;
    values('link-metrics', () => link ? [
      [tr('非相干和'), powerText(link, 'incoherent_dbm')], [tr('载频相干'), powerText(link, 'coherent_dbm')], [tr('带宽平均'), powerText(link, 'wideband_dbm')],
      [tr('有效路径'), tr("{0} 条", format(link.path_count, 0))],
      ...(powerFractionStr ? [[tr('显示路径占比'), tr("{0} 条 ({1})", format(displayedCount, 0), powerFractionStr)]] : []),
      [tr('RMS 时延扩展'), finite(link.rms_delay_ns) ? `${format(link.rms_delay_ns)} ns` : tr('无有效路径')], [tr('直达路径'), link.has_los ? tr('存在') : tr('无')]
    ] : [[tr('查看范围'),tr("全部 {0} 个基站", links.length)],[tr('有效路径总数'),tr("{0} 条", format(links.reduce((sum,l)=>sum+(l.path_count || 0),0),0))],[tr('默认服务站'),ue.network?.serving_bs_id || tr('未解析')]]);
    renderPathTable(); renderPaths(); renderCfr(link?.cfr, selectedLinkId===''?links:link, 'cfr-chart');
  }

  function interactionObjectName(inter) {
    const name = inter.object_name || inter.object_id || tr('表面');
    const point = inter.position_m, sampling = project()?.sampling;
    if (inter.object_id === 'ground' && Array.isArray(point) && sampling && [0, 1].some(
      axis => point[axis] < sampling.min_m[axis] || point[axis] > sampling.max_m[axis])) return tr('地面（采样框外）');
    return name;
  }

  function renderPathTable() {
    const link = resultLink(), paths = resultPaths(), shown = paths, body = $('path-table'); body.replaceChildren();
    const displayed = finite(link?.displayed_path_count) ? link.displayed_path_count : shown.length;
    const total = resultLinks().reduce((sum,l)=>sum+(l.path_count || 0),0);
    const frac = finite(link?.displayed_power_fraction) ? link.displayed_power_fraction : null;
    localizedText($('path-count'), () => tr("{0} / {1} 条", format(shown.length, 0), format(total, 0)));
    let note = '';
    if (frac !== null) {
      note = tr("已绘制能量最高的 {0} / {1} 条有效路径（占非相干总功率 {2}%）。漫反射路径为蒙特卡洛随机采样，折点不满足镜像角。点击行突出显示路径及折点。", format(displayed, 0), format(total, 0), (frac * 100).toFixed(1));
    } else if (paths.length > 50 || total > paths.length) {
      note = tr("汇总使用全部 {0} 条有效路径；此处显示并绘制前 {1} 条。漫反射路径为蒙特卡洛随机采样。点击行突出显示路径及折点。", format(total, 0), format(shown.length, 0));
    } else {
      note = tr("点击行突出显示该路径及折点；漫反射路径为蒙特卡洛随机采样，折点不满足镜像角。再次点击恢复全部路径。");
    }
    localizedText($('path-note'), () => tr("{0}{1} 绿色框限定接收采样范围；框外已建模的地面仍可产生反射。", selectedLinkId==='' ? tr("全部 {0} 基站同时显示；每站最多显示最强 50 条，统计使用全部有效路径。", resultLinks().length) : '', uiString(note)));

    for (const [index, path] of shown.entries()) {
      const row = document.createElement('tr'); row.tabIndex = 0; row.classList.toggle('selected', selectedPath === index);
      localizedAttribute(row, 'aria-label', () => tr("第 {0} 条，{1}，点击查看复系数和折点", index + 1, kindNames[path.kind] || path.kind || tr('未知交互')));
      resultCell(row, () => `${selectedLinkId===''?path.bs_id+' · ':''}${kindNames[path.kind] || path.kind || tr('未知交互')}`); resultCell(row, () => finite(path.power_dbm) ? format(path.power_dbm) : tr('无有效路径')); resultCell(row, () => format(path.delay_ns)); resultCell(row, () => format(path.phase_deg));
      const select = () => { selectedPath = selectedPath === index ? null : index; renderPathTable(); renderPaths(); };
      row.addEventListener('click', select); row.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } }); body.append(row);
    }
    if (!shown.length) { const row = document.createElement('tr'), cell = resultCell(row, () => (link?.path_count || 0) > 0 ? tr('本次结果未包含可显示的路径折点。') : tr('在当前机制和求解参数下无有效路径。'), 'empty-cell'); cell.colSpan = 4; body.append(row); }
    const path = selectedPath == null ? null : shown[selectedPath];
    const detail = $('path-detail');
    detail.replaceChildren();
    if (path) {
      const real = finite(path.beta_real) ? path.beta_real.toExponential(4) : tr('未报告'), imag = finite(path.beta_imag) ? Math.abs(path.beta_imag).toExponential(4) : tr('未报告');
      const summaryDiv = document.createElement('div');
      summaryDiv.className = 'path-detail-summary';
      localizedText(summaryDiv, () => tr("β = {0} {1} j{2} · 时延 {3} ns · 功率 {4} · 相位 {5}°", real, path.beta_imag < 0 ? '−' : '+', imag, format(path.delay_ns), finite(path.power_dbm) ? format(path.power_dbm) + ' dBm' : '—', format(path.phase_deg)));
      detail.append(summaryDiv);

      if (finite(path.length_m) || finite(path.delay_length_error_m) || (path.geometry_warnings && path.geometry_warnings.length)) {
        const diagDiv = document.createElement('div');
        diagDiv.className = 'path-detail-diag';
        const parts = [];
        if (finite(path.length_m)) parts.push(tr("几何光程 {0} m", format(path.length_m)));
        if (finite(path.delay_length_error_m)) parts.push(tr("时延光程差 {0} mm", format(path.delay_length_error_m * 1000, 2)));
        if (path.geometry_valid === false && path.geometry_warnings?.length) parts.push(tr("【提示】{0}", path.geometry_warnings.join('；')));
        localizedText(diagDiv, () => parts.map(uiString).join(' · '));
        detail.append(diagDiv);
      }

      if (Array.isArray(path.interactions) && path.interactions.length > 0) {
        const list = document.createElement('div');
        list.className = 'path-bends-list';
        path.interactions.forEach((inter, idx) => {
          const row = document.createElement('div');
          row.className = 'path-bend-item';
          const typeName = kindNames[inter.type] || inter.type || tr('交互');
          const objName = interactionObjectName(inter);
          const matName = inter.material ? ` [${inter.material}]` : '';
          const pos = Array.isArray(inter.position_m) ? `(${inter.position_m.map(v => format(v, 2)).join(', ')})` : '';
          const err = finite(inter.surface_error_m) ? tr(" · 贴表误差 {0} mm", format(inter.surface_error_m * 1000, 1)) : '';
          const diffNote = inter.type === 'diffuse' ? tr(' (漫反射采样，非镜像角)') : '';
          localizedText(row, () => tr("折点 {0}：{1} @ {2}{3} {4}{5}{6}", idx + 1, uiString(typeName), objName, matName, pos, uiString(err), uiString(diffNote)));
          list.append(row);
        });
        detail.append(list);
      } else {
        const fallbackDiv = document.createElement('div');
        fallbackDiv.className = 'path-detail-fallback';
        const vertices = (path.vertices_m || []).map(p => `(${p.map(v => format(v, 2)).join(', ')})`).join(' → ');
        const inters = (path.interaction_types || []).map(k => kindNames[k] || k).join(' → ');
        localizedText(fallbackDiv, () => tr("{0}ENU 路径点（含 BS/UE）/ m：{1}。", inters ? tr("交互类型：{0}。", inters) : '', vertices || tr('未报告')));
        detail.append(fallbackDiv);
      }
    } else {
      localizedText(detail, () => tr('相位来自复数 CIR 系数；频响按频偏计算，不再重复叠加载频传播相位。漫反射路径由蒙特卡洛随机采样生成，折点不满足镜面反射镜像角。'));
    }
  }

  function renderPaths() {
    if (!viewer || !frame) return;
    clearGroup('paths');
    const paths = resultPaths();
    for (const [index, path] of paths.entries()) {
      const vertices = path.vertices_m || [];
      if (vertices.length < 2) continue;
      const chosen = selectedPath === index, faded = selectedPath != null && !chosen;
      const stationColor=window.RANMultiBS.colors[(resultUe()?.links || []).findIndex(l=>l.bs_id===path.bs_id)%window.RANMultiBS.colors.length];
      addEntity('paths', { polyline: { positions: vertices.map(world), arcType: C.ArcType.NONE, width: chosen ? 4 : path.kind === 'los' ? 3 : 2, material: C.Color.fromCssColorString(selectedLinkId===''?stationColor:pathColors[path.kind] || '#8e948d').withAlpha(faded ? 0.25 : 0.95) } });
      if (chosen) for (const [vertexIndex, position] of vertices.entries()) {
        addEntity('paths', { position: world(position), point: { pixelSize: 6, color: C.Color.fromCssColorString(pathColors[path.kind] || '#8e948d'), outlineColor: C.Color.WHITE, outlineWidth: 1, disableDepthTestDistance: Number.POSITIVE_INFINITY } });
        let bendText = '';
        if (vertexIndex === 0) bendText = `BS · ${path.bs_id || tr('基站')}`;
        else if (vertexIndex === vertices.length - 1) bendText = `UE · ${selectedUeId || tr('终端')}`;
        else if (Array.isArray(path.interactions) && path.interactions[vertexIndex - 1]) {
          const inter = path.interactions[vertexIndex - 1];
          const typeName = kindNames[inter.type] || inter.type || tr('交互');
          const objName = interactionObjectName(inter);
          bendText = tr("折点 {0} · {1} ({2})", vertexIndex, typeName, objName);
        } else if (path.interaction_types?.[vertexIndex - 1]) {
          const t = path.interaction_types[vertexIndex - 1];
          bendText = tr("折点 {0} · {1}", vertexIndex, kindNames[t] || t);
        } else bendText = tr("折点 {0}", vertexIndex);
        label('paths', position, bendText, '#9fc7b8', [0, -14]);
      }
    }
    groupVisible('paths', $('show-paths').checked); requestRender();
  }

  function svgElement(name, attributes = {}, text) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const [key, value] of Object.entries(attributes)) { if (key === 'aria-label' || key === 'title') localizedAttribute(el, key, () => value); else el.setAttribute(key, String(value)); }
    if (text != null) localizedText(el, () => uiString(text));
    return el;
  }
  function renderCfr(cfr, link, targetId = 'cfr-chart') {
    const chart = $(targetId); chart.replaceChildren();
    const links=Array.isArray(link)?link:link?[{...link,cfr:cfr || link.cfr}]:[];
    const curves=window.RANMultiBS.cfrSeries(links);
    const samples = curves.flatMap(curve=>curve.offsets.map((offset,index)=>({x:finite(offset)?offset/1e6:null,y:curve.gains[index]}))).filter(p=>finite(p.x)&&finite(p.y));
    if (!samples.length) { const empty = document.createElement('p'); empty.className = 'help'; localizedText(empty, () => !link?.path_count ? tr('无有效路径，未绘制频率响应。') : tr('本次结果没有有限的频响幅度可供绘制。')); chart.append(empty); return; }
    let xmin = Math.min(...samples.map(p => p.x)), xmax = Math.max(...samples.map(p => p.x)), ymin = Math.min(...samples.map(p => p.y)), ymax = Math.max(...samples.map(p => p.y));
    if (xmin === xmax) { xmin -= 0.5; xmax += 0.5; }
    const pad = Math.max(3, (ymax - ymin) * 0.12); ymin -= pad; ymax += pad;
    const left = 49, right = 284, top = 17, bottom = 135;
    const x = value => left + (value - xmin) / (xmax - xmin) * (right - left), y = value => bottom - (value - ymin) / (ymax - ymin) * (bottom - top);
    const svg = svgElement('svg', { viewBox: '0 0 300 177', role: 'img', 'aria-label': tr("{0} 的独立带内频率响应对比，横轴 MHz，纵轴 dB", links.map(l=>l.bs_id).join('、')) });
    svg.append(svgElement('title', {}, tr('带内频率响应')));
    for (const tick of [ymin, (ymin + ymax) / 2, ymax]) {
      svg.append(svgElement('line', { x1: left, x2: right, y1: y(tick), y2: y(tick), stroke: '#273430', 'stroke-width': 1 }));
      svg.append(svgElement('text', { x: left - 7, y: y(tick) + 4, 'text-anchor': 'end', fill: '#8e948d' }, format(tick, 1)));
    }
    svg.append(svgElement('path', { d: `M${left},${top} V${bottom} H${right}`, fill: 'none', stroke: '#3d4e48', 'stroke-width': 1 }));
    for (const tick of [xmin, (xmin + xmax) / 2, xmax]) svg.append(svgElement('text', { x: x(tick), y: bottom + 18, 'text-anchor': 'middle', fill: '#8e948d' }, format(tick, 2)));
    svg.append(svgElement('text', { x: 52, y: 11, fill: '#8e948d' }, 'dB'), svgElement('text', { x: 167, y: 173, 'text-anchor': 'middle', fill: '#8e948d' }, tr('频偏 / MHz')));
    for(const curve of curves) {
      let segment = [];
      function flush() {
        if(segment.length>1)svg.append(svgElement('polyline',{points:segment.join(' '),fill:'none',stroke:curve.color,'stroke-width':2,'stroke-linejoin':'round','data-bs-id':curve.bs_id}));
        else if(segment.length===1){const [cx,cy]=segment[0].split(',');svg.append(svgElement('circle',{cx,cy,r:3,fill:curve.color,'data-bs-id':curve.bs_id}));}
        segment=[];
      }
      for(let i=0;i<curve.offsets.length;i++) {if(finite(curve.offsets[i])&&finite(curve.gains[i]))segment.push(`${x(curve.offsets[i]/1e6)},${y(curve.gains[i])}`);else flush();}flush();
    }
    chart.append(svg);
    if(links.length>1) {
      const legend=document.createElement('div');legend.className='cfr-legend';
      for(const curve of curves) {const item=document.createElement('span');item.style.color=curve.color;localizedText(item, () => curve.bs_id+(curve.gains.some(finite)?'':tr(' · 未解析')));legend.append(item);}
      chart.append(legend);
    }
  }

  function powerColor(power, opacity) {
    const scale=coverageScale();
    const position = Math.max(0, Math.min(1, (power-scale.low)/(scale.high-scale.low))) * (rampHex.length - 1);
    const index = Math.min(Math.floor(position), rampHex.length - 2);
    return C.Color.lerp(C.Color.fromCssColorString(rampHex[index]), C.Color.fromCssColorString(rampHex[index + 1]), position - index, new C.Color()).withAlpha(opacity);
  }
  function displayOptions() {
    const optional = id => $(id).value.trim() === '' ? null : number(id);
    const heat=$('coverage-mode').value==='heatmap',agg=$('heatmap-aggregation').value;
    const size = heat ? 5 : number('point-size'), opacity = number('point-opacity'), lower = heat ? null : optional('display-zmin'), upper = heat ? null : optional('display-zmax'), threshold = heat ? null : optional('minimum-power');
    if (size < 1 || size > 20) throw new Error(tr('点大小应在 1–20 px 之间。'));
    if (opacity < 0.05 || opacity > 1) throw new Error(tr('透明度应在 0.05–1 之间。'));
    if (lower != null && lower < 0 || upper != null && upper < 0) throw new Error(tr('高度切片不能低于局部地面 0 m。'));
    if (lower != null && upper != null && lower > upper) throw new Error(tr('高度下切不能大于上切。'));
    if (heat) {
      if(agg==='slice') {if(number('heatmap-height')<0)throw new Error(tr('接收高度不能低于地面。'));}
      else {
        const zMin=number('heatmap-zmin'),zMax=number('heatmap-zmax');
        if(zMin<0 || zMax<zMin)throw new Error(tr('热力图高度范围无效。'));
      }
      if(agg==='coverage')number('heatmap-threshold');
    }
    return { size, opacity, lower, upper, threshold: threshold ?? -Infinity, key: $('color-mode').value, bsId: $('display-bs').value,metric:$('coverage-metric').value };
  }

  function coverageScale(ignoreAggregation=false) {
    const metric=$('coverage-metric').value;
    if (!ignoreAggregation && $('coverage-mode').value==='heatmap' && $('heatmap-aggregation').value==='coverage') return {name:tr('达标比例下界'),unit:'%',low:0,high:100};
    if (metric==='received') return {name:powerNames[$('color-mode').value],unit:'dBm',low:-110,high:-40};
    const ranges={sinr_db:[-10,30],snr_db:[0,50],cooperation_gain_db:[0,10]};
    const [low,high]=ranges[metric] || [-110,-40];
    return {name:networkNames[metric],unit:metric.endsWith('_dbm')?'dBm':'dB',low,high};
  }
  function displayValue(point,options) {return window.RANHeatmap.valueAt(point,{metric:options.metric,powerKey:options.key,bsId:options.bsId});}
  function stationScopeLabel(options) {
    return options.metric==='total_received_dbm'?tr("全部 {0} 站满功率和", project().bs.length):options.bsId || tr('全网 · 自动选服务站');
  }
  function updateCoverageLegend() {
    const s=coverageScale();localizedText($('legend-title'), () => uiString(s.name));localizedText($('legend-unit'), () => s.unit);
    $('legend-ticks').replaceChildren(...[0,1/3,2/3,1].map(t=>{const el=document.createElement('span');localizedText(el, () => format(s.low+(s.high-s.low)*t,0));return el;}));
    $('heatmap-legend-note').hidden=$('coverage-mode').value!=='heatmap';
  }
  function syncCoverageControls() {
    const heat=$('coverage-mode').value==='heatmap',agg=$('heatmap-aggregation').value,metric=$('coverage-metric').value;
    $('heatmap-controls').hidden=!heat;$('heatmap-view').setAttribute('aria-pressed',String(heat));
    $('heatmap-height-wrap').hidden=false;$('heatmap-range-wrap').hidden=agg==='slice';
    $('heatmap-threshold-wrap').hidden=agg!=='coverage';localizedText($('heatmap-threshold-unit'), () => coverageScale(true).unit);
    $('color-mode').disabled=metric!=='received';$('network-metric-note').hidden=false;
    $('display-bs').disabled=metric==='total_received_dbm';
    const spacing=$('heatmap-spacing').value==='project'?appState?.project?.sampling.spacing_m.slice(0,2):[Number($('heatmap-spacing').value),Number($('heatmap-spacing').value)];
    localizedText($('quick-heatmap-note'), () => tr("接收高度 {0} m · 每格 {1} m", $('heatmap-height').value || '—', spacing?.map(v=>format(v)).join(' × ') || '—'));
    updateCoverageLegend();
  }
  function switchCoverageMode() {
    selectedHeatmapCell=null;selectedPointIndex=null;$('point-inspector').hidden=true;
    syncCoverageControls();queueCloudUpdate();
    if ($('coverage-mode').value==='heatmap') camera(true);
  }

  function rebuildHeatmap() {
    if (!viewer || !frame) return;
    if (heatmapPrimitive) viewer.scene.primitives.remove(heatmapPrimitive);
    heatmapPrimitive=null;heatmapProjection=null;heatmapCanvas=null;
    if ($('coverage-mode').value!=='heatmap') {releaseHeatmapMaterial();return;}
    const result=appState?.result;
    if (!result?.points?.length || result.surface_meshes?.length) {
      releaseHeatmapMaterial();
      localizedText($('heatmap-status'), () => result?.surface_meshes?.length?tr('球面数据不能作高度列统计；可按输入高度计算单层体网格。'):tr('先计算体网格覆盖，或按输入高度计算单层。'));
      localizedText($('point-cloud-count'), () => tr('尚无可投影的体网格结果'));return;
    }
    try {
      const options=displayOptions(),sampling=project().sampling;
      const aggregation=$('heatmap-aggregation').value;
      heatmapProjection=window.RANHeatmap.project(result.points,sampling,{aggregation,
        height:aggregation==='slice'?number('heatmap-height'):1.5,
        zMin:aggregation==='slice'?0:number('heatmap-zmin'),zMax:aggregation==='slice'?0:number('heatmap-zmax'),
        threshold:aggregation==='coverage'?number('heatmap-threshold'):-80,metric:options.metric,powerKey:options.key,bsId:options.bsId});
      const map=heatmapProjection,lo=sampling.min_m,hi=sampling.max_m,spanE=hi[0]-lo[0],spanN=hi[1]-lo[1];
      if (spanE<=0 || spanN<=0) throw new Error(tr('热力图 E/N 范围须有面积。'));
      const canvas=heatmapCanvas=document.createElement('canvas');
      canvas.width=Math.min(2048,Math.max(128,map.count[0]*8));canvas.height=Math.min(2048,Math.max(128,map.count[1]*8));
      const ctx=canvas.getContext('2d'),x=e=>(e-lo[0])/spanE*canvas.width,y=n=>(hi[1]-n)/spanN*canvas.height;
      for (const cell of map.cells) {
        const [e0,n0,e1,n1]=cell.bounds;
        ctx.fillStyle=finite(cell.value)?powerColor(cell.value,options.opacity).toCssColorString():cell.zero?powerColor(coverageScale().low,options.opacity).toCssColorString():'#58635d';
        ctx.fillRect(x(e0),y(n1),x(e1)-x(e0),y(n0)-y(n1));
      }
      // Remove each solid footprint independently: overlapping buildings form
      // a union, while a building's courtyard remains uncovered by its mask.
      ctx.globalCompositeOperation='destination-out';ctx.fillStyle='#000';
      for (const b of project().buildings) {
        ctx.beginPath();
        for (const ring of [b.footprint_m,...(b.holes_m || [])]) {
          ring.forEach(([e,n],i)=>i?ctx.lineTo(x(e),y(n)):ctx.moveTo(x(e),y(n)));ctx.closePath();
        }
        ctx.fill('evenodd');
      }
      ctx.globalCompositeOperation='source-over';
      const vertices=[lo[0],lo[1],.09,hi[0],lo[1],.09,hi[0],hi[1],.09,lo[0],hi[1],.09];
      const geometry=new C.Geometry({attributes:{
        position:new C.GeometryAttribute({componentDatatype:C.ComponentDatatype.DOUBLE,componentsPerAttribute:3,values:new Float64Array(vertices)}),
        normal:new C.GeometryAttribute({componentDatatype:C.ComponentDatatype.FLOAT,componentsPerAttribute:3,values:new Float32Array([0,0,1,0,0,1,0,0,1,0,0,1])}),
        st:new C.GeometryAttribute({componentDatatype:C.ComponentDatatype.FLOAT,componentsPerAttribute:2,values:new Float32Array([0,0,1,0,1,1,0,1])})},
        indices:new Uint16Array([0,1,2,0,2,3]),primitiveType:C.PrimitiveType.TRIANGLES,boundingSphere:C.BoundingSphere.fromVertices(vertices)});
      // A stable fabric type prevents anonymous entries in Cesium's static
      // cache. Reuse the material; its update destroys the previous texture.
      // Cache the template with a placeholder, never a particular canvas.
      if(!heatmapMaterial) heatmapMaterial=new C.Material({fabric:{type:'RANHeatmap',uniforms:{image:C.Material.DefaultImageId,imageSize:new C.Cartesian2(1,1)},
        source:'czm_material czm_getMaterial(czm_materialInput materialInput) { czm_material m = czm_getDefaultMaterial(materialInput); vec2 uv = (floor(clamp(materialInput.st, vec2(0.0), vec2(0.999999)) * imageSize) + 0.5) / imageSize; vec4 p = texture(image, uv); m.diffuse = p.rgb; m.alpha = p.a; return m; }'},translucent:true});
      heatmapMaterial.uniforms.image=canvas;heatmapMaterial.uniforms.imageSize=new C.Cartesian2(canvas.width,canvas.height);
      heatmapPrimitive=viewer.scene.primitives.add(new C.Primitive({geometryInstances:new C.GeometryInstance({geometry,modelMatrix:frame,id:{kind:'heatmap'}}),asynchronous:false,
        appearance:new C.MaterialAppearance({material:heatmapMaterial,flat:true,faceForward:true,closed:false,translucent:true})}));
      const label=$('heatmap-aggregation').selectedOptions[0].textContent;
      const heights=map.options.aggregation==='slice'?tr("请求 {0} m · 实际计算层 {1} m", format(map.options.height), format(map.actualHeight)):`${format(map.options.zMin)}–${format(map.options.zMax)} m · ${label}`;
      const cellSize=`${format(sampling.spacing_m[0])} × ${format(sampling.spacing_m[1])} m`;
      localizedText($('heatmap-status'), () => tr("当前每格 {0}。{1}。{2} 列，{3} 个未解析样本。", cellSize, uiString(heights), format(map.cells.length,0), format(map.missingSamples,0)));
      localizedText($('point-cloud-count'), () => tr("每格 {0} · {1} · {2} / {3} 列 · {4}", cellSize, uiString(heights), format(map.finiteCells,0), format(map.cells.length,0), stationScopeLabel(options)));
      localizedText($('display-filter-status'), () => tr('热力图显示所选高度统计；点云高度裁剪与显示下限仅用于三维点云。'));
      if (selectedHeatmapCell) {
        const cell=map.byKey.get(selectedHeatmapCell);if(cell) inspectHeatmap(cell);else {$('point-inspector').hidden=true;selectedHeatmapCell=null;}
      }
    } catch(error) {heatmapProjection=null;localizedText($('heatmap-status'), () => errorText(error));localizedText($('point-cloud-count'), () => tr('热力图参数无效'));}
    applyLayerVisibility();requestRender();
  }

  function releaseHeatmapMaterial() {
    if(heatmapMaterial) {
      heatmapMaterial.uniforms.image=C.Material.DefaultImageId;
      heatmapMaterial.destroy();heatmapMaterial=null;
    }
  }

  function inspectHeatmap(cell) {
    const map=heatmapProjection;if(!map)return;
    selectedHeatmapCell=`${cell.ix},${cell.iy}`;selectedPointIndex=null;
    const target=$('point-inspector');target.replaceChildren();const s=coverageScale();
    for (const text of [tr("平面网格 · E {0}–{1} / N {2}–{3} m", format(cell.bounds[0]), format(cell.bounds[2]), format(cell.bounds[1]), format(cell.bounds[3])),
      tr("此格 {0} × {1} m（边界格会裁切）", format(cell.bounds[2]-cell.bounds[0]), format(cell.bounds[3]-cell.bounds[1])),
      tr("实际样本高度 {0}–{1} m", format(cell.zMin), format(cell.zMax)),`${s.name}：${finite(cell.value)?`${format(cell.value)} ${s.unit}`:cell.zero?tr('完全相消（−∞ dBm）'):tr('未解析 / 缺测')}`,
      tr("室外样本 {0} · 有值 {1} · 未解析 {2}", cell.total, cell.valid, cell.missing)]) {const p=document.createElement('div');localizedText(p, () => uiString(text));target.append(p);}
    const row=appState.result.points[cell.index];
    if (row?.network) {
      const n=map.options.bsId?row.network.per_bs.find(n=>n.bs_id===map.options.bsId):row.network;
      const p=document.createElement('div');localizedText(p, () => tr("代表样本 {0} · {1} · SINR {2} dB", coord(row.position_m), row.network.serving_bs_id || tr('无服务站'), format(n?.sinr_db)));
      target.append(p);
    }
    const button=document.createElement('button');button.className='primary probe-trigger-btn';button.type='button';localizedText(button, () => tr("在代表样本 {0} m 高度精算", format(row.position_m[2])));
    button.addEventListener('click',guard(()=>runExactProbe(row.position_m)));target.append(button);target.hidden=false;
    localizedText($('footer-selection'), () => tr("热力图单元 {0},{1} · {2} {3} {4}", cell.ix, cell.iy, uiString(s.name), format(cell.value), s.unit));
  }

  function rebuildCoveragePrimitives() {
    rebuildPointCloud();
    rebuildSurfaceMesh();
  }

  function rebuildPointCloud() {
    if (!viewer || !frame) return;
    if (pointCloud) viewer.scene.primitives.remove(pointCloud);
    pointCloud = null; pointRefs = [];
    const points = appState?.result?.points || [];
    if (points.length) {
      pointCloud = viewer.scene.primitives.add(new C.PointPrimitiveCollection({ modelMatrix: frame, blendOption: C.BlendOption.OPAQUE_AND_TRANSLUCENT }));
      for (const [index, point] of points.entries()) pointRefs.push(pointCloud.add({ position: new C.Cartesian3(...point.position_m), pixelSize: 5, color: C.Color.TRANSPARENT, show: false, id: { kind: 'point', index }, outlineWidth: 0 }));
    }
    updatePointCloud(); applyLayerVisibility();
  }

  function rebuildSurfaceMesh() {
    if (!viewer || !frame) return;
    if (surfaceMeshPrimitive) {
      viewer.scene.primitives.remove(surfaceMeshPrimitive);
      surfaceMeshPrimitive = null;
    }
    const result = appState?.result;
    if ($('coverage-mode').value==='heatmap') return;
    if (!result?.surface_meshes?.length) return;

    let options;
    try { options = displayOptions(); } catch { return; }

    const meshes = result.surface_meshes;
    const activeMeshes = currentRadius === 'all' ? meshes : meshes.filter(m => String(m.radius_m) === String(currentRadius));
    const allVertices = [], allColors = [], allIndices = [];

    for (const mesh of activeMeshes) {
      const v = mesh.vertices_m || [];
      const faces = mesh.faces || [];
      const pointIndices = mesh.point_indices || [];

      for (let f = 0; f < faces.length; f += 1) {
        const [i0, i1, i2] = faces[f];
        const pIndex = pointIndices[f];
        if (pIndex == null) continue;
        const pt = result.points[pIndex];
        if (!pt) continue;

        const link = bestLink(pt.links || [], options.key, options.bsId);
        const val = displayValue(pt,options);
        const height = pt.position_m[2];
        const visible = finite(val) && val >= options.threshold && (options.lower == null || height >= options.lower) && (options.upper == null || height <= options.upper);
        if (!visible) continue;

        const color = powerColor(val, options.opacity);
        const r = Math.round(color.red * 255), g = Math.round(color.green * 255), b = Math.round(color.blue * 255), a = Math.round(color.alpha * 255);

        const baseIdx = allVertices.length / 3;
        for (const vertIdx of [i0, i1, i2]) {
          allVertices.push(...v[vertIdx]);
          allColors.push(r, g, b, a);
        }
        allIndices.push(baseIdx, baseIdx + 1, baseIdx + 2);
      }
    }

    if (allVertices.length >= 3) {
      const geom = new C.Geometry({
        attributes: {
          position: new C.GeometryAttribute({ componentDatatype: C.ComponentDatatype.DOUBLE, componentsPerAttribute: 3, values: new Float64Array(allVertices) }),
          color: new C.GeometryAttribute({ componentDatatype: C.ComponentDatatype.UNSIGNED_BYTE, componentsPerAttribute: 4, normalize: true, values: new Uint8Array(allColors) })
        },
        indices: allVertices.length / 3 > 65535 ? new Uint32Array(allIndices) : new Uint16Array(allIndices),
        primitiveType: C.PrimitiveType.TRIANGLES,
        boundingSphere: C.BoundingSphere.fromVertices(allVertices)
      });

      surfaceMeshPrimitive = viewer.scene.primitives.add(new C.Primitive({
        geometryInstances: new C.GeometryInstance({ geometry: geom, modelMatrix: frame }),
        asynchronous: false,
        appearance: new C.PerInstanceColorAppearance({ flat: true, translucent: options.opacity < 1.0, closed: false })
      }));
    }
    applyLayerVisibility();
  }

  function queueCloudUpdate() {
    if (cloudUpdateQueued) return;
    cloudUpdateQueued = true;
    requestAnimationFrame(() => {
      cloudUpdateQueued = false;
      updatePointCloud();
      rebuildSurfaceMesh();
    });
  }

  function updatePointCloud() {
    const isSpherical = !!appState?.result?.surface_meshes?.length;
    syncCoverageControls();
    if ($('coverage-mode').value==='heatmap') {
      for(const point of pointRefs) point.show=false;
      rebuildHeatmap();applyLayerVisibility();return;
    }
    if(heatmapPrimitive) viewer.scene.primitives.remove(heatmapPrimitive);
    heatmapPrimitive=null;heatmapProjection=null;heatmapCanvas=null;releaseHeatmapMaterial();
    if(isSpherical && $('coverage-metric').value==='received') localizedText($('legend-title'), () => tr('球面面积平均非相干功率'));
    let options;
    try { options = displayOptions(); localizedText($('display-filter-status'), () => ''); $('display-filter-status').classList.remove('error-text'); }
    catch (error) { localizedText($('display-filter-status'), () => errorText(error)); $('display-filter-status').classList.add('error-text'); return; }
    const points = appState?.result?.points || [];
    let shown = 0;
    const meshMode = $('surface-display-mode')?.value === 'mesh';

    for (let i = 0; i < pointRefs.length; i += 1) {
      const source = points[i], point = pointRefs[i], value = displayValue(source,options), height = source.position_m[2];
      const radiusMatches = !isSpherical || currentRadius === 'all' || String(source.radius_m) === String(currentRadius);
      const isVisible = radiusMatches && finite(value) && value >= options.threshold && (options.lower == null || height >= options.lower) && (options.upper == null || height <= options.upper);

      // In spherical mesh mode, centroid points in pointCloud are hidden so filled facets take precedence
      point.show = isVisible && (!isSpherical || !meshMode);
      point.pixelSize = options.size;
      if (isVisible) {
        point.color = powerColor(value, options.opacity);
        shown += 1;
      }
    }
    if (!appState?.result) localizedText($('point-cloud-count'), () => tr('尚无三维覆盖结果'));
    else if (!points.length) localizedText($('point-cloud-count'), () => appState.result.mode === 'ue' ? tr('本次为 UE 计算，不含空间点云') : tr('本次结果没有室外空间采样点'));
    else if (isSpherical) {
      localizedText($('point-cloud-count'), () => tr("显示 {0} / {1} 球面面片 · {2} · {3}", format(shown, 0), format(points.length, 0), currentRadius === 'all' ? tr('全部半径') : tr("半径 {0}m", format(Number(currentRadius))), stationScopeLabel(options)));
    } else {
      localizedText($('point-cloud-count'), () => tr("显示 {0} / {1} 点 · {2} · 无有限功率处留空", format(shown, 0), format(points.length, 0), stationScopeLabel(options)));
    }
    localizedText($('display-filter-status'), () => points.length ? tr("当前显示 {0} 个有效场强单元。", format(shown, 0)) : tr('运行三维覆盖后可筛选空间采样点。'));
    if (pointCloud) pointCloud.show = $('show-points').checked && (!isSpherical || !meshMode);
    if (selectedPointIndex != null) inspectPoint(selectedPointIndex);
    requestRender();
  }

  function inspectPoint(index) {
    const point = appState?.result?.points?.[index]; if (!point) return;
    const options = displayOptions(), link = bestLink(point.links || [], options.key, options.bsId);
    selectedPointIndex = index;
    const inspector = $('point-inspector'); inspector.replaceChildren();

    const isSurfaceCell = point.sample_kind === 'surface_cell';
    const titleText = isSurfaceCell
      ? tr("球面面片 #{0} · 半径 {1} m · 面积 {2} m²", point.cell_id ?? index, format(point.radius_m), format(point.area_m2))
      : tr("采样点 · {0}", coord(point.position_m));

    const lines = [
      titleText,
      tr("坐标：{0}", coord(point.position_m)),
      link ? `${link.bs_id} · ${powerNames[options.key]} ${powerText(link, options.key)}` : tr('所选基站在此采样点无有效路径')
    ];
    if (point.network) {
      const n=options.bsId?point.network.per_bs.find(n=>n.bs_id===options.bsId):point.network;
      lines.push(tr("网络（非相干口径）：服务站 {0} · SINR {1} dB · 干扰 {2} dBm", point.network.serving_bs_id || tr('未解析'), format(n?.sinr_db), format(n?.interference_dbm)));
    }
    if (isSurfaceCell) {
      lines.push(tr('【注】各向同性非相干面积平均，不代表单个极化接收机相干合成。'));
    } else {
      if (link) lines.push(tr("非相干 {0} · 相干 {1}", powerText(link, 'incoherent_dbm'), powerText(link, 'coherent_dbm')));
      if (link) lines.push(tr("带宽平均 {0} · {1} 条路径", powerText(link, 'wideband_dbm'), format(link.path_count, 0)));
    }

    for (const text of lines) {
      if (!text) continue; const row = document.createElement('div'); localizedText(row, () => uiString(text)); inspector.append(row);
    }

    // Add Exact Probe button for outdoor valid cells
    const probeBtn = document.createElement('button');
    probeBtn.type = 'button';
    probeBtn.id = 'btn-exact-probe';
    probeBtn.className = 'primary probe-trigger-btn';
    localizedText(probeBtn, () => tr('对此处发起单点精算探测 (Exact Probe)'));
    localizedAttribute(probeBtn, 'title', () => tr('调用 PathSolver 进行单天线极化对准精确多径与 CIR 求解'));
    probeBtn.addEventListener('click', () => runExactProbe(point.position_m));
    inspector.append(probeBtn);

    inspector.hidden = false;
    localizedText($('footer-selection'), () => isSurfaceCell
      ? tr("球面面片 {0} · 半径 {1} m · {2}", point.cell_id ?? index, format(point.radius_m), coord(point.position_m))
      : tr("采样点 {0} · {1}", index + 1, coord(point.position_m)));
  }

  function updateRadiusSliderLabel() {
    const meshes = appState?.result?.surface_meshes || [];
    const slider = $('radius-slider');
    const labelEl = $('radius-slider-label');
    if (!meshes.length) { localizedText(labelEl, () => '—'); return; }
    if (currentRadius === 'all') {
      localizedText(labelEl, () => tr('全部'));
      return;
    }
    const idx = Number(slider.value);
    const m = meshes[idx] || meshes.find(item => String(item.radius_m) === String(currentRadius));
    localizedText(labelEl, () => m ? `${format(m.radius_m)} m` : '—');
  }

  function setRadiusFilter(radiusValue) {
    currentRadius = radiusValue === 'all' ? 'all' : Number(radiusValue);
    $('display-radius').value = String(radiusValue);
    const meshes = appState?.result?.surface_meshes || [];
    const idx = meshes.findIndex(m => String(m.radius_m) === String(radiusValue));
    if (idx >= 0) $('radius-slider').value = idx;
    updateRadiusSliderLabel();
    queueCloudUpdate();
  }

  function toggleRadiusPlay() {
    const meshes = appState?.result?.surface_meshes || [];
    if (!meshes.length) return;
    if (isPlayingRadius) {
      clearInterval(radiusPlayTimer);
      radiusPlayTimer = null;
      isPlayingRadius = false;
      localizedText($('radius-play-btn'), () => tr('▶ 播放扩展'));
    } else {
      isPlayingRadius = true;
      localizedText($('radius-play-btn'), () => tr('⏸ 暂停'));
      let curr = Number($('radius-slider').value) || 0;
      radiusPlayTimer = setInterval(() => {
        curr = (curr + 1) % meshes.length;
        $('radius-slider').value = curr;
        setRadiusFilter(meshes[curr].radius_m);
      }, 900);
    }
  }

  function initTooltips() {
    const tooltipEl = $('accessible-tooltip');
    if (!tooltipEl) return;
    let activeTarget = null, activeField = null, showTimer = null, hideTimer = null, isHoveringTooltip = false, suppressedByEscape = false;

    function findHelp(el) {
      if (!el || el === document.body || el === tooltipEl) return null;
      let field = null;

      if (el.dataset?.preset) {
        const key = `preset-grid-${el.dataset.preset}`;
        if (PARAM_HELP[key]) return { key, help: PARAM_HELP[key], target: el, field: el };
      }
      if (el.id?.startsWith('preset-radii-')) {
        if (PARAM_HELP[el.id]) return { key: el.id, help: PARAM_HELP[el.id], target: el, field: el };
      }

      if (el.tagName === 'LABEL') {
        const forId = el.getAttribute('for');
        field = forId ? $(forId) : el.querySelector('input, select, textarea, button');
        const key = field?.id || forId;
        if (key && PARAM_HELP[key]) return { key, help: PARAM_HELP[key], target: el, field: field || el };
      }

      const parentLabel = el.closest('label');
      if (parentLabel) {
        field = parentLabel.querySelector('input, select, textarea, button') || el;
        const key = field?.id || el.id;
        if (key && PARAM_HELP[key]) return { key, help: PARAM_HELP[key], target: el, field: field || el };
      }

      const control = el.closest('button, input, select, textarea, [data-mode], [data-preset]');
      if (control) {
        const key = control.id;
        if (key && PARAM_HELP[key]) return { key, help: PARAM_HELP[key], target: control, field: control };
      }

      if (el.id && PARAM_HELP[el.id]) return { key: el.id, help: PARAM_HELP[el.id], target: el, field: el };
      return null;
    }

    function positionTooltip(target) {
      if (!tooltipEl || tooltipEl.hidden) return;
      const rect = target.getBoundingClientRect();
      const tipRect = tooltipEl.getBoundingClientRect();
      const margin = 8;
      let top, left;

      if (rect.bottom + tipRect.height + margin <= window.innerHeight) {
        top = rect.bottom + margin;
      } else if (rect.top - tipRect.height - margin >= 0) {
        top = rect.top - tipRect.height - margin;
      } else {
        top = Math.max(margin, Math.min(window.innerHeight - tipRect.height - margin, (window.innerHeight - tipRect.height) / 2));
      }

      left = rect.left;
      if (left + tipRect.width + margin > window.innerWidth) {
        left = window.innerWidth - tipRect.width - margin;
      }
      if (left < margin) left = margin;

      tooltipEl.style.top = `${Math.round(top)}px`;
      tooltipEl.style.left = `${Math.round(left)}px`;
    }

    function show(match, immediate = false) {
      if (suppressedByEscape && !immediate) return;
      clearTimeout(hideTimer);
      clearTimeout(showTimer);
      const executeShow = () => {
        const { help, target, field } = match;
        if (activeTarget && activeTarget !== target) activeTarget.removeAttribute('aria-describedby');
        if (activeField && activeField !== field) activeField.removeAttribute('aria-describedby');
        activeTarget = target;
        activeField = field || target;
        target.setAttribute('aria-describedby', 'accessible-tooltip');
        if (activeField && activeField !== target) {
          activeField.setAttribute('aria-describedby', 'accessible-tooltip');
        }
        localizedText($('tooltip-title'), () => help.title);
        localizedText($('tooltip-tag'), () => help.tag || '');
        $('tooltip-tag').hidden = !help.tag;
        localizedText($('tooltip-desc'), () => help.desc || '');
        localizedText($('tooltip-meta'), () => help.meta ? tr("基准/单位：{0}", help.meta) : '');
        $('tooltip-meta').hidden = !help.meta;
        localizedText($('tooltip-impact'), () => help.impact ? tr("调整影响：{0}", help.impact) : '');
        $('tooltip-impact').hidden = !help.impact;
        tooltipEl.hidden = false;
        tooltipEl.setAttribute('aria-hidden', 'false');
        positionTooltip(target);
      };
      if (immediate) executeShow();
      else showTimer = setTimeout(executeShow, 150);
    }

    function hide(immediate = false) {
      clearTimeout(showTimer);
      clearTimeout(hideTimer);
      const executeHide = () => {
        if (isHoveringTooltip && !immediate) return;
        if (activeTarget) {
          activeTarget.removeAttribute('aria-describedby');
          activeTarget = null;
        }
        if (activeField) {
          activeField.removeAttribute('aria-describedby');
          activeField = null;
        }
        tooltipEl.hidden = true;
        tooltipEl.setAttribute('aria-hidden', 'true');
      };
      if (immediate) executeHide();
      else hideTimer = setTimeout(executeHide, 250);
    }

    tooltipEl.addEventListener('mouseenter', () => { isHoveringTooltip = true; clearTimeout(hideTimer); });
    tooltipEl.addEventListener('mouseleave', () => { isHoveringTooltip = false; hide(false); });

    document.addEventListener('mouseover', event => {
      suppressedByEscape = false;
      if (tooltipEl.contains(event.target)) return;
      const match = findHelp(event.target);
      if (match) {
        if (match.target.title) { match.target.dataset.savedTitle = match.target.title; match.target.removeAttribute('title'); }
        show(match, false);
      } else if (!isHoveringTooltip) hide(false);
    });

    document.addEventListener('mouseout', event => {
      if (tooltipEl.contains(event.target)) return;
      const related = event.relatedTarget;
      if (tooltipEl.contains(related)) return;
      const match = findHelp(event.target);
      if (match?.target.dataset.savedTitle) {
        localizedAttribute(match.target, 'title', () => match.target.dataset.savedTitle);
        delete match.target.dataset.savedTitle;
      }
      if (!related || !findHelp(related)) hide(false);
    });

    document.addEventListener('focusin', event => {
      suppressedByEscape = false;
      const match = findHelp(event.target);
      if (match) show(match, true);
    });

    document.addEventListener('focusout', () => hide(true));

    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !tooltipEl.hidden) {
        event.preventDefault();
        suppressedByEscape = true;
        isHoveringTooltip = false;
        hide(true);
      }
    });

    window.addEventListener('resize', () => { if (activeTarget && !tooltipEl.hidden) positionTooltip(activeTarget); }, { passive: true });
    document.addEventListener('scroll', () => { if (activeTarget && !tooltipEl.hidden) positionTooltip(activeTarget); }, { passive: true, capture: true });
  }

  function wireControls() {
    $('dismiss-message').addEventListener('click', clearMessage);
    for (const side of ['left', 'right']) {
      const buttons = [...document.querySelectorAll(`[data-${side}-tab]`)];
      for (const [index, button] of buttons.entries()) {
        button.addEventListener('click', () => tab(side, button.dataset[side === 'left' ? 'leftTab' : 'rightTab']));
        button.addEventListener('keydown', event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
          tab(side, buttons[nextIndex].dataset[side === 'left' ? 'leftTab' : 'rightTab'], true);
        });
      }
    }
    async function fileAction(action) {
      clearMessage();
      const frameScene = ['new', 'open'].includes(action); if (frameScene) shouldFrameNext = true;
      try {
        const response = await command(action);
        if (!response.cancelled && response.exported_path) setMessage(tr("已导出 {0}：{1}", String(response.format || '').toUpperCase(), response.exported_path), 'info');
      } finally { if (frameScene) shouldFrameNext = false; }
    }
    for (const [id, action] of [['project-new', 'new'], ['project-open', 'open'], ['project-save', 'save'], ['project-save-as', 'save_as'], ['export-result', 'export']]) $(id).addEventListener('click', guard(() => fileAction(action)));
    $('workspace-strategy').addEventListener('click', guard(() => command('strategy', { action: 'get' })));
    $('project-form').addEventListener('submit', guard(event => { event.preventDefault(); return saveProjectForm(); }));
    $('building-form').addEventListener('submit', guard(event => { event.preventDefault(); return saveBuildingForm(); }));
    $('device-form').addEventListener('submit', guard(event => { event.preventDefault(); return saveDevice(); }));
    $('simulation-form').addEventListener('submit', guard(event => { event.preventDefault(); return commit(simulationProject()); }));
    $('create-building-form').addEventListener('submit', guard(event => { event.preventDefault(); return createBuilding(); }));
    $('import-form').addEventListener('submit', guard(async event => {
      event.preventDefault(); requireForm('import-form'); clearMessage(); shouldFrameNext = true;
      try {
        const response = await command('import_geojson', { source_crs: $('import-crs').value, height_field: $('height-field').value.trim(), base_field: $('base-field').value.trim(), default_height_m: number('default-height') });
        const report = response.report || response.import_report;
        if (!response.cancelled && report) setMessage(tr("已导入 {0} 栋建筑；{1} 栋使用默认高度。{2}", report.imported_count, report.default_height_count, (report.warnings || []).join('；')), 'info');
      } finally { shouldFrameNext = false; }
    }));
    $('building-picker').addEventListener('change', event => { if (event.target.value && appState?.project) selectBuilding(event.target.value); });
    $('building-delete').addEventListener('click', guard(async () => { const b = projectBuilding(); if (!b) return; const next = clone(project()); next.buildings = next.buildings.filter(item => item.id !== b.id); await commit(next); selection = null; renderSelection(); }));
    $('create-shape').addEventListener('change', updateCreateShape);
    $('finish-drawing').addEventListener('click', guard(finishDrawing));
    $('undo-drawing').addEventListener('click', () => { drawing.pop(); renderDrawing(); });
    $('cancel-drawing').addEventListener('click', () => setMode('select'));
    $('device-kind').addEventListener('change', event => { if (!appState?.project) return; if (mode !== 'select') setMode('select'); const kind = event.target.value; selectDevice(kind, project()[kind].find(d => d.id === deviceMemory[kind])?.id || project()[kind][0]?.id || null); });
    $('new-device').addEventListener('click', guard(() => {
      if (project()[activeDeviceKind].length >= (activeDeviceKind === 'bs' ? 5 : 64)) throw new Error(tr("当前已达到 {0} 个{1}上限。", activeDeviceKind === 'bs' ? 5 : 64, activeDeviceKind === 'bs' ? tr('基站') : tr('终端')));
      setMode('select'); selectDevice(activeDeviceKind, null);
    }));
    $('place-device').addEventListener('click', guard(() => { requireForm('device-form'); setMode(`place-${activeDeviceKind}`); }));
    $('delete-device').addEventListener('click', guard(async () => { const device = currentDevice(); if (!device) return; const next = clone(project()); next[activeDeviceKind] = next[activeDeviceKind].filter(d => d.id !== device.id); await commit(next); selection = null; renderDevices(); }));
    $('device-up').addEventListener('input', updateModeInstruction);

    // Sampling Switch & Presets
    $('sampling-mode-grid').addEventListener('click', () => setSamplingMode('grid'));
    $('sampling-mode-sphere').addEventListener('click', () => setSamplingMode('sphere'));
    $('surface-center-mode').addEventListener('change', () => { updateSurfaceCenterVisibility(); previewSampling(); });
    $('surface-center-bs').addEventListener('change', previewSampling);
    for (const id of ['surface-radii', 'surface-subdivisions', 'surface-replicates', 'surface-chunk-size', 'surface-center-e', 'surface-center-n', 'surface-center-u']) {
      $(id).addEventListener('input', previewSampling);
    }
    for (const preset of ['dense', 'standard', 'wide']) {
      $(`preset-radii-${preset}`)?.addEventListener('click', event => {
        const val = event.target.dataset.radii;
        if (val) {
          $('surface-radii').value = val;
          previewSampling();
        }
      });
    }

    $('simulation-form').addEventListener('input', previewSampling);
    $('run-coverage').addEventListener('click', guard(() => runSimulation('coverage')));
    $('run-ue').addEventListener('click', guard(() => runSimulation('ue')));
    $('cancel-job').addEventListener('click', guard(async () => {
      if (!appState?.busy) return;
      clearMessage();
      await command('cancel');
      if (appState?.busy) { appState.progress = { stage: 'cancelling' }; updateProgress(); }
    }));
    $('clear-probe').addEventListener('click', () => {
      if (appState) appState.probe_result = null;
      renderProbeResultSection();
      renderProbeInScene();
      requestRender();
    });

    // Display controls
    $('surface-display-mode').addEventListener('change', queueCloudUpdate);
    $('display-radius').addEventListener('change', event => setRadiusFilter(event.target.value));
    $('radius-slider').addEventListener('input', event => {
      const meshes = appState?.result?.surface_meshes || [];
      const idx = Number(event.target.value);
      if (meshes[idx]) setRadiusFilter(meshes[idx].radius_m);
    });
    $('radius-play-btn').addEventListener('click', toggleRadiusPlay);
    $('coverage-mode').addEventListener('change',switchCoverageMode);
    $('heatmap-view').addEventListener('click',guard(showHeatmap));
    $('run-heatmap').addEventListener('click',guard(runHeatmapPlane));
    for(const id of ['heatmap-aggregation','coverage-metric']) $(id).addEventListener('change',()=>{if(id==='coverage-metric')coverageMetricExplicit=true;syncCoverageControls();queueCloudUpdate();});
    for(const id of ['heatmap-height','heatmap-zmin','heatmap-zmax','heatmap-threshold']) $(id).addEventListener('input',()=>{syncCoverageControls();queueCloudUpdate();});
    $('heatmap-spacing').addEventListener('change',syncCoverageControls);
    $('run-heatmap-plane').addEventListener('click',guard(runHeatmapPlane));

    for (const button of document.querySelectorAll('[data-mode]')) button.addEventListener('click', guard(() => setMode(button.dataset.mode)));
    $('overview').addEventListener('click', () => camera()); $('top-view').addEventListener('click', () => camera(true));
    $('result-ue').addEventListener('change', event => { selectedUeId = event.target.value; selectedPath = null; selectedLinkId = null; renderUeResult(); });
    $('result-link').addEventListener('change', event => { selectedLinkId = event.target.value; selectedPath = null; renderUeResult(); });
    for (const id of ['color-mode', 'display-bs']) $(id).addEventListener('change', queueCloudUpdate);
    for (const id of ['point-size', 'point-opacity', 'display-zmin', 'display-zmax', 'minimum-power']) $(id).addEventListener('input', queueCloudUpdate);
    for (const id of ['show-points', 'show-surface-mesh', 'show-paths', 'show-buildings', 'show-devices', 'show-sampling', 'show-axes', 'show-grid']) $(id).addEventListener('change', applyLayerVisibility);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && mode !== 'select') { event.preventDefault(); setMode('select'); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (!locked()) fileAction(event.shiftKey ? 'save_as' : 'save').catch(error => setMessage(errorText(error))); }
    });
    for (const preset of [20, 10, 5]) $(`preset-grid-${preset}`)?.addEventListener('click', () => applyGridPreset(preset));
    initTooltips();
    updateCreateShape(); syncCoverageControls(); updateControls();
  }

  // Automation interface
  const automationDisplay = { power: 'color-mode', bs_id: 'display-bs', point_size: 'point-size', opacity: 'point-opacity', z_min: 'display-zmin', z_max: 'display-zmax', minimum_power: 'minimum-power', radius: 'display-radius', surface_display_mode: 'surface-display-mode',coverage_mode:'coverage-mode',metric:'coverage-metric',aggregation:'heatmap-aggregation',heat_height:'heatmap-height',heat_spacing:'heatmap-spacing',heat_z_min:'heatmap-zmin',heat_z_max:'heatmap-zmax',heat_threshold:'heatmap-threshold' };
  const automationStringDisplay=['power','bs_id','radius','surface_display_mode','coverage_mode','metric','aggregation','heat_spacing'];
  const automationLayers = { points: 'show-points', surface_mesh: 'show-surface-mesh', paths: 'show-paths', buildings: 'show-buildings', devices: 'show-devices', sampling: 'show-sampling', axes: 'show-axes', grid: 'show-grid' };
  const automationForms = { project: 'project-form', building: 'building-form', device: 'device-form', simulation: 'simulation-form', create_building: 'create-building-form' };
  function automationObject(value, label) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(tr("{0}需要对象。", label));
    return value;
  }
  function automationSnapshot() {
    const selectedTab = side => [...document.querySelectorAll(`[data-${side}-tab]`)].find(button => button.getAttribute('aria-selected') === 'true')?.dataset[side === 'left' ? 'leftTab' : 'rightTab'];
    const display = {};
    for (const [key, id] of Object.entries(automationDisplay)) display[key] = automationStringDisplay.includes(key) ? $(id).value : $(id).value === '' ? null : Number($(id).value);
    const position = viewer && inverseFrame ? C.Matrix4.multiplyByPoint(inverseFrame, viewer.camera.positionWC, new C.Cartesian3()) : null;
    const groundVertices = groundPrimitive?.geometryInstances?.geometry?.attributes?.position?.values;
    let renderedGround = null;
    if (groundVertices?.length) {
      let loE = Infinity, loN = Infinity, hiE = -Infinity, hiN = -Infinity, top = -Infinity;
      for (let i = 0; i < groundVertices.length; i += 3) {
        loE = Math.min(loE, groundVertices[i]); hiE = Math.max(hiE, groundVertices[i]);
        loN = Math.min(loN, groundVertices[i + 1]); hiN = Math.max(hiN, groundVertices[i + 1]); top = Math.max(top, groundVertices[i + 2]);
      }
      renderedGround = { min_m: [loE, loN, top], max_m: [hiE, hiN, top] };
    }
    return {
      ready: document.body.dataset.ready === 'true', revision: appState?.revision, busy: !!appState?.busy,
      all_bs:{scope_text:$('calculation-scope').textContent,ue_selection:$('result-link').value,
        ue_scope:resultUe()?(selectedLinkId===''?'all':'single'):null,
        cfr_stations:[...new Set([...$('cfr-chart').querySelectorAll('[data-bs-id]')].map(e=>e.dataset.bsId))],
        path_stations:[...new Set(resultPaths().map(p=>p.bs_id))],
        metric_visible:!!$('coverage-metric').getClientRects().length && !$('coverage-metric').closest('details'),
        interference_sources:$('ue-network').querySelectorAll('.interference-sources tbody tr').length},
      project_name: $('project-title').textContent, project_path: $('project-path').textContent, dirty: !!appState?.dirty,
      runtime: $('runtime-status').textContent, error: runtimeError, stage: $('job-stage').textContent,
      footer_selection: $('footer-selection').textContent,
      message: { visible: !$('global-message').hidden, text: $('global-message-text').textContent },
      scroll: { left: document.querySelector('.editor-panel .panel-scroll').scrollTop, right: document.querySelector('.results-panel .panel-scroll').scrollTop },
      tabs: { left: selectedTab('left'), right: selectedTab('right') }, mode, drawing: clone(drawing),
      selection: selection ? clone(selection) : null, selected_building: selectedBuildingId,
      device_kind: activeDeviceKind, selected_device: selectedDeviceId, selected_ue: selectedUeId,
      selected_link: selectedLinkId, selected_path: selectedPath, selected_point: selectedPointIndex,
      sampling_mode: samplingMode, current_radius: currentRadius,
      heatmap: {active:$('coverage-mode').value==='heatmap',rendered:!!heatmapPrimitive,actual_height:heatmapProjection?.actualHeight ?? null,
        cell_size_m:heatmapProjection?.sampling.spacing_m.slice(0,2) || null,
        columns:heatmapProjection?.cells.length || 0,finite_columns:heatmapProjection?.finiteCells || 0,missing_samples:heatmapProjection?.missingSamples || 0,
        selected_cell:selectedHeatmapCell,status:$('heatmap-status').textContent,unit:$('legend-unit').textContent,
        preview:heatmapProjection?.cells.slice(0,5) || []},
      primary_heatmap_action:{label:$('run-heatmap').textContent,visible:!!$('run-heatmap').getClientRects().length,disabled:$('run-heatmap').disabled},
      advanced:{height_statistics:$('heatmap-advanced').open,display:$('display-advanced').open},
      network_text:$('ue-network').textContent,probe_network_text:$('probe-network').textContent,
      orthographic:viewer?.camera.frustum instanceof C.OrthographicFrustum,
      ground_bounds_m: renderedGround,
      display, layers: Object.fromEntries(Object.entries(automationLayers).map(([key, id]) => [key, $(id).checked])),
      counts: { buildings: appState?.project?.buildings.length || 0, bs: appState?.project?.bs.length || 0, ues: appState?.project?.ues.length || 0 },
      has_result: !!appState?.result, has_probe_result: !!appState?.probe_result,
      visible_points: pointRefs.filter(point => point.show).length,
      path_rows: $('path-table').querySelectorAll('tr.selected, tr[tabindex]').length, cfr_svg: !!$('cfr-chart').querySelector('svg'),
      path_detail: $('path-detail').textContent,
      camera: position ? { position_m: [position.x, position.y, position.z], heading_deg: C.Math.toDegrees(viewer.camera.heading), pitch_deg: C.Math.toDegrees(viewer.camera.pitch), roll_deg: C.Math.toDegrees(viewer.camera.roll) } : null,
      forms: Object.fromEntries(Object.entries(automationForms).map(([name, id]) => [name, Object.fromEntries([...$(id).querySelectorAll('input[id], select[id]')].map(field => [field.id, field.type === 'checkbox' ? field.checked : field.value]))])),
      width: innerWidth, height: innerHeight,
      overflow: document.documentElement.scrollWidth > innerWidth || document.documentElement.scrollHeight > innerHeight
    };
  }
  function automationSetView(data) {
    const display = automationObject(data.display ?? {}, 'display'), layers = automationObject(data.layers ?? {}, 'layers');
    const previous = Object.fromEntries(Object.values(automationDisplay).map(id => [id, $(id).value]));
    for (const [key, value] of Object.entries(display)) {
      if (!Object.hasOwn(automationDisplay, key)) throw new Error(tr("未知显示参数：{0}", key));
      if (automationStringDisplay.includes(key)) {
        if (typeof value !== 'string' || ![...$(automationDisplay[key]).options].some(option => !option.disabled && option.value === value)) throw new Error(tr("{0}不是当前可选值。", key));
      } else if (!(value === null && ['z_min', 'z_max', 'minimum_power'].includes(key)) && !finite(value)) throw new Error(tr("{0}需要有限数字。", key));
    }
    for (const [key, value] of Object.entries(layers)) if (!Object.hasOwn(automationLayers, key) || typeof value !== 'boolean') throw new Error(tr("图层 {0}需要布尔值。", key));
    try {
      for (const [key, value] of Object.entries(display)) $(automationDisplay[key]).value = value ?? '';
      displayOptions();
    } catch (error) {
      for (const [id, value] of Object.entries(previous)) $(id).value = value;
      throw error;
    }
    if(display.metric!==undefined)coverageMetricExplicit=true;
    for (const [key, value] of Object.entries(layers)) $(automationLayers[key]).checked = value;
    if (display.radius !== undefined) {
      setRadiusFilter(display.radius);
    } else {
      updateRadiusSliderLabel();
    }
    if(display.coverage_mode!==undefined) {selectedHeatmapCell=null;selectedPointIndex=null;$('point-inspector').hidden=true;}
    updatePointCloud(); rebuildSurfaceMesh(); applyLayerVisibility();
    if(display.coverage_mode==='heatmap') camera(true);
  }
  function automationSelect(data) {
    if (data.kind === 'clear') {
      selection = null; selectedPointIndex = null; selectedHeatmapCell = null; $('point-inspector').hidden = true; renderSelection();
    } else if (data.kind === 'building') {
      if (!project().buildings.some(b => b.id === data.id)) throw new Error(tr('没有找到指定建筑。'));
      selectBuilding(data.id); tab('left', 'buildings');
    } else if (['bs', 'ues'].includes(data.kind)) {
      if (data.id !== null && !project()[data.kind].some(d => d.id === data.id)) throw new Error(tr('没有找到指定设备。'));
      selectDevice(data.kind, data.id); tab('left', 'devices');
    } else if(data.kind==='heatmap') {
      const cell=heatmapProjection?.byKey.get(`${data.ix},${data.iy}`);if(!cell)throw new Error(tr('没有该热力图单元。'));inspectHeatmap(cell);
    } else if (data.kind === 'point' || data.kind === 'cell') {
      if (!Number.isInteger(data.index) || !appState?.result?.points?.[data.index]) throw new Error(tr('采样点序号无效。'));
      selection = null; renderSelection(); inspectPoint(data.index);
    } else if (data.kind === 'probe') {
      if (!appState?.probe_result) throw new Error(tr('尚无单点探测结果。'));
      tab('right', 'results');
    } else if (data.kind === 'result') {
      const ue = appState?.result?.ues?.find(item => item.ue_id === data.ue_id);
      const link = ue?.links?.find(item => item.bs_id === data.bs_id);
      const allStations=data.bs_id==null;
      if (!ue || (!allStations && !link)) throw new Error(tr('没有找到指定 UE 与基站的计算链路。'));
      const available=allStations?window.RANMultiBS.pathEntries(ue.links):link.paths?.slice(0,50) || [];
      const index = data.path_index ?? null;
      if (index !== null && (!Number.isInteger(index) || index < 0 || index >= available.length)) throw new Error(tr('路径序号超出可显示范围。'));
      selectedUeId = ue.ue_id; selectedLinkId = allStations?'':link.bs_id; selectedPath = index;
      renderUeResult(); tab('right', 'results');
    } else throw new Error(tr('kind 需要 clear、building、bs、ues、point、cell、probe 或 result。'));
  }
  async function automationForm(data) {
    if (locked()) throw new Error(tr('当前不能修改表单，请等待本机操作完成。'));
    const formId = automationForms[data.name];
    if (!formId) throw new Error(tr('未知表单。'));
    if (data.submit !== undefined && typeof data.submit !== 'boolean') throw new Error(tr('submit 需要布尔值。'));
    const form = $(formId), values = automationObject(data.values ?? {}, 'values');
    const fields = new Map([...form.querySelectorAll('input[id], select[id]')].map(field => [field.id, field]));
    const previous = new Map([...fields].map(([id, field]) => [id, field.type === 'checkbox' ? field.checked : field.value]));
    for (const [id, value] of Object.entries(values)) {
      const field = fields.get(id);
      if (!field || field.disabled) throw new Error(tr("字段 {0}不可编辑。", id));
      if (field.type === 'checkbox' ? typeof value !== 'boolean' : !['string', 'number'].includes(typeof value) || typeof value === 'number' && !finite(value)) throw new Error(tr("字段 {0}的值无效。", id));
      if (field.tagName === 'SELECT' && ![...field.options].some(option => option.value === String(value))) throw new Error(tr("字段 {0}的选项无效。", id));
    }
    try {
      for (const [id, value] of Object.entries(values)) { const field = fields.get(id); if (field.type === 'checkbox') field.checked = value; else field.value = String(value); }
      if (data.name === 'create_building') updateCreateShape();
      if (!form.checkValidity()) throw new Error(tr('请修正表单中超出范围或缺失的值。'));
      if (data.name === 'simulation') previewSampling();
      if (data.name === 'device') updateModeInstruction();
      if (data.submit) {
        const actions = { project: saveProjectForm, building: saveBuildingForm, device: saveDevice, simulation: () => commit(simulationProject()), create_building: createBuilding };
        await actions[data.name]();
      }
    } catch (error) {
      for (const [id, value] of previous) { const field = fields.get(id); if (field.type === 'checkbox') field.checked = value; else field.value = value; }
      if (data.name === 'create_building') updateCreateShape();
      if (data.name === 'simulation') previewSampling();
      throw error;
    }
  }
  async function automationView(data) {
    if (data.expected_revision !== undefined && data.expected_revision !== appState.revision) throw new Error(tr("项目修订已改变，当前为 {0}。", appState.revision));
    const method = data.method || 'get';
    if (method === 'set') automationSetView(data);
    else if(method==='heatmap_plane') await runHeatmapPlane();
    else if (method === 'select') automationSelect(data);
    else if (method === 'form') await automationForm(data);
    else if (method === 'tab') {
      if (!['left', 'right'].includes(data.side) || ![...document.querySelectorAll(`[data-${data.side}-tab]`)].some(button => button.dataset[data.side === 'left' ? 'leftTab' : 'rightTab'] === data.name)) throw new Error(tr('未知侧栏或标签页。'));
      tab(data.side, data.name);
    } else if (method === 'mode') {
      if (!['select', 'draw', 'place-bs', 'place-ues'].includes(data.value)) throw new Error(tr('未知交互模式。'));
      setMode(data.value);
    } else if (method === 'sampling_mode') {
      setSamplingMode(data.value);
    } else if (method === 'message') {
      if (data.operation !== 'dismiss') throw new Error(tr('提示操作仅支持 dismiss。'));
      clearMessage();
    } else if (method === 'scroll') {
      if (!['left', 'right'].includes(data.side)) throw new Error(tr('滚动侧栏需要 left 或 right。'));
      const panel = document.querySelector(data.side === 'left' ? '.editor-panel .panel-scroll' : '.results-panel .panel-scroll');
      if (data.target !== undefined) {
        const targets = ['cfr-chart', 'path-table', 'path-detail', 'ue-result', 'result-metadata', 'result-summary', 'display-filter-status', 'surface-result-section', 'probe-result-section', ...Object.values(automationForms), 'scene-counts'];
        if (!targets.includes(data.target) || !panel.contains($(data.target)) || !$(data.target).getClientRects().length) throw new Error(tr('目标需要位于当前侧栏可见的标签页。'));
        panel.scrollTop += $(data.target).getBoundingClientRect().top - panel.getBoundingClientRect().top;
      } else {
        if (!finite(data.top) || data.top < 0) throw new Error(tr('滚动位置需要非负有限数字。'));
        panel.scrollTop = data.top;
      }
    } else if (method === 'camera') {
      if (['overview', 'top'].includes(data.preset)) camera(data.preset === 'top');
      else {
        const position = data.position_m;
        if (!Array.isArray(position) || position.length !== 3 || !position.every(finite) || position[2] < 0) throw new Error(tr('相机位置需要有效 ENU 三维坐标。'));
        const heading = data.heading_deg ?? 0, pitch = data.pitch_deg ?? -45, roll = data.roll_deg ?? 0;
        if (![heading, pitch, roll].every(finite) || pitch < -90 || pitch > 90) throw new Error(tr('相机朝向无效。'));
        viewer.camera.setView({ destination: world(position), orientation: { heading: C.Math.toRadians(heading), pitch: C.Math.toRadians(pitch), roll: C.Math.toRadians(roll) } });
      }
    } else if (method === 'drawing') {
      if (locked()) throw new Error(tr('当前不能绘制，请等待本机操作结束。'));
      if (data.operation === 'begin') setMode('draw');
      else if (data.operation === 'cancel') setMode('select');
      else {
        if (mode !== 'draw') throw new Error(tr('请先开始多边形绘制。'));
        if (data.operation === 'add') {
          const p = data.position_m;
          if (!Array.isArray(p) || p.length !== 2 || !p.every(finite)) throw new Error(tr('绘图顶点需要两个有限 EN 坐标。'));
          drawing.push([p[0], p[1], 0]); renderDrawing();
        } else if (data.operation === 'undo') { drawing.pop(); renderDrawing(); }
        else if (data.operation === 'finish') await finishDrawing();
        else throw new Error(tr('未知绘图操作。'));
      }
    } else if (method !== 'get') throw new Error(tr('未知视图操作。'));
    requestRender();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return automationSnapshot();
  }
  window.ranAutomation = Object.freeze({
    execute(ticket, data) {
      if (!bridge?.automationReply) return false;
      (async () => {
        let response;
        try {
          if (!appState?.project || !viewer || document.body.dataset.ready !== 'true') {
            response = { ok: false, error_code: 'VIEW_NOT_READY', error: tr('三维界面尚未就绪') };
          } else response = { ok: true, view: await automationView(data) };
        } catch (error) { response = { ok: false, error_code: 'VIEW_ERROR', error: errorText(error) }; }
        bridge.automationReply(ticket, JSON.stringify(response));
      })();
      return true;
    }
  });

  window.RANI18n.onChange(() => {
    updateRuntime(); updateProgress();
    if (appState?.project) { updateModeInstruction(); previewSampling(); }
    for (const [entity, caption] of sceneCaptions) if (entity.label) entity.label.text = caption();
    requestRender();
  });

  async function start() {
    wireControls();
    try { initScene(); } catch (error) { setMessage(errorText(error)); localizedText($('map-placeholder').firstElementChild, () => errorText(error)); }
    if (!window.qt?.webChannelTransport || typeof window.QWebChannel !== 'function') {
      setMessage(tr('当前环境没有本机 Qt 桥接。请通过 RANWorkbench.exe 启动；场景、文件与 GPU 计算需要本机桌面宿主。此页面不会生成示例计算结果。'));
      localizedText($('map-placeholder').firstElementChild, () => tr('请通过 RANWorkbench.exe 打开独立工作台。当前页面未连接本机项目或计算进程。')); return;
    }
    try {
      const channel = await new Promise(resolve => new window.QWebChannel(window.qt.webChannelTransport, resolve));
      bridge = channel.objects.bridge;
      if (!bridge?.command || !bridge?.event?.connect) throw new Error(tr('本机桥接未提供约定的 command / event 接口。'));
      bridge.event.connect(handleEvent);
      await command('get_state');
    } catch (error) { setMessage(errorText(error)); }
  }
  start().catch(error => setMessage(errorText(error)));
})();
