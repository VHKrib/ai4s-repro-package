# evidence/reference_run — 这份证据是怎么来的

| 项 | 值 |
|---|---|
| 原始运行 | `t_664138d8_hiv1_darunavir_20261030-224808` |
| 运行日期 | 2026-10-30 |
| stage-3 run id | `hiv1_1t3r_darunavir-20261030-225324` |
| stage-3 状态 | `completed` |
| Vina 耗时 | 33.93 s |
| stage-3 耗时 | 34.35 s |
| validate | `OK  10 outputs validated` |

## 拷贝方式

`evidence/reference_run/` 是上述 stage-3 运行目录的**逐文件拷贝**
（`outputs/`、`schemas/`、`poses/`、`normalized/`、`artifacts/`、`manifest.json`、
`run_state.json`、`events.jsonl`）。

## 唯一的改动（可审计）

为了不把某台机器的绝对路径带进交付包，以下**元数据**文件里的路径字符串被替换成占位符
`<ORIGINAL_ROOT>` / `<HOME>`：

* `manifest.json`
* `run_state.json`
* `artifacts/01_intake.json`
* `normalized/candidates.csv`
* `normalized/receptors.csv`

`manifest.json` 里记录的路径是**相对于 run 目录**的（如 `outputs/pose_metrics.csv`），
所以替换绝对路径不影响校验；被 `stage3 validate` 逐个重算 sha256 的 `outputs/` 与
`schemas/` **没有任何字节改动**。

## 为什么这仍然是"真证据"

`stage3 validate` 会重读每个产物、重算 sha256、比对行数与列契约。它在本包内跑通
（`python tests/smoke_test.py`），说明这 10 个产物与当初写出时**逐字节一致**——
文件是从原始运行拷来的，不是重新生成的，也没有被"修"成能通过检查的样子。

反过来，如果你想验证**新算一遍**的结果，跑 `python scripts/run_pipeline.py`，
它会生成一个全新的 stage-3 run 并当场 validate，不碰这份 reference_run。
