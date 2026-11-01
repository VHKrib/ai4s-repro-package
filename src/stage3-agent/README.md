# 阶段 03 计算建模 Agent（v0.1）

把「蛋白靶标小分子抑制剂」流程图的第三段做成受控管线：输入契约 → 方法适用性与参考对照 → 结合姿势/复合物 → 几何 QC → 综合排序 → 预设判据 → 固定格式输出。附带 A1（HIV-1 蛋白酶）回放案例，解压即可运行，不需要联网、不需要安装对接软件。

## 快速开始

需要 Python 3.10+。核心管线只用标准库。

```bash
# Windows
python stage3.py run --config stage3_configs\a1_replay.json

# Linux / macOS
python stage3.py run --config stage3_configs/a1_replay.json

# 另开一个终端看进度
python stage3.py status --latest --follow

# 复核产物列、行数、哈希与 JSON Schema
python stage3.py validate --latest --config stage3_configs\a1_replay.json
```

跑完在 `stage3_runs/a1_replay-*/outputs/` 下得到六张 CSV、三个人读文件和一个 `manifest.json`。A1 回放约 2 秒。

## A1 回放预期结果

- 4 个条目（2 候选 + 2 对照）、9 个受体、54 个姿势。
- 参考对照 6 条：红对接 RMSD 0.88 Å 通过；已知药物次序失败；saquinavir 突变趋势 +0.63 通过；darunavir 突变趋势 −0.01 失败；刚柔方法一致性 r=0.13 失败；协议一致性通过。综合状态 `warn`。
- 排序：3h 0.759（A）、34b 0.725（A），均为 `advance_with_flags`，带 `ranking_unvalidated`。
- 对照不参与排名：darunavir 0.791、saquinavir 0.744。

这些数字用于核对安装是否正常，不是新的实验结论。

## 目录

```
stage3-agent/
├── stage3.py                  # CLI：run / status / list / validate / schemas
├── stage3_agent/              # 七步管线、schema、状态机、引擎适配器
├── stage3_configs/            # a1_replay.json
├── stage3_cases/a1_replay/    # 规范化输入 + build_inputs.py
├── struct_case/               # 回放所需受体、配体、结果表（不是全部对接姿势）
├── a1_case/A1_compounds.csv   # 重建输入时用于取 SMILES
├── tests/                     # 20 个阶段 03 测试
├── STAGE3.md                  # 设计与契约说明
├── STAGE3_MAPPING.md          # 与流程图的逐框对照
└── STAGE3_FLOW.mermaid        # 工作流源图
```

## 依赖

`requirements-stage3.txt` 里只有可选项。回放不需要任何第三方包。

- `vina`：真实对接引擎。没有它时 `engine.name=vina` 会明确报错，不会静默跳过。
- `rdkit`：当前适配器没有强制依赖；结构几何用内置 PDB/PDBQT/SDF 解析。
- `jsonschema`：`validate` 时做第二层校验；没装也能做列契约和哈希校验。

## 切到真实对接

把配置里的 `engine` 改成：

```json
"engine": { "name": "vina", "options": { "exhaustiveness": 16, "n_poses": 9, "seed": 42 } }
```

并在 `receptors.csv` / `candidates.csv` 里确认 `pdbqt_path`、`ligand_pdbqt_path` 和六个对接盒参数。V82A 缺少 `_flex_flex.pdbqt`，该受体的 `flex_pdbqt_path` 目前留空，切到 vina 前要补这份制备。

## 可复现性

相同输入重跑，六张 CSV 字节一致。`manifest.json` 记录输入/输出 SHA256、参数、软件版本、随机种子；`run_state.json` + `events.jsonl` 记录每一步的状态与耗时；`run --resume` 按输入哈希跳过未变步骤。

## 解释边界

对接分不是实测亲和力；位点重叠和催化接触只是几何证据；氢键/盐桥只按距离计数，未做角度与质子化校验；AF3 是静态复合物，多个样本不是轨迹；MD 只补动态证据，不替代实验。这些同时写在报告第 9 节和 `manifest.interpretation_limits`。

## 测试

```bash
python -m unittest discover -s tests -v
```

20 个测试，覆盖列契约、浮点/枚举/必填校验、JSON Schema、确定性、参考对照三种状态、AF3 不能推进、Vina 适配器（注入假 `vina` 模块）、输入违约、续跑与 status 渲染。

## 来源

从 `D:\AI4S\pubmed-inhibitor-agent` 抽出，版本 `stage3/0.1.0`，打包日期 2026-10-29。不含文献检索/证据抽取层、PubMed 缓存和完整对接姿势库。
