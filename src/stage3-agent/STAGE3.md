# 阶段 03 Agent：计算建模、评估与排序

这层做的是流程图 03 那一段：方法适用性与参考对照 → 结合姿势/复合物 → 综合评估与排序（可选 MD）→ 判据 → 计算阶段输出。它不替代阶段 02 的候选产生，也不替代阶段 04 的实验验证。

设计上的三个硬约束：

1. **步骤固定**：七个 stage，顺序不变，每步只读上一步写出的规范化表。
2. **格式固定**：列名、列序、类型、枚举写在 `stage3_agent/schema.py`，写出前逐行校验，写出后再读回来校验一遍。
3. **进度可见**：`run_state.json` 原子写，`events.jsonl` 追加写，另开一个终端 `status --follow` 即可看到跑到哪一步、哪个候选、预计还要多久。

## 快速开始

```bash
# 回放 A1 已有的对接结果（不重新算）
python stage3_cases/a1_replay/build_inputs.py
python stage3.py run --config stage3_configs/a1_replay.json

# 另开一个终端
python stage3.py status --latest --follow

# 复核产物列、行数、哈希与 JSON Schema
python stage3.py validate --latest --config stage3_configs/a1_replay.json
```

真实对接时把配置里的 `engine.name` 改成 `vina`，填好受体/配体的 PDBQT 路径和对接盒即可。引擎适配器不在时不会静默跳过，会直接报 `engine not available`。

## 七个步骤

| # | stage | 做什么 | 产物 |
|---|---|---|---|
| 1 | `intake` | 校验输入契约、补齐可选列、写规范化副本 | `normalized/*.csv` |
| 2 | `applicability` | 红对接 RMSD、已知药物次序、突变趋势、方法一致性 | `reference_check.csv` |
| 3 | `poses` | 调用引擎产生姿势（replay / vina / af3） | `artifacts/03_poses.json`、`poses/` |
| 4 | `qc` | 逐姿势打分、几何门控、选最佳姿势、构建候选×受体矩阵 | `pose_metrics.csv`、`receptor_matrix.csv` |
| 5 | `rank` | 五分量加权、固定窗口归一化、分层 | `candidates_ranked.csv` |
| 6 | `decide` | 预设判据、失败原因、下一环节 | `decisions.csv` |
| 7 | `export` | 固定格式写出、manifest、自检 | `stage3_report.md`、`manifest.json` |

每步完成时写 `artifacts/NN_<stage>.json`。`--resume` 用「配置哈希 + 输入文件哈希 + stage 名」算指纹，指纹一致且产物存在就跳过该步。改一个输入文件会让相关步骤重跑，没改的步骤保持原样。

## 输入契约

阶段 03 只读 tidy 表，不读各脚本自己的输出。A1 的转换脚本是 `stage3_cases/a1_replay/build_inputs.py`，它也是唯一知道旧列名的地方。

`candidates.csv` 的必填列是 `candidate_id,name,role`。`role=candidate` 参与排序，`control` 只做方法对照，`reference` 不参与决策。可选列包括 SMILES、来源、化学型、重原子数、配体文件路径，以及四项成药性评分 `availability / synthesizability / solubility_risk / selectivity_risk`（0–1）。没填的按中性 0.5 处理，并在不确定性表里留一条 `developability_unassessed`。

`receptors.csv` 的必填列是 `receptor_id`。`mutations` 用 WT 留空、突变写 `G48V` 这种形式；`is_primary` 只能有一个 true。`vina` 引擎额外要求 `pdbqt_path` 和六个对接盒参数。

`poses.csv` 一行一个姿势，必填 `candidate_id,receptor_id,pose_index,protocol,protocol_role,engine`。`protocol_role=primary` 的行才参与排序；`sensitivity` 只用于审计，例如 A1 里 9 个 WT 姿势。这样不会因为混协议把不可比的数值排到一起。

`panel.csv` 一行一个「候选 × 突变受体」，给刚性/柔性 delta、姿势是否改变、已知耐药标注。`reference_poses.csv` 给参考配体的重对接 RMSD。

## 输出契约

所有 CSV 以 UTF-8 with BOM、LF 换行写出，列序固定，浮点统一三位小数。同一个输入重跑两次，六张表的字节完全一致；运行时间只写进 `manifest.json` 和 `run_state.json`，不进表。

| 文件 | 内容 |
|---|---|
| `outputs/candidates_ranked.csv` | 排名、分层、五个分量、综合分、决策编码；对照行保留但不排名 |
| `outputs/decisions.csv` | 决策、硬失败、软标记、下一环节、是否需人工复核 |
| `outputs/pose_metrics.csv` | 每个姿势的对接分、配体效率、位点重叠、催化接触、冲突、是否选中及原因 |
| `outputs/receptor_matrix.csv` | 候选 × 受体：相对 WT 的 Δ、姿势改变、风险等级 |
| `outputs/reference_check.csv` | 每条参考对照的指标、阈值、通过/失败/缺口 |
| `outputs/uncertainties.csv` | 未解决的不确定性、影响级别、如何解决 |
| `outputs/stage3_report.md` | 人读报告，数字与 CSV 同源 |
| `manifest.json` | 输入与规范化表哈希、参数、软件版本、每个产物的哈希、JSON Schema 路径 |

