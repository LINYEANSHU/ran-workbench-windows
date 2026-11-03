# README diagrams / README 图源

The architecture and data-flow diagrams describe published RAN Workbench code at [e505d3b](https://github.com/LINYEANSHU/ran-workbench-windows/tree/e505d3bc3ccf80331d1c14c566cc1a5c50bf7193). Every Archify node includes repository-relative source ranges pinned to that revision. The English and Chinese versions share the same graph.

架构图与数据流图依据上述公开版本。每个 Archify 节点都包含固定版本的源码行号；中英文图保持相同节点和连接。

| Diagram | English | 简体中文 |
| --- | --- | --- |
| Architecture / 架构 | [SVG](../assets/readme/architecture-en.svg) · [Archify source](../assets/readme/architecture-en.archify.json) | [SVG](../assets/readme/architecture-zh.svg) · [Archify 图源](../assets/readme/architecture-zh.archify.json) |
| Data flow / 数据流 | [SVG](../assets/readme/dataflow-en.svg) · [Archify source](../assets/readme/dataflow-en.archify.json) | [SVG](../assets/readme/dataflow-zh.svg) · [Archify 图源](../assets/readme/dataflow-zh.archify.json) |

## Read the boundaries / 关键边界

- Workbench and Strategy each own a `JobRunner` instance. The architecture groups this shared implementation into one node; it does not imply a global queue or one shared runner.
- Strategy uses a scene copy. Refresh creates a new experiment after busy-state and unsaved-change checks; it is not continuous two-way synchronization.
- The NR simulator consumes the experiment's scene, graph and settings. It produces its own channel snapshots; Workbench coverage results are not the NR channel input.
- CUDA is required for propagation and the RT channel. The synthetic channel is explicitly selected and is not an automatic fallback after a CUDA failure.
- Modeling reads user-provided meshes for viewing and screenshots only.
- Job requests and results cross process boundaries as local JSON files; progress uses stdout JSON lines. Scene and experiment fingerprints guard accepted results.

Workbench 与 Strategy 的 runner 为独立实例；图中合并展示共同实现。Strategy 使用场景副本，自行计算信道，不消费覆盖结果。合成信道必须显式选择，CUDA 失败不会自动降级。Modeling 不参与传播计算。

## Regenerate / 重新生成

The checked Archify topology is the content source. A small dependency-free renderer gives the README exports larger type, fixed colors and static SVG primitives suitable for GitHub. It preserves node and edge IDs, directions, and detailed edge labels in SVG titles. The surrounding README also explains the paths in text for narrow screens.

```powershell
python tools/render_readme_diagrams.py
python tools/render_readme_diagrams.py --check
```

When changing the graph, update both language sources and the renderer's layout together. Recheck the cited code at the new pinned revision. With Archify installed, point `ARCHIFY_CLI` at its `bin/archify.mjs` and validate each candidate as an interactive HTML diagram:

```powershell
node "$env:ARCHIFY_CLI" finalize architecture assets/readme/architecture-en.archify.json docs/evidence/architecture-en.html --repo-root . --quality showcase --json
node "$env:ARCHIFY_CLI" finalize dataflow assets/readme/dataflow-en.archify.json docs/evidence/dataflow-en.html --repo-root . --quality showcase --json
```

Repeat for the `-zh` sources. HTML viewers and machine-specific verification receipts stay in the ignored evidence directory; the repository publishes only the lightweight SVGs, portable JSON sources and renderer. CI checks that SVGs match their sources. Inspect all four assets at 900 px and 360 px after visual changes.
