# 阶段 03 agent 与流程图的逐框对照

结论：主流程能一一对应，七个 stage 覆盖了图里 03 区块的主干。图里有四处当前没有实现：MD（图里本身标为可选）、P2Rank/fpocket 位点预测、ChimeraX 的检测/测量/可视化、AF3 与对接的“按需互补”。另有一处只做了接口：可获得性/可合成性/溶解性/选择性四项，agent 只接收评分，不自动计算。

工作流图见 [STAGE3_FLOW.mermaid](STAGE3_FLOW.mermaid)。

## 主流程

| 图中的框 | agent 中的对应 | 代码 / 产物 | 状态 |
|---|---|---|---|
| 03 区块的输入：候选、受体、参考 | `intake` | `intake.py`，`normalized/*.csv` | 已实现（图里没有画，但是必要的守门步） |
| 方法适用性与参考对照检查 | `applicability` | `applicability.py`，`reference_check.csv` | 已实现 |
| 有适用参考时检验流程表现 | 红对接 RMSD、已知药物次序、突变趋势、刚柔方法一致性、协议一致性 | 同上，5 类检查 | 已实现 |
| 无参考时记录校验缺口与不确定性 | `status=gap` + `U-GAP-*` / `U-REF-*` | `applicability.py`、`uncertainties.csv` | 已实现 |
| 结合姿势 / 复合物建模 | `poses` | `engines/replay.py`、`engines/vina.py`、`engines/af3.py` | 已实现 |
| 分子对接 | `vina` 适配器 | `engines/vina.py`，写 `poses/*.pdbqt` | 已实现；当前环境没装 Vina，未真实跑，只做了注入假模块的单元测试 |
| AlphaFold 3 | `af3` 适配器 | `engines/af3.py` | 已实现（导入清单与结构，只取置信度） |
| 按需要互补 | 单次运行选一个引擎；`replay` 可以混合导入对接和 AF3 行 | `config.engine.name` | 部分实现 |
| 输出姿势、评分/置信度与结构文件 | `pose_metrics.csv` + `poses/` | `qc.py`、`pipeline.py` | 已实现；结构文件只保留 Vina 的 PDBQT 和 AF3 原路径，没有统一导出 SDF/PDB |
| 综合评估：结构 / 几何 | `qc` 的姿势分、位点重叠、催化接触、冲突、极性接触 | `qc.py`，`pose_metrics.csv` | 已实现 |
| 综合评估：位点匹配 | 与参考配体的重叠率、质心距离 | `qc.py`、`vina.py` | 已实现，但需要有参考配体文件 |
| 综合评估：方法一致性 | 刚柔 delta 偏移 + 姿势间重叠标准差 | `rank.py` 的 `consistency_score` | 已实现 |
| 综合评估：可获得性、可合成性、溶解性、选择性 | 输入四列 0–1，缺省按 0.5 中性并记 `U-DEV-*` | `rank.py` 的 `developability_score` | 部分实现（不自动计算） |
| 按项目目标补充其他性质 | 权重、窗口、阈值全部可配 | `config.py`、`stage3_configs/*.json` | 已实现（接口层面） |
| 可选：分子动力学模拟 | 没有对应 stage | — | 未实现（图里标为可选） |
| 达到预设计算筛选标准？ | `decide` 的硬门控与软标记 | `decide.py`，`decisions.csv` | 已实现 |
| 是 → 计算阶段输出 | `export` | `pipeline.py`，全套产物 | 已实现 |
| 否 → 删除或重新设计候选（返回阶段 02，保留失败原因） | `decision=revise`、`next_stage=02`、`hard_failures` | `decide.py` | 已实现；不物理删除，保留记录以便追溯 |
| 计算阶段输出：优先实验测试的候选清单 | `candidates_ranked.csv` + `decisions.csv` | `export` | 已实现 |
| 输出：预测依据、候选结构、未解决的不确定性 | `stage3_report.md`、`uncertainties.csv`、`manifest.json`、`poses/` | `report.py`、`pipeline.py` | 已实现 |

## 左侧边栏

| 图中的说明 | agent 中的对应 | 状态 |
|---|---|---|
| ChimeraX：检测、测量与可视化 | 没有调用 ChimeraX；只输出结构文件和 CSV，供人工打开 | 未实现（外部工具） |
| P2Rank / fpocket：候选位点预测 | 没有实现；对接盒由 `receptors.csv` 给定 | 未实现 |
| 分子对接：评估已有候选姿势 | 对接负责产生姿势，评估在 `qc` | 已实现 |
| 候选产生：文献、库或设计模块 | 属于阶段 02，agent 只接 `candidates.csv` | 边界外 |
| 口袋预测本身不确认抑制机制 | 报告第 9 节固定写明；`manifest.interpretation_limits` 也记录 | 已实现（文字约束） |
| AF3 输出静态复合物，多个样本不是轨迹 | 同上；AF3 行强制 `affinity` 为空、决策 `needs_review` | 已实现 |
| 对接评分不等同实测亲和力 | 报告、决策理由、`interpretation_limits` 三处都写 | 已实现 |
| 氢键与盐桥是模型中的几何证据 | `polar_contacts` 只按距离计数，报告声明未做角度/质子化校验 | 已实现 |
| 结合姿势合理不等于功能受抑制 | 决策理由与报告固定声明 | 已实现 |
| 旋转展示视频属于可视化 | 没有输出视频 | 边界外 |

## 图里没有、agent 多出来的

`intake` 和 `export` 两个 stage 图里没有单独画，但它们是“稳定输入输出”的必要环节。另外 agent 多了一个 `needs_review` 分支：图里的判据只有“是/否”，但参考对照失败不是候选的错，直接判 `revise` 会让阶段 02 去改一个本来没问题的分子。所以参考对照失败时停在这里，等人工补对照或换方法。

`revise` 也不等于“删除”：候选行保留，失败原因写在 `hard_failures`，阶段 02 重新设计时可以直接引用。这比物理删除更符合图里“保留失败原因”的要求。

## 缺口怎么补

MD 是唯一一个图里明确标为“可选”但 agent 没做的。补法是在 `qc` 之后加一个可选 stage：读 GROMACS/OpenMM 的 RMSD、RMSF、氢键占有率汇总，只作为 `advance_with_flags` 的补充证据，不进入综合分，也不替代实验。P2Rank/fpocket 更适合放在阶段 02 或 `intake` 之前，输出候选口袋，但对接盒仍要人来确认。ChimeraX 不需要 Python 调用，导出一个 `.cxc` 会话脚本，把受体和排名前几的姿势一起打开即可。

## 怎么核对

```bash
python stage3.py run --config stage3_configs/a1_replay.json
```

跑完后按图从上往下对：

- 第 1 框：`outputs/reference_check.csv`，A1 里 6 条检查，3 通过 3 失败，`reference_status=warn`。
- 第 2 框：`outputs/pose_metrics.csv`，A1 里 54 个姿势，36 个被选中。
- 第 3 框：`outputs/candidates_ranked.csv`，两个候选的五个分量和综合分。
- 判据分支：`outputs/decisions.csv`，`decision` 与 `next_stage` 两列就是图里的“是/否”走向。
- 输出：`outputs/stage3_report.md` 第 1–9 节，对应图底部的“候选清单、预测依据、结构、不确定性”。
