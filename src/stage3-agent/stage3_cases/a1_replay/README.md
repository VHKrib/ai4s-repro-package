# A1 回放案例（阶段 03）

这些 CSV 是结构化输入，不是原始结果。原始数据在 `struct_case/results/`，由一个转换脚本变成固定列名的 tidy 表，阶段 03 只读这些表。

重新生成：

```bash
python stage3_cases/a1_replay/build_inputs.py
python stage3.py run --config stage3_configs/a1_replay.json
python stage3.py status --latest
```

## 列的含义

- `candidates.csv`：4 个条目。`saquinavir`、`darunavir` 是已上市药，角色为 `control`；`3h`、`34b` 是 A1 候选。成药性四项留空，agent 会按中性 0.5 处理并在不确定性表里标出。
- `receptors.csv`：WT（1HXB）+ 8 个单点突变。`flex_pdbqt_path` 指向放开突变侧链的受体，`pdbqt_path` 是刚性受体。两者都保留，便于切到 `vina` 引擎。
- `poses.csv`：primary 协议是 `vina_flex_sidechain`，来自 `panel_{ligand}_flex.csv` 的最佳姿势；`vina_rigid_wt_9poses` 是敏感性协议，只用于审计，不参与排序。
- `panel.csv`：来自 `panel_comparison.csv`，刚性/柔性 delta、姿势是否改变、已知耐药标注。
- `reference_poses.csv`：saquinavir 在 WT 上的 8 个重对接姿势及其 RMSD。

## 已知的坑

- `poses.csv` 没有 `clash_pairs` 和 `polar_contacts`：原始结果没有保存逐姿势受体坐标。agent 会在不确定性表里记录“pose_score 未含冲突惩罚”。
- 34b 的单位矛盾（摘要 0.32 nM vs ChEMBL 320 nM）不影响计算排序，但必须保留在候选备注里。
- 这里的 `known_resistance` 是 `compare_panels.py` 里的手工标注，不是 agent 的判断。
- V82A 只有 `_flex_rigid.pdbqt`，没有 `_flex_flex.pdbqt`，所以该受体的 `flex_pdbqt_path` 留空；回放不受影响，切到 `vina` 前要补这份制备。
