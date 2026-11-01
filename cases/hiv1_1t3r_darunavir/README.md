# hiv1_1t3r_darunavir — 最小可复现案例

HIV-1 蛋白酶野生型二聚体（PDB **1T3R**，1.20 Å）× Darunavir / TMC114（共晶配体 `017`）
的**共晶自对接（redocking）**方法自检。配体就是晶体里的配体本身，所以"分数"和
"RMSD"都有一个可验证的正确答案。

## 期望结果

| 项 | 期望值 |
|---|---|
| 姿势数 | 9 |
| 最佳对接分 | −9.958 kcal/mol |
| 9 个姿势（pose 1→9） | −9.958 / −9.148 / −8.877 / −8.782 / −8.366 / −8.233 / −7.946 / −7.880 / −7.810 |
| 重对接 RMSD（pose 1） | 0.211 Å（阈值 2.0 Å） |
| 盒子 center / size | (19.459, 29.766, 14.159) / (20.361, 20.806, 18.122) Å |
| 受体 PDBQT 原子 | 1 811（315 极性氢） |
| 决策 | advance |

## 目录内容

```
case.json                    案例定义：输入哈希、引擎参数、盒子、stage-3 判据、期望数字、容差
inputs/1T3R.pdb              晶体结构（RCSB 原文件，未改）
inputs/pubchem_cid213039_3d.sdf  PubChem 3D 构象，仅作键级模板
scripts/prep_inputs.py       1T3R → 蛋白(链A+B) + 配体017 + 盒子      [science 环境]
scripts/prep_receptor.py     meeko mk_prepare_receptor → 受体 PDBQT    [science 环境]
scripts/prep_ligand.py       晶体坐标 + 模板键级 → cocrystal_pose.sdf / darunavir.pdbqt  [science 环境]
scripts/dock_vina.py         真实 Vina 对接（经 scripts/shim/vina.py）  [science 环境]
scripts/build_stage3_case.py 生成 stage-3 的 candidates/receptors/reference_poses + config [任意 Python 3.11]
evidence/reference_run/      2026-10-30 原始运行的 stage-3 运行目录（validate 用）
evidence/PROVENANCE.md       这份 evidence 是怎么来的、改过什么
```

## 怎么跑

```bash
# 只验证（不重跑对接，约 2 秒）
python tests/smoke_test.py

# 完整重算（制备 + 对接 + 重对接 + stage-3 + validate，约 2 分钟）
python scripts/run_pipeline.py --case cases/hiv1_1t3r_darunavir
```

`run_pipeline.py` 会把本次运行的 `result.json` / `summary.md` / 日志写到
`runs/<case_id>-<时间戳>/`，`verdict` 只有两种：`OK`（12 项比对全中）或 `MISMATCH`。

## 为什么这些数字可以不一致，但结果仍然算对

`case.json → tolerances` 给每种量规定了容差：对接分 ±0.001、RMSD ±0.02 Å、盒子 ±0.002 Å。
超出容差就是 `MISMATCH`，不会被解释成"差不多"。RMSD 允许 ±0.02 是因为不同 RDKit
版本的对称性匹配顺序可能带来末位差异；分数与盒子几乎是精确的（同一二进制、同一 seed）。

## 换案例

复制本目录 → 改 `case.json`（输入、盒子、stage-3 判据、期望值）→ 替换 `scripts/`
里三步制备脚本里与化学相关的部分（残基名、模板、公式检查）。`run_pipeline.py`
不认任何具体靶点，它只读 `case.json`。
