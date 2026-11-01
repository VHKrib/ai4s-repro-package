# 阶段 03：计算建模、评估与排序 — hiv1_1t3r_darunavir

靶点：HIV-1 protease WT dimer (1T3R, 1.20 A) ｜ 引擎：vina ｜ 主受体：HIV1_1T3R ｜ 运行：`hiv1_1t3r_darunavir-20261030-225324` ｜ 用时：34.0s

候选 1 个：进入实验 1。排名第一：Darunavir (1T3R co-crystal ligand 017, TMC114)（综合分 0.59）。

## 1. 方法适用性与参考对照

| 检查 | 对象 | 指标 | 数值 | 阈值 | 结果 | 说明 |
|---|---|---|---|---|---|---|
| redock_drv | DRV@HIV1_1T3R | best_rmsd_A | 0.21 | 2.00 | 通过 | 最高分姿势 RMSD=0.21 A (通过); 红对接只验证姿势复现，不验证打分排序 |

参考对照全部通过，排序结果可在本体系内相对比较；仍需实验校准绝对值。

## 2. 候选姿势与复合物

| 候选 | 角色 | 主受体 | 引擎 | 协议 | 对接分 | 配体效率 | 位点重叠 | 催化接触 | 位点重叠标准差 | 姿势选择 |
|---|---|---|---|---|---|---|---|---|---|---|
| Darunavir (1T3R co-crystal ligand 017, TMC114) | candidate | HIV1_1T3R | vina | vina_rigid | -9.96 | 0.26 | 1.00 | 1 | 0.12 | pose 1 · geometry_gate_passed;lowest_affinity |

对接分是打分函数输出，不是实测亲和力；位点重叠与催化接触是几何证据，只能说明姿势落在预期口袋内，不能证明功能被抑制。

## 3. 综合评估与排序

综合分 = 姿势×0.30 + 亲和力×0.20 + 一致性×0.20 + 成药性×0.15 + 耐药×0.15。各分量都是固定窗口下的 0–1 变换，不随本批候选重新归一化。

| 排名 | 候选 | 分层 | 综合分 | 姿势 | 亲和力 | 一致性 | 成药性 | 耐药 | 决策 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Darunavir (1T3R co-crystal ligand 017, TMC114) | B | 0.59 | 0.80 | 0.99 | 0.00 | 0.50 | 0.50 | 进入实验 |

## 4. 突变负担

没有出现需要标记的突变风险。

风险分级只看计算差值：known_resistance 来自输入标注，不是本 agent 的判断；柔性侧链 panel 能提示方向，但不能当耐药排序或 go/no-go 门槛。

## 5. 判据与决策

| 候选 | 决策 | 依据 | 下一环节 | 需人工复核 |
|---|---|---|---|---|
| Darunavir (1T3R co-crystal ligand 017, TMC114) | 进入实验 | 达到预设计算筛选标准，优先进入实验验证 | 04 | 否 |

## 6. 未解决的不确定性

| ID | 范围 | 类型 | 说明 | 影响 | 如何解决 |
|---|---|---|---|---|---|
| U-INPUT-001 | case | input_warning | 未提供突变 panel；一致性与耐药分量按中性值处理 | 提示 | 补全输入字段后重跑 |
| U-INPUT-002 | case | input_warning | ['DRV'] 的成药性四项未填全；按中性 0.5 处理并在不确定性表标记 | 提示 | 补全输入字段后重跑 |
| U-METHOD-DRV | DRV | no_method_comparison | 缺少刚性/柔性协议对照，方法一致性按中性 0.5 处理 | 限制排序解释 | 补同参数的刚性 panel 或统一协议 |
| U-DEV-DRV | DRV | developability_unassessed | 可获得性/可合成性/溶解度/选择性四项都未评估，按中性 0.5 处理 | 限制排序解释 | 补供应商目录、逆合成可行性、logS/PSA 与选择性数据 |
| U-PANEL-DRV | DRV | no_mutation_panel | 没有突变 panel，耐药负担按中性 0.5 处理 | 限制排序解释 | 至少覆盖已知耐药位点的柔性侧链对接 |

## 7. 判据与参数

| 参数 | 值 |
|---|---|
| primary_receptor | HIV1_1T3R |
| min_site_overlap | 0.70 |
| min_catalytic_contacts | 0 |
| max_clashes | 0 |
| max_affinity_kcal_mol | -7.00 |
| min_ligand_efficiency | 0.18 |
| max_flex_delta | 1.00 |
| max_consensus_std | 0.08 |
| require_reference_pass | 否 |
| allow_af3_only_advance | 否 |
| 权重 · 姿势 | 0.30 |
| 权重 · 亲和力 | 0.20 |
| 权重 · 一致性 | 0.20 |
| 权重 · 成药性 | 0.15 |
| 权重 · 耐药 | 0.15 |
| 分层阈值 A / B | 0.70 / 0.55 |

## 8. 输出与可复现性

| 文件 | 路径 |
|---|---|
| 候选排序 | `outputs/candidates_ranked.csv` |
| 姿势指标 | `outputs/pose_metrics.csv` |
| 受体矩阵 | `outputs/receptor_matrix.csv` |
| 参考对照 | `outputs/reference_check.csv` |
| 决策 | `outputs/decisions.csv` |
| 不确定性 | `outputs/uncertainties.csv` |
| 人读报告 | `outputs/stage3_report.md` |
| 运行清单 | `manifest.json` |

每次运行都会写出 manifest.json，记录输入文件哈希、规范化表哈希、参数、软件版本与随机种子；相同输入重跑得到相同的表格字节。

## 9. 解释边界

- AF3 输出是静态复合物，多个预测样本不是运动轨迹；置信度不能替代结合自由能。
- 对接评分不等于实测亲和力，跨体系数值不可直接比较。
- 氢键与盐桥是模型中的几何接近证据，未做角度与质子化校验。
- 结合姿势合理不等于功能受抑制，最终需要实验验证。
- MD 只补充动态证据，不替代实验；本阶段不把 MD 结果当判据。