`validate` 会重读每张表，检查列序、类型、枚举、行数、哈希；装了 `jsonschema` 时再用 `schemas/*.schema.json` 校验一遍。列漂移是硬错误，不会写出一个「看起来跑了」的半成品。

## 判据与决策

判据写在配置的 `criteria` 里：位点重叠、催化接触、冲突数、对接分上限、配体效率下限、突变惩罚上限、参考对照是否必须通过、是否允许仅 AF3 推进。

硬失败会得到 `revise`，`next_stage=02`，失败原因保留在 `hard_failures`，阶段 02 重新设计时可以直接引用。参考对照失败会得到 `needs_review`，因为问题在方法而不在单个候选。只有软标记时是 `advance_with_flags`，带着标记进阶段 04。

参考对照分两种影响，这一点是刻意的：

- `impact=blocks_advance`：姿势本身不可信（例如红对接 RMSD 超阈值），整个排序降级为人工复核。
- `impact=limits_ranking`：姿势可信，但排序能力没被验证（已知药物次序复现不了、突变趋势复现不了、刚性与柔性协议不一致）。候选仍可推进，但必须带 `ranking_unvalidated` 标记，并且只能在同系列内比较。

这个区分对应 A1 的实际结论：红对接可信，刚性/柔性 panel 只能做定性风险 flag，不能做跨分子排序。

## 进度与续跑

`run_state.json` 记录每个 stage 的 `status / progress / started_at / duration_s / fingerprint / artifact`，用临时文件 + `os.replace` 原子替换；Windows 上读取方占用文件时会短暂重试。`events.jsonl` 是一行一个事件的追加日志，用来回看失败点和警告。

`status` 显示总进度条、每个 stage 的进度、耗时、当前 stage 的 ETA、计数器、最近事件、错误和产物列表。`--json` 给机器读，`--follow` 每秒刷新直到 `completed/failed/interrupted`。

`run --resume --run-id <id>` 从已完成的 stage 继续，不会重算已完成的对接。

## 引擎适配器

`replay` 读取既有 tidy 表，用于回归和「集群算完、本地评估」的场景。`vina` 直接调 `vina` python 包，写 PDBQT 姿势，再用 PDB/PDBQT 坐标算冲突、极性接触、位点重叠和 Asp25 接触；单对失败只记不确定性，不终止整轮。`af3` 读 AF3 的清单和结构，只保留 `ranking_score / ipTM / pTM` 作为置信度，`affinity` 永远为空，决策强制 `needs_review`。

三者输出同一套 `pose_metrics` 列，所以 QC 和排序不需要知道姿势来自哪里。

## A1 回放结果（2026-10-22）

`stage3_runs/a1_replay-demo/`，2 个候选、2 个对照、9 个受体、54 个姿势，约 2 秒。

参考对照：红对接 RMSD 0.88 Å 通过；已知药物次序失败（Vina 认为 saquinavir −11.45 强于 darunavir −9.25）；saquinavir 突变趋势通过（已知位点 − 其余 = +0.63 kcal/mol）；darunavir 突变趋势失败（−0.01）；刚性 vs 柔性相关 r = 0.13，失败；协议一致性通过。综合状态是 `warn`，不是 `fail`。

排序：3h 综合分 0.759（A），34b 0.725（A）。两个都 `advance_with_flags`，因为排序参考没过，必须带 `ranking_unvalidated` 进实验；`reviewer_required=true`。

3h 排第一的原因是可解释的：姿势分 0.928 对 0.874，耐药分 0.669 对 0.603，一致性 0.803 对 0.758；两者亲和力几乎相同（−8.90 与 −8.92）。34b 的 I50V 柔性惩罚 0.79、I84V 0.50 仍然保留在矩阵里，没有被平均掉。

十个不确定性里，值得先处理的是三条：`control_order` 失败（跨分子排序不可用）、`U-CLASH-TABLE`（回放表没有冲突计数）、`U-DEV-*`（3h/34b 的成药性四项未评估）。

## 边界

对接分不是实测亲和力；位点重叠和催化接触只是几何证据，不证明功能被抑制；氢键与盐桥只算了距离，没有角度和质子化校验；AF3 是静态复合物，多个采样不是轨迹；MD 只补动态证据，不替代实验。这些限制同时写进报告和 `manifest.interpretation_limits`。

## 测试

```bash
python -m unittest discover -s tests -v
```

新增 20 个测试，覆盖列契约、浮点/枚举/必填校验、JSON Schema、确定性、参考对照三种状态、AF3 不能推进、Vina 适配器（注入假 `vina` 模块）、输入违约、续跑跳过已完成步骤、status 渲染。全部 41 个测试通过。
