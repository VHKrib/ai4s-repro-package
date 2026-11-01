# 上游反馈：02 模块（A4S-02-MODULE-001）Windows 可移植性缺陷

范围：`module02-module001`（v1.0.0），`scripts/common.py` + `scripts/check_integrity.py`
影响：**在 Windows 上真实重跑 02 准备，永远无法返回 `READY_FOR_03`**，即使 12 个科学步骤全部成功。

## 现象（可复现）

```
python -B scripts/run_prepare.py --request cases/jak2_6vgl.json --out <新目录>
```

- `records/commands.jsonl`：`01-dependency-probe` … `12-full-scientific-check` **全部 exit 0**
- `records/full_science_check.json`：`status: SCIENCE_PASS`，`issues: []`
- 但最终 `run_status.json`：

```
FAIL / 1   FINAL_INTEGRITY_FAILED
issues: INPUT_HASH_OR_PATH_MISMATCH   ×4   (structure, sequence, handoff, RXT_CCD)
        OUTPUT_HASH_OR_PATH_MISMATCH  ×10  (receptor, receptor_json, prepared_pdb, selected_protein,
                                            terminal_model, reference, ligand_sdf, ligand,
                                            ligand_roundtrip, vina_box)
        UNLISTED_BINDING              ×1   (records/box_binding.json)
```

## 根因：清单写反斜杠，终检用正斜杠比对

写清单（`scripts/common.py:50-56`）：

```python
def write_manifest(root):
    excluded={'records/manifest.json','SHA256SUMS'}
    paths=sorted(p for p in root.rglob('*') if p.is_file() and str(p.relative_to(root)) not in excluded ...)
    record={... 'files':[file_record(root,str(p.relative_to(root))) for p in paths]}
```

`str(p.relative_to(root))` 在 Windows 上产生 **`inputs\domain_experimental.pdb`**（反斜杠）。

终检（`scripts/check_integrity.py:20/46/51/52`）：

```python
name=row['path']; ...; listed[name]=row['sha256']          # 清单键：反斜杠
need(row['path']==relative and row['path'] in listed ...)  # job.json 值：正斜杠（INPUT_NAMES/REQUIRED_OUTPUTS 是 POSIX 字面量）
```

两边永不相等 → 每一条 INPUT/OUTPUT 都判 `*_PATH_MISMATCH`，`records/box_binding.json` 判 `UNLISTED_BINDING`。
`SHA256SUMS` 也由同一路径写出（`sha256sum -c` 在 Windows 上因此也不可用）。

**附带**：这些文本产物由 Python 文本模式写出，Windows 下换行为 CRLF，导致产物字节 SHA-256 与
macOS 交付不同（例如 `vina_box.txt` 126 B vs 120 B = 6 行 × 1 B），这会让任何跨机器哈希比对“看起来不一致”。

## 建议修法（两处，均不触及科学逻辑）

1. `scripts/common.py` — 清单路径统一 POSIX：

```diff
-    paths=sorted(p for p in root.rglob('*') if p.is_file() and str(p.relative_to(root)) not in excluded and '__pycache__' not in p.parts)
+    paths=sorted(p for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix() not in excluded and '__pycache__' not in p.parts)
     record={'schema_version':'a4s.file_manifest.v1','job_id':read_json(root/'job.json')['job_id'],
-            'files':[file_record(root,str(p.relative_to(root))) for p in paths]}
+            'files':[file_record(root,p.relative_to(root).as_posix()) for p in paths]}
```

2. 所有文本产物与 `SHA256SUMS` 统一 LF 写出（消除跨平台字节差异）：
   `write_text(..., newline='\n')`，或 `path.write_bytes(text.encode('utf-8'))`。
   （`write_json` 同理，若希望 `job.json` 也跨平台字节一致。）

3. 可选加固：`check_integrity` 比对前做 `.replace('\\','/')` 归一化，作为对旧 run 的兼容层。

## 已验证：修 1（仅路径分隔符）后真实重跑到 READY，且化学结果不变

- 沙箱副本（交付包外，只改上面第 1 处）：`READY_FOR_03 / 0`，`integrity_status=INTEGRITY_PASS`，12 步全 0
- 产物与 macOS 交付：**行尾归一化（`tr -d '\r'`）后 SHA-256 完全相同**

```
receptor.pdbqt         8b3cd76af7590d1aec6c967f7a71e693…  ==  交付
ligand.pdbqt           132eac63b562df0080ecac6893eb2b901…  ==  交付
reference.sdf          d8eb3ae7b53c319d24fea7040f53a2d89…  ==  交付
ligand.sdf / ligand_roundtrip.sdf / vina_box.txt            ==  交付
```

即：缺陷纯属**平台字符串处理**，不影响科学结论。

## 未改动声明

交付原件一行未改：`sha256sum -c PACKAGE_SHA256SUMS` 在两处目录均 exit 0，
`scripts/stage02.py check --target all` 仍为 `READONLY_CHECKS_PASS`（包完整性 441 文件）。
适配副本与 diff 全文见 `src/module02-portability-win/README-portability.md`。
