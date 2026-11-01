# 02 模块的 Windows 适配副本（仅用于真实重跑，不是交付物）

交付包原件（`src\handoff_skill\a4s-stage02-delivery-v1.1.0\`、`src\A4S-02-MODULE-001\module02-module001\`）
**未做任何修改**，其 `PACKAGE_SHA256SUMS` / 清单校验仍原样通过。

本目录 `src/module02-portability-win/` 是从 `src/A4S-02-MODULE-001/module02-module001`
复制（排除 `runs/`）后、只改了一处路径分隔符的**沙箱副本**，用于验证「在 Windows 上真实重跑 02」。

## 为什么需要改

`scripts/run_prepare.py` 在最后一步调用 `check_integrity.check()` 做终检。终检把
`job.json` 里 POSIX 风格的相对路径（`outputs/receptor.pdbqt`、`inputs/...`）与
`records/manifest.json` 的条目名逐一比对：

```python
need(row['path'] in listed, 'INPUT_HASH_OR_PATH_MISMATCH', role=role)
```

而 `common.write_manifest()` 生成清单时用的是 `str(p.relative_to(root))`，在 Windows 上
产生**反斜杠**（`outputs\receptor.pdbqt`）。两边永远不相等，于是每次真实重跑都以：

```
FAIL/1  FINAL_INTEGRITY_FAILED
issues: INPUT_HASH_OR_PATH_MISMATCH × 4, OUTPUT_HASH_OR_PATH_MISMATCH × 10, UNLISTED_BINDING
```

收场——注意这是**终检的路径字符串问题**，全部 12 个科学步骤（依赖检查、环境、裁域/OXT、
Meeko 受体、CIF-CCD 配体、Meeko 配体、读回、内容校验、盒计算、完整科学复核）都返回 0，
`records/full_science_check.json` 是 `SCIENCE_PASS`。

## 具体改动（唯一一处，`scripts/common.py`）

```diff
 def write_manifest(root):
     excluded={'records/manifest.json','SHA256SUMS'}
-    paths=sorted(p for p in root.rglob('*') if p.is_file() and str(p.relative_to(root)) not in excluded and '__pycache__' not in p.parts)
+    paths=sorted(p for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix() not in excluded and '__pycache__' not in p.parts)
     record={'schema_version':'a4s.file_manifest.v1','job_id':read_json(root/'job.json')['job_id'],
-            'files':[file_record(root,str(p.relative_to(root))) for p in paths]}
+            'files':[file_record(root,p.relative_to(root).as_posix()) for p in paths]}
```

不涉及科学配方、哈希白名单、模板或断言：`APPROVED_PROFILE_SHA`、输入 SHA-256 白名单、
`-B`（禁用 .pyc）与断言全部原样保留。**没有为了让校验通过而修改任何哈希。**

## 真实重跑结果

```
python -B scripts/run_prepare.py --request cases/jak2_6vgl.json --out <新目录>
  CPython 3.11.9 + rdkit 2026.9.1 + meeko 0.8.0 + gemmi 0.7.5
  + numpy 2.4.6 + scipy 1.17.1 + tqdm 4.70.1     （与 requirements.lock.txt 完全一致）
  -> READY_FOR_03 / 0, integrity_status=INTEGRITY_PASS, 约 12 s
  产物目录：runs\jak2_rerun_patched_001\
```

与原交付 run 的关系（可复核）：

* 10 个产物中 6 个核心文件的**行尾归一化后**（`tr -d '\r'`）SHA-256 与原交付文件**逐字节相同**
  （receptor.pdbqt `8b3cd76a…`、ligand.pdbqt `132eac63…`、reference.sdf `d8eb3ae7…`、
  ligand.sdf `dd8699af…`、ligand_roundtrip.sdf `60bd8eeb…`、vina_box.txt `54a55c29…`）。
* 差异只来自 Windows 文本模式写出的 CRLF（例如 vina_box.txt 126 B vs 120 B，6 行 × 1 B），
  以及清单里的路径分隔符——都是平台字符串问题，不是化学差异。

## 结论

1. 02 的科学计算在本机可以真实重跑，结果与 macOS 交付物在化学上一致（行尾归一化后哈希相同）。
2. 交付包本身在 Windows 上无法返回 `READY_FOR_03`：属**上游待修的可移植性缺陷**，不是科学失败。
   建议上游把 `write_manifest` 改为 `as_posix()`，并明确文本模式统一用 `newline='\n'`。
3. 因此本工作流的编排入口（`scripts/run_pipeline.py --mode jak2`）默认使用**冻结交付 run**
   （`jak2_acceptance_001`，哈希经 `delivery_index.json` 交叉核对）作为 03 输入；要在本副本里
   重跑 02，直接执行 `python -B scripts/run_prepare.py --request cases/jak2_6vgl.json --out <新目录>`
   （本包的 `run_pipeline.py` 不再提供 `--stage02-rerun` 开关，避免一条命令里混两种输入来源）。

## 校验这个副本

```bash
cd src/module02-portability-win
sha256sum -c PACKAGE_SHA256SUMS
```

预期结果**正好**是：

* 60 个条目 `OK`；
* `scripts/common.py: FAILED` —— 这就是唯一被改动的文件（`str(...)` → `.as_posix()`），
  FAILED 本身就是改动证据，不是损坏；
* 58 个 `runs/...` 条目报「No such file」—— 副本按设计排除了 `runs/`。

换言之：`PACKAGE_SHA256SUMS` 这份原清单在此处变成了一份**差异审计**，而不是一句"校验通过"。
