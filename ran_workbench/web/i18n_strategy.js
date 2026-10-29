/* Presentation vocabulary only; graph keys and experiment values stay intact. */
(() => {
  'use strict';
  const I = window.RANI18n;
  const entries = {
    'NR 策略工作台': 'NR Strategy Workbench', 'RAN 工作台 / 系统': 'RAN WORKBENCH / SYSTEM',
    '连接本机工作台…': 'Connecting to the local workbench…', '实验操作': 'Experiment actions',
    '新建': 'New', '打开实验': 'Open experiment', '保存': 'Save', '导出结果': 'Export results',
    '撤销': 'Undo', '重做': 'Redo', '放弃草稿 / 读取最新': 'Discard draft / Reload latest',
    '校验流程': 'Validate graph', '取消': 'Cancel', '运行实验': 'Run experiment',
    '读取实验…': 'Loading experiment…', 'CUDA 城市信道': 'CUDA city channel',
    '实验时间与资源': 'Experiment duration and resources', '时长': 'Duration', '子载波间隔': 'Subcarrier spacing',
    '随机种子': 'Random seed', '信道': 'Channel', '真实 CUDA / 城市场景': 'CUDA / City scene',
    '合成演示 · 非城市传播': 'Synthetic demo · No city propagation', '更多资源设置': 'More resource settings',
    '高级资源设置': 'Advanced resource settings', '设备与功能层': 'Devices and functional layers',
    '查看对象': 'Show entity', '选择模块调整策略。': 'Select a module to adjust its policy.',
    '从输出圆点连接到兼容输入；选中连线可断开。': 'Connect an output port to a compatible input; select a connection to disconnect it.',
    '标准与实现范围': 'Standards and implementation scope',
    'TS 38.214 V18.4.0：CQI/MCS table 1、TBS、两端口 Type-I 单面板码本。调度、反馈承载与 HARQ 为系统级抽象。': 'TS 38.214 V18.4.0: CQI/MCS table 1, TBS, and the two-port Type-I single-panel codebook. Scheduling, feedback transport, and HARQ are system-level abstractions.',
    '曲线覆盖 MCS 3–28、CBS 24–2000 bit；EESM 校准出处不完整。这里的结果不等于商用设备实测。': 'Curves cover MCS 3–28 and CBS 24–2000 bits; EESM calibration provenance is incomplete. Results do not represent commercial device measurements.',
    '支持 1/2 发射与接收端口、RI 1/2、标准两端口 PMI；高维码本和完整协议栈尚未实现。': 'Supports 1/2 transmit and receive ports, RI 1/2, and standard two-port PMI. Higher-dimensional codebooks and a complete protocol stack are not implemented.',
    '可编辑策略流程': 'Editable strategy graph', '策略流程': 'Strategy graph', '缩小流程图': 'Zoom out', '放大流程图': 'Zoom in',
    '适应画布': 'Fit to canvas', '断开连线': 'Disconnect', '流程图，可滚动和缩放': 'Strategy graph; pan and zoom',
    'BS、信道和 UE 策略节点': 'BS, channel, and UE strategy nodes',
    '拖动空白处平移 · 滚轮缩放 · 拖动模块调整布局': 'Drag the background to pan · Scroll to zoom · Drag modules to arrange',
    '已同步': 'Synchronized', '参数检查': 'Parameters', '选择一个模块': 'Select a module', '应用修改': 'Apply changes',
    '本次传输': 'Selected transmission', '实验结果': 'Experiment results', '结果概览': 'Overview', '时隙事件': 'Slot events',
    'A/B 对比': 'A/B comparison', '尚未运行': 'Not run yet', '保存为基准 A': 'Save as baseline A', '对比当前 B': 'Compare current B',
    '让策略经过一次完整闭环': 'Run the complete strategy loop',
    '先选择 BS 或 UE 模块，调整策略后运行。这里将显示包交付吞吐量、时延、反馈成本和资源使用。': 'Select a BS or UE module, adjust its policy, and run. Results show delivered throughput, latency, feedback cost, and resource usage.',
    '事件': 'Event', '全部': 'All', '传输 / 重传': 'Transmission / Retransmission', 'CSI 到达': 'CSI arrival',
    '反馈丢弃': 'Feedback drop', 'ACK/NACK 到达': 'ACK/NACK arrival', '时隙': 'Slot',
    '完成一次实验后保存为 A，修改策略再运行 B。场景、轨迹、时间、资源和种子需保持一致。': 'Save a completed experiment as A, then change the policy and run B. Keep the scene, trajectories, duration, resources, and seed identical.',
    '轮询 · Round Robin': 'Round robin', '比例公平 · PF': 'Proportional fair · PF', '最大 CQI': 'Maximum CQI',
    '依据已到达 CSI': 'Use received CSI', '固定 MCS': 'Fixed MCS', '使用已到达的 RI / PMI': 'Use received RI / PMI',
    '满缓冲业务': 'Full-buffer traffic', '恒定速率': 'Constant rate', '宽带 CQI': 'Wideband CQI',
    '研究子带差分 CQI': 'Research subband differential CQI', '周期报告': 'Periodic reporting',
    'CQI / RI / PMI 变化触发': 'CQI / RI / PMI change trigger', 'NR 曲线参考模型': 'NR reference-curve model', '理想无误差 · 对照': 'Ideal error-free control',
    '整个网络': 'Entire network', '无线环境与观察': 'Radio environment and observation', '无线信道 / 观察': 'Radio channel / Observation',
    '输入': 'Input', '输出': 'Output', '此模块汇总实际结果，没有独立策略参数。': 'This module aggregates results and has no independent policy parameters.',
    '{0} 模块 / {1} 连线': '{0} modules / {1} connections', '{0} {1}: {2}': '{0} {1}: {2}',
    '已选 {0}.{1}；点击兼容的输入圆点。': 'Selected {0}.{1}; click a compatible input port.',
    '连线已修改；运行前将校验类型、归属和时序。': 'Connection changed; types, ownership, and timing will be validated before running.',
    '外部配置已经更新，旧草稿不能覆盖它。请保留需要的内容，再选择“放弃草稿 / 读取最新”。': 'The configuration changed externally; this draft cannot overwrite it. Keep any edits you need, then choose “Discard draft / Reload latest”.',
    '策略已应用，下次运行使用当前流程。': 'Strategy applied. The next run will use the current graph.',
    '实验进行中…': 'Experiment running…', '实验进行中': 'Experiment running', '当前配置尚无有效结果': 'No valid results for the current configuration',
    '净吞吐 Mbps': 'Goodput Mbps', '时延 P95 ms': 'P95 latency ms', '首次 BLER': 'First-transmission BLER', 'CSI 年龄 slot': 'CSI age (slots)', '反馈 bit': 'Feedback bits',
    '交付 {0} 个完整包；重传 {1} 次。曲线范围外取值：{2}。EESM 与当前场景 / 接收机尚未校准。': 'Delivered {0} complete packets; {1} retransmissions. Curve extrapolation: {2}. EESM is not calibrated for this scene / receiver.',
    '已使用声明的边界策略': 'declared boundary policy used', '未使用': 'not used', '累计成功交付 / kB': 'Cumulative successful delivery / kB',
    '完成本次实验后显示包交付、时延、反馈成本与资源使用。': 'Complete this experiment to see packet delivery, latency, feedback cost, and resource usage.',
    '差值 / Mbps': 'Delta / Mbps', '完成实验后保存为 A，修改策略运行 B，再进行对比。': 'Save a completed experiment as A, change the policy, run B, then compare.',
    '{0} 条匹配，显示前 {1} 条{2}': '{0} matches, showing the first {1}{2}', '；完整记录已按预算截断': '; trace truncated to the configured budget',
    '设备': 'Device', '状态': 'Status', 'PDSCH 符号': 'PDSCH symbols', '其他 RE / PRB': 'Other RE / PRB',
    '参考符号': 'Reference symbols', '共享反馈 bit / slot': 'Shared feedback bits / slot', '事件采样间隔': 'Trace sampling interval', '事件上限': 'Trace limit', '解码模型': 'Decoder model',
    '尚未保存 · 实验包含独立场景副本': 'Not saved · Experiment includes an independent scene copy', '有未应用更改': 'Unapplied changes', '未保存': 'Unsaved', '待校验并应用': 'Pending validation and application',
    '正在运行': 'Running', '实验失败': 'Experiment failed', '实验已完成': 'Experiment completed', '已取消': 'Cancelled', '本机工作台就绪': 'Local workbench ready',
    '流程校验通过：端口、设备归属、参数与时序一致。': 'Graph valid: ports, device ownership, parameters, and timing are consistent.',
    '放弃尚未应用的流程修改？': 'Discard unapplied graph changes?', '放弃当前草稿并读取最新配置？': 'Discard the current draft and reload the latest configuration?',
    '已读取最新配置。': 'Latest configuration loaded.', '当前结果已保存为基准 A。': 'Current results saved as baseline A.',
    '此工作区需要从 RAN Workbench 桌面程序打开。': 'Open this workspace from the RAN Workbench desktop application.',
    '发射功率与天线': 'Transmit power and antenna', '业务缓存': 'Traffic queue', '已到达 CSI': 'Received CSI', '资源调度': 'Resource scheduling',
    'MCS 与发送计划': 'MCS and transmission plan', 'HARQ 进程': 'HARQ processes', '城市场景信道': 'City scene channel', '下行业务源': 'Downlink traffic source',
    'CSI 参考测量': 'CSI reference measurement', '信道估计误差': 'Channel estimation error', 'CSI 报告策略': 'CSI reporting policy', '数据接收与交付': 'Data reception and delivery',
    '反馈承载与延迟': 'Feedback transport and delay', '终端运动': 'UE motion', '指标与事件': 'Metrics and events',
    '传播': 'Propagation', '应用': 'Application', 'PHY / 应用': 'PHY / Application', 'UCI 抽象': 'UCI abstraction', '场景': 'Scene', '观察': 'Observation',
    '系统级策略': 'System-level policy', '总发射功率上限': 'Total transmit power limit', '发射端口': 'Transmit ports', '阵元间距': 'Element spacing', '波长': 'wavelengths',
    '缓存上限': 'Queue limit', '最大 CSI 年龄': 'Maximum CSI age', '调度算法': 'Scheduling algorithm', 'PF 平滑窗口': 'PF averaging window',
    '链路自适应': 'Link adaptation', '固定 / 回退 MCS': 'Fixed / Fallback MCS', '最大 MCS': 'Maximum MCS', 'MCS 保守回退': 'MCS backoff',
    '最大空间层数': 'Maximum spatial layers', '预编码策略': 'Precoding policy', '固定 RI / 无反馈回退': 'Fixed RI / Fallback without feedback', '固定 PMI / 无反馈回退': 'Fixed PMI / Fallback without feedback',
    '38.214 §5.1.3；两端口 Type-I table 5.2.2.2.1-1': '38.214 §5.1.3; two-port Type-I table 5.2.2.2.1-1',
    '并行进程数': 'Parallel processes', 'ACK 传输延迟': 'ACK transport delay', '最多传输次数': 'Maximum transmission attempts', 'ACK 超时': 'ACK timeout',
    '38.321 §5.3.2：系统级 HARQ 状态': '38.321 §5.3.2: system-level HARQ state', '移动快照间隔': 'Motion snapshot interval',
    'Sionna RT / CUDA；非 TR 38.901 随机信道': 'Sionna RT / CUDA; not a TR 38.901 stochastic channel',
    '业务模式': 'Traffic mode', '到达速率': 'Arrival rate', '包大小': 'Packet size', '测量周期': 'Measurement period', '时隙偏移': 'Slot offset',
    '38.214 §5.2：参考资源抽象': '38.214 §5.2: reference-resource abstraction', '系数幅度误差标准差': 'Coefficient magnitude error standard deviation', '系数相位误差标准差': 'Coefficient phase error standard deviation', '度': 'degrees',
    '误差模型假设；不模拟逐样本 CSI-RS 接收': 'Assumed error model; no sample-level CSI-RS reception simulation',
    '报告粒度': 'Reporting granularity', '子带数量': 'Subband count', '触发方式': 'Trigger', '事件触发 CQI 差': 'Event-trigger CQI difference', '参考目标 TBLER': 'Reference target TBLER',
    'UE 最大报告 RI': 'Maximum reported UE RI', '两端口码本子集掩码': 'Two-port codebook subset mask',
    '38.214 CQI table 1 / 两端口 Type-I；子带为研究扩展': '38.214 CQI table 1 / two-port Type-I; subbands are a research extension',
    '接收噪声系数': 'Receiver noise figure', '接收端口': 'Receive ports', '接收阵元间距': 'Receive element spacing',
    '线性 MMSE 接收；NR LDPC/AWGN 参考曲线抽象': 'Linear MMSE receiver; NR LDPC/AWGN reference-curve abstraction',
    '服务基站': 'Serving BS', 'CSI 传输延迟': 'CSI transport delay', 'CSI 丢失概率': 'CSI loss probability', '最多排队等待': 'Maximum queue wait', '研究承载头部': 'Research transport header',
    'FDD 逻辑 UCI 预算；不模拟 PUCCH 波形': 'FDD logical UCI budget; no PUCCH waveform simulation', '东向速度': 'Eastward velocity', '北向速度': 'Northward velocity', '上向速度': 'Upward velocity', '轨迹模型': 'Trajectory model',
    '节点不存在': 'Node does not exist', '连线需要两个不同的节点': 'A connection requires two different nodes', '端口类型不兼容': 'Incompatible port types', '反馈节点不存在': 'Feedback node does not exist', '目标 BS 功能不完整': 'Target BS functions are incomplete',
    '参数名称不完整或未知': 'Parameter names are incomplete or unknown', '需要有限数值': 'A finite number is required', '超出范围': 'Out of range', '需要非空文字': 'Nonempty text is required', '不支持的策略': 'Unsupported policy',
    '类型与实体不兼容': 'Type is incompatible with the entity', '同一实体功能重复': 'Duplicate function for this entity', '位置无效': 'Invalid position', '服务 BS 不存在': 'Serving BS does not exist', '输入端口重复连接': 'Input port is connected more than once',
    'graph 需要 nodes / edges': 'graph requires nodes / edges', '策略图超过节点 / 连线预算': 'Strategy graph exceeds the node / connection budget', '节点 ID 无效或重复': 'Invalid or duplicate node ID', '节点字段缺失或未知': 'Missing or unknown node fields',
    '测量偏移必须小于周期': 'Measurement offset must be shorter than the period', '满缓存包大小超过服务 BS 缓存上限': 'Full-buffer packet size exceeds the serving BS queue limit',
    '固定 / 回退 RI 超过服务链路端口或空间层数': 'Fixed / Fallback RI exceeds the serving link ports or spatial layers', '固定 / 回退 PMI 不属于所选 RI 的码本': 'Fixed / Fallback PMI is outside the selected RI codebook',
    'UE 码本子集没有合法的 RI/PMI 候选': 'UE codebook subset has no valid RI/PMI candidate', '连线需要明确的源 / 目标节点和端口': 'Connections require explicit source / target nodes and ports', '连线引用无效节点或自身环': 'Connection refers to an invalid node or a self-loop',
    '内核不支持此信息通路': 'The kernel does not support this information path', '禁止跨 BS / 非服务 UE 的私有状态连线': 'Private-state connections across BSs / nonserved UEs are prohibited', '重复连线': 'Duplicate connection',
    '实验正在运行；请先取消或等待完成': 'An experiment is running; cancel it or wait for completion', '实验结果指纹与当前配置不一致，已丢弃': 'Result fingerprint differs from the current configuration; results discarded',
    '需要明确文件 path': 'An explicit file path is required', '草稿的原修订已过期；请保留草稿内容或放弃草稿并读取最新配置': 'The draft base revision is outdated; preserve its contents or discard it and reload the latest configuration',
    '未知策略操作': 'Unknown strategy action', 'data 必须是对象': 'data must be an object', '实验修订已变化，请重新读取后再编辑': 'Experiment revision changed; reload before editing', '旧草稿不能覆盖外部更新；请读取最新配置': 'An outdated draft cannot overwrite external changes; reload the latest configuration',
    '草稿需要原始 base_revision': 'Draft requires the original base_revision', '草稿需要实验对象': 'Draft requires an experiment object', '实验有未保存更改 / 草稿；先保存或显式放弃': 'Experiment has unsaved changes / draft; save or explicitly discard them first',
    '请先完成实验': 'Complete an experiment first', '请先应用或放弃未应用草稿，再运行实验': 'Apply or discard the draft before running an experiment', '先完成基准实验 A': 'Complete baseline experiment A first', '需要基准 A 和当前已完成的实验 B': 'Baseline A and a completed current experiment B are required',
    'A/B 需要相同场景、随机种子、时间与物理资源 / 解码配置': 'A/B requires identical scenes, random seeds, duration, and physical resource / decoder settings', 'A/B 需要相同轨迹和信道快照配置': 'A/B requires identical trajectories and channel snapshot settings',
    '相同外部输入下比较；不是统计显著性或实测性能结论': 'Comparison uses identical external inputs; it does not establish statistical significance or measured performance',
    '没有有效路径；不能将未解析链路当作零信道': 'No valid paths; an unresolved link cannot be treated as a zero channel', 'CIR 含无效系数 / 时延': 'CIR contains invalid coefficients / delays', '没有有效路径；请检查射线预算或场景': 'No valid paths; check the ray budget or scene',
    '准备信道': 'Preparing channel', 'NR 时隙仿真': 'NR slot simulation', '实验完成': 'Experiment completed', 'CUDA 信道 · {0} → {1}': 'CUDA channel · {0} → {1}',
    '节点 {0}: {1}': 'Node {0}: {1}', '节点 {0}.{1}: {2}': 'Node {0}.{1}: {2}', '连线 {0} → {1}: {2}': 'Connection {0} → {1}: {2}', '缺少输入 {0}': 'Missing input {0}', '实体 {0}: 缺少 {1} 节点': 'Entity {0}: Missing {1} node',
    'HARQ 超时': 'HARQ timeout', 'CSI 已生成': 'CSI generated', '反馈已发送': 'Feedback sent', '到达时已过期': 'Stale on arrival', '排队等待过期': 'Queue wait expired', '反馈传输丢失': 'Feedback transport loss',
    '缺少发送信道连接': 'Missing connection to the transmit channel', '缺少到 {0}.{1} 的连接': 'Missing connection to {0}.{1}', '存在无延迟的反馈环': 'Feedback loop without delay',
    'CSI report 必须连接本 UE 的测量 / 估计': 'CSI report must connect to this UE’s measurement / estimation', 'UE {0}: 单条报告超过共享反馈预算': 'UE {0}: A single report exceeds the shared feedback budget',
    '实验需要至少一个 BS 和一个 UE': 'Experiment requires at least one BS and one UE', '需要 nr-experiment schema_version=2（支持迁移早期 v1）': 'nr-experiment schema_version=2 is required (migration from v1 is supported)',
    '实验顶层字段缺失或未知': 'Missing or unknown top-level experiment fields', '实验名称不能为空': 'Experiment name cannot be empty', '此实现锁定 TS 38.214 V18.4.0 / Release 18': 'This implementation uses TS 38.214 V18.4.0 / Release 18',
    '实验需要 BS 和 UE': 'Experiment requires BSs and UEs', '实验 settings 字段不完整或未知': 'Experiment settings fields are incomplete or unknown', 'slot RE 配置冲突': 'Conflicting slot RE configuration', 'PRB 占用带宽超过场景带宽': 'PRB bandwidth exceeds the scene bandwidth',
    '未知解码模型': 'Unknown decoder model', '未知信道后端': 'Unknown channel backend', '实验工作量超过 500000 UE-slot 预算；减少时长 / UE 数': 'Experiment exceeds the 500000 UE-slot budget; reduce duration / UE count',
    '业务包处理超过 500000 包预算；减少速率 / 时长或增大包': 'Traffic processing exceeds the 500000-packet budget; reduce rate / duration or increase packet size', 'RT 链路求解超过 4096 次预算；增加快照间隔或减少时长 / 设备': 'RT link solves exceed the 4096-solve budget; increase snapshot interval or reduce duration / device count',
    '必要信道未解析': 'Required channel is unresolved', '{0} → {1}: 必要信道未解析': '{0} → {1}: Required channel is unresolved',
    '系统信道需要有限 H{0}，收到 {1}': 'System channel requires finite H{0}; received {1}', '{0} 需要布尔值': '{0} requires a boolean value',
    '节点 {0} must be an integer in [{1}, {2}]': 'Node {0} must be an integer in [{1}, {2}]',
  };
  I?.register(entries);
  // Parse only known backend message structures, never replace fragments inside
  // user names or other experiment content.
  function message(value) {
    if (!I || I.language !== 'en' || typeof value !== 'string') return value;
    if (entries[value]) return I.t(value);
    let m;
    if ((m = value.match(/^节点 (.+): (.+)$/))) return I.t('节点 {0}: {1}', m[1], message(m[2]));
    if ((m = value.match(/^节点 (.+)\.(\w+): (.+)$/))) return I.t('节点 {0}.{1}: {2}', m[1], m[2], message(m[3]));
    if ((m = value.match(/^连线 (.+) → (.+): (.+)$/))) return I.t('连线 {0} → {1}: {2}', m[1], m[2], message(m[3]));
    if ((m = value.match(/^缺少输入 (.+)$/))) return I.t('缺少输入 {0}', m[1]);
    if ((m = value.match(/^缺少到 (.+)\.(\w+) 的连接$/))) return I.t('缺少到 {0}.{1} 的连接', m[1], m[2]);
    if ((m = value.match(/^实体 (.+): 缺少 (.+) 节点$/))) return I.t('实体 {0}: 缺少 {1} 节点', m[1], m[2]);
    if ((m = value.match(/^UE (.+): 单条报告超过共享反馈预算$/))) return I.t('UE {0}: 单条报告超过共享反馈预算', m[1]);
    if ((m = value.match(/^CUDA 信道 · (.+) → (.+)$/))) return I.t('CUDA 信道 · {0} → {1}', m[1], m[2]);
    if ((m = value.match(/^(.+) → (.+): 必要信道未解析$/))) return I.t('{0} → {1}: 必要信道未解析', m[1], m[2]);
    if ((m = value.match(/^系统信道需要有限 H(.+)，收到 (.+)$/))) return I.t('系统信道需要有限 H{0}，收到 {1}', m[1], m[2]);
    if ((m = value.match(/^(\w+) 需要布尔值$/))) return I.t('{0} 需要布尔值', m[1]);
    if ((m = value.match(/^节点 (.+) must be an integer in \[([^,]+), (.+)\]$/))) return I.t('节点 {0} must be an integer in [{1}, {2}]', m[1], m[2], m[3]);
    return I.t(value);
  }
  window.RANStrategyI18n = {message};
})();
