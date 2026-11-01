# a4s-repro

把 A4S（AI4ScienceHackthon）的 **02 结构准备 → 03 评估决策** 工作链路打包成一个
「解压、建环境、跑一条命令」的复现包，并附一个真实的端到端案例作为验收：

> **HIV-1 蛋白酶（PDB 1T3R，1.20 Å）× Darunavir 共晶自对接**
> 期望：9 个姿势、最佳 −9.958 kcal/mol、重对接 RMSD 0.211 Å、stage-3 决策 `advance`。

包里的每个数字都是真实跑出来的，`scripts/run_pipeline.py` 会把本次运行的结果与
`cases/hiv1_1t3r_darunavir/case.json` 里的期望值**逐项比对**，全中才返回 `verdict=OK`。

---

## 0. 30 秒验收（依赖已装好的情况）

```bash
python tests/smoke_test.py
```

不跑对接、不联网，约 2 秒：校验包内输入哈希 → 校验 vina 二进制 → 真跑 `stage3 validate`
（重读全部产物、重算 sha256）→ 比对数.

## 1. 本包需要什么

| 需要 | 说明 |
|---|---|
| **Windows 10/11 x64** | 脚本与二进制按 Windows 准备。Linux/macOS 理论可跑（`setup_envs.ps1` 之外的步骤都是纯 Python），**未验证**。 |
| **Python 3.11.9** | 需要两个环境：`py3119-science`（rdkit/meeko/gemmi）和 `py3119-stage3`（纯标准库）。`setup_envs.ps1` 用本机任意 3.11 解释器自动创建，不需要装进系统 Python。版本不是 3.11.9 也能跑，但数字未经保证（脚本会告警）。 |
| **网络（仅首次）** | `setup_envs.ps1` 从 PyPI 装 `requirements/science.txt`，约 150 MB 下载。之后完全离线。 |
| **AutoDock Vina 1.2.5** | **已内置** `tools/vina_1.2.5_win.exe`（1.2 MB，sha256 已记录），不需要自己下。 |
| **SciencePal API key** | **可选**。只有 `scripts/sciencepal_bridge.py`（01 编排层桥接）用得到；不填它，本地 02/03 链路照常跑通。 |
| 磁盘 | 包本体约 5 MB，两个环境约 700 MB，一次运行产物约 10 MB。 |
| 内存/CPU | 无特殊要求；脚本里 `cpu=4`，在更少核心的机器上会自动变慢但结果相同（Vina 的 seed 固定）。 |

**不需要**：GPU、数据库、Docker、管理员权限、任何云端账号。

## 2. 从零开始（5 步）

```powershell
# 1) 解压后进入包根目录
cd C:\path\to\a4s-repro

# 2) 建两个 Python 环境并装锁定依赖（首次约 2–4 分钟）
powershell -ExecutionPolicy Bypass -File setup_envs.ps1
#   或： setup_envs.bat
#   本机没有 3.11 在 PATH 时，显式指定解释器：
#   powershell -ExecutionPolicy Bypass -File setup_envs.ps1 -PythonExe C:\Python311\python.exe
#   （或先 set A4S_PYTHON311=C:\Python311\python.exe）

# 3) 单命令自检（约 2 秒）
python tests\smoke_test.py

# 4) 完整重算一个案例（约 1 分 20 秒：两次真实对接 + stage-3 全链路）
python scripts\run_pipeline.py

# 5) 看结果
type runs\hiv1_1t3r_darunavir-<时间戳>\summary.md
```

第 4 步会在 `runs/<case_id>-<时间戳>/` 下写出 `result.json`、`summary.md`、`logs/`
和一份全新的 stage-3 运行目录；退出码 0 表示 19 项比对全部通过。

### 分步跑（想逐步看）

```powershell
$env:PYTHONPATH = "scripts\shim"     # stage-3 的 vina 引擎需要这个 shim
python scripts\run_pipeline.py --case cases\hiv1_1t3r_darunavir --out runs\mycheck
```

## 3. 预期输出

```
==============================================================================
verdict            : OK
case               : hiv1_1t3r_darunavir
poses / best score : 9 / -9.958 kcal/mol
redock RMSD        : 0.211 A
stage3 validate    : OK  10 outputs validated: reference_check, pose_metrics, ...
result.json        : ...\runs\hiv1_1t3r_darunavir-<ts>\result.json
==============================================================================
```

`result.json` 里的 19 项检查：

| 类别 | 检查项 |
|---|---|
| 输入完整性 | `input_sha256:pdb`、`input_sha256:ligand_template_sdf`、`vina_binary_sha256` |
| 对接 | `pose_count`、`affinities_by_pose`、`best_affinity_kcal_mol` |
| 重对接 | `redock_best_rmsd_A`、`redock_pose_index` |
| 制备 | `box_center`、`box_size`、`protein_atoms`、`receptor_pdbqt_atoms`、`ligand_heavy_atoms`、`ligand_formula` |
| stage-3 | `stage3_validate_exit`、`stage3_pose_rows`、`stage3_best_affinity`、`stage3_decision`、`stage3_outputs_validated` |

参考数字见 `cases/hiv1_1t3r_darunavir/README.md`，方法见 `docs/方法说明.md`。

## 4. 目录结构

```
a4s-repro/
├─ README.md                    本文件
├─ LICENSE                      MIT（自研部分）
├─ THIRD_PARTY_NOTICES          第三方组件许可（逐项核实过，含 Meeko 实为 LGPL-2.1）
├─ VERSION
├─ requirements/
│  ├─ science.txt               py3119-science 的 pip freeze 精确锁定
│  ├─ stage3.txt                py3119-stage3（标准库，无三方依赖）
│  └─ bridge.txt                可选：requests，仅 SciencePal 桥接
├─ setup_envs.ps1 / .bat        建环境 + 装依赖 + 自检
├─ tools/
│  ├─ vina_1.2.5_win.exe        AutoDock Vina 1.2.5（Apache-2.0）
│  ├─ vina_1.2.5_win.exe.sha256
│  └─ SOURCE.md                 来源、版本、为什么内置二进制
├─ scripts/
│  ├─ run_pipeline.py           一键编排：case 模式（默认）/ jak2 模式
│  ├─ a4s_redock.py             PDBQT → SDF → RDKit GetBestRMS
│  ├─ pipeline_inputs.py        02 交付 → stage-3 输入的桥（jak2 模式用）
│  ├─ sciencepal_bridge.py      SciencePal API 桥接（可选，需 key）
│  ├─ af3_to_stage3_manifest.py AF3 产物 → stage-3 af3 manifest
│  └─ shim/vina.py              vina Python API → 官方 vina.exe 的适配层
├─ cases/hiv1_1t3r_darunavir/   最小可复现案例（结构、配体、配置、期望数字、参考运行）
├─ src/
│  ├─ module02-portability-win/ 02 Windows 适配副本（原件未改，仅 as_posix + 换行）
│  └─ stage3-agent/             03 管线（intake → qc → rank → decide → export → validate）
├─ docs/
│  ├─ 方法说明.md               算的是什么、每个数字怎么来的、限制
│  ├─ 证据索引.md               结论 → 证据文件 → 复核命令
│  ├─ 02_UPSTREAM_DEFECT_write_manifest.md   上游 02 的 Windows 缺陷与修法
│  ├─ 桥接报告.md               SciencePal 联调实录（脱敏）
│  └─ AF3摸底报告.md            AF3 能力摸底
└─ tests/smoke_test.py          单条命令自检（不跑对接）
```

`runs/`、`envs/`、`__pycache__` 都是运行时生成的，不在交付 zip 里。

## 5. 配置与路径

没有任何硬编码的绝对路径——全部由「脚本所在目录」推导，必要时用环境变量覆盖：

| 变量 | 作用 | 默认 |
|---|---|---|
| `A4S_ROOT` | 包根目录 | 由脚本位置推导 |
| `A4S_VINA_EXE` | vina 二进制 | `<包>/tools/vina_1.2.5_win.exe` |
| `A4S_SCIENCE_PYTHON` | science 环境解释器 | `<包>/envs/py3119-science/Scripts/python.exe` |
| `A4S_STAGE3_PYTHON` | stage-3 环境解释器 | `<包>/envs/py3119-stage3/Scripts/python.exe` |
| `A4S_RUNS` | 运行产物目录 | `<包>/runs` |
| `A4S_STAGE02_DELIVERY` | 02 交付路径（仅 jak2 模式） | `<包>/src/handoff_skill/a4s-stage02-delivery-v1.1.0`（未随包分发） |
| `A4S_SCIENCEPAL_KEY_FILE` | SciencePal key 文件（可选） | `<包>/secrets/sciencepal_key.txt` |
| `A4S_PYTHON311` | `setup_envs.ps1` 用来建环境的 3.11 解释器 | 自动在 PATH / 常见安装位置查找 |
| `A4S_LOGS` / `A4S_SCIENCEPAL_LOCK` | 桥接日志 / initiate 锁 | `<包>/logs`、`<包>/sciencepal_initiate_lock.json` |

`scripts/run_pipeline.py --science-python ... --stage3-python ... --out ...` 也可直接指定。

## 6. 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `FATAL: science python missing: ...` | 没建环境 | 跑 `setup_envs.ps1` |
| `pip` 报 `Could not find a suitable TLS CA certificate bundle` | 本机 `CURL_CA_BUNDLE` / `SSL_CERT_FILE` 指向不存在的文件 | `setup_envs.ps1` 会自动清理；手工处理：`$env:CURL_CA_BUNDLE=""` |
| `FATAL: --stage02-delivery must point at a module-02 delivery` | 用了 `--mode jak2` 但没给 02 交付 | 02 交付未随包分发（约 217 MB），用 `--stage02-delivery <路径>` 指定；只跑 HIV-1 案例不需要它 |
| `vina executable not found` | 二进制缺失或被改名 | 从 `tools/` 恢复；`tools/SOURCE.md` 有 sha256 |
| `input verification failed: ['pdb']` | 输入文件被改过 | 重新解压对应文件；哈希记录在 `case.json` |
| `verdict: MISMATCH` + `pose_count` / `affinities` 不符 | 用了非 1.2.5 的 vina，或改了 seed/exhaustiveness | 恢复默认参数；见 `case.json → engine` |
| `stage3 validate` 报 `sha256 mismatch` | 有人改过 `evidence/reference_run/outputs/` | 重新解压；该目录只读使用 |
| `ModuleNotFoundError: No module named 'vina'` | 手工跑 stage3 时没设 `PYTHONPATH` | `set PYTHONPATH=scripts\shim`（`run_pipeline.py` 自动设） |
| 中文路径/乱码 | Windows 控制台编码 | `chcp 65001`；文件本身都是 UTF-8 |
| 用 Git-Bash 的 `unzip` 解压后 `docs/方法说明.md` 等变成乱码文件名 | MSYS `unzip` 不认 zip 里的 UTF-8 文件名标志 | 用资源管理器「全部解压缩」、`Expand-Archive` 或 7-Zip 解压（zip 本身没问题，`tests/smoke_test.py` 会报 `layout.files_present` 缺失） |
| 机器只有 2 核，跑得很慢 | `cpu=4` 是建议值 | 结果与核数无关（seed 固定），只是更慢 |

## 7. 两种模式

* **`--mode case`（默认）**：跑包内的复现案例。输入 → 制备 → 对接 → 重对接 → stage-3 →
  validate → 与期望值比对。这是验收路径。
* **`--mode jak2`**：原始的 02→03 链路（读冻结的 02 交付、过 `READY_FOR_03` 门、两次对接、
  重对接、validate）。需要你自己提供 02 交付目录（未随包分发）。

## 8. 数据、许可与引用

自研代码 MIT（`LICENSE`）。第三方组件在 `THIRD_PARTY_NOTICES` 里逐项列出并**核对了实际
license 字段**——注意 **Meeko 是 LGPL-2.1**（不是 BSD-3），它只作为未修改的 PyPI 依赖
安装，未被复制进本包。结构数据来自 wwPDB（CC0），配体模板来自 PubChem（公有领域），
Vina 二进制按 Apache-2.0 原样分发。

## 9. 限制（请连同结果一起读）

1. 对接分是 **Vina 打分函数值**，不是实测亲和力，不能与 Kd/IC50 直接比较。
2. 本案例是 **co-crystal redocking 方法自检**，不是新抑制剂发现；低 RMSD 只证明协议能
   复现已知姿势。
3. 只做了**刚性受体 + 单一 vina 协议**：无突变 panel、无 MD、无 MM/GBSA、无 AF3、无重复
   种子统计。`result.json → not_run` 里这些是 `NOT_RUN`，不是 0。
4. 02 模块原批准环境是 macOS arm64 + CPython 3.11.9；Windows 上属跨平台组合，化学结果
   一致但**未经上游验证**（见 `docs/02_UPSTREAM_DEFECT_write_manifest.md`）。
5. `src/module02-portability-win/` 是适配**副本**，02 原件未改、也未随包分发。

## 10. 移植测试记录（2026-11-01，真机真做）

在一台**全新的空目录**里从 zip 解压、按本 README 从零执行，不是"看起来能跑"。

* 机器：Windows 11 x64（10.0.26200），无 GPU
* 目标目录：`<PKGTEST>\v1`（原本不存在）
* 解压：`Expand-Archive`（与资源管理器「全部解压缩」同一套 API）
* 基础解释器：CPython **3.11.9**（本机 PATH 上没有 3.11，故用
  `set A4S_PYTHON311=<path to python3.11>` 指定，README §2 已写明这条回退路径）

| # | 步骤 | 命令 | 退出码 | 耗时 |
|---|---|---|---|---|
| 1 | 解压 zip | `Expand-Archive a4s-repro-v1.0.0.zip -DestinationPath <PKGTEST>\v1` | 0 | ~2 s |
| 2 | 硬编码检查 | `grep -rn "$OLD_USER" a4s-repro/`（`$OLD_USER`＝开发机用户名） | 0（**0 行命中**） | <1 s |
| 3 | 建环境 + 装依赖 | `cmd /c setup_envs.bat`（内部 `setup_envs.ps1`） | **0** | **34 s** |
| 4 | 单命令自检 | `python tests\smoke_test.py` | **0** | **<1 s（10/10 PASS）** |
| 5 | 完整案例重算 | `python scripts\run_pipeline.py` | **0** | **75 s** |

第 3 步的真实输出（摘要）：两个 venv 均为 CPython 3.11.9；science 环境装上
`colorama 0.4.6 / gemmi 0.7.5 / meeko 0.8.0 / numpy 2.4.6 / pillow 13.0.0 /
rdkit 2026.9.1 / scipy 1.17.1 / tqdm 4.70.1`；stage3 环境无三方依赖；
`mk_prepare_receptor.exe / mk_prepare_ligand.exe / mk_export.exe` 都在；
`rdkit 2026.09.1 | gemmi 0.7.5 | meeko 0.8.0` 导入成功。

第 5 步的真实输出（摘录）：

```
==============================================================================
verdict            : OK
case               : hiv1_1t3r_darunavir
poses / best score : 9 / -9.958 kcal/mol
redock RMSD        : 0.211 A
stage3 validate    : OK  10 outputs validated: reference_check, pose_metrics, ...
result.json        : ...\runs\hiv1_1t3r_darunavir-20261101-164533\result.json
==============================================================================
```

分步耗时（来自该次 `result.json`）：制备 0.27 / 1.44 / 0.77 s，**对接 33.4 s**，
重对接 5.4 s，stage-3 **33.2 s**，validate 0.1 s。19 项比对 **19 PASS / 0 FAIL**：

| 比对项 | 期望 | 实测 |
|---|---|---|
| 姿势数 | 9 | 9 |
| 9 个分数（pose 1→9） | −9.958 / −9.148 / −8.877 / −8.782 / −8.366 / −8.233 / −7.946 / −7.880 / −7.810 | 完全一致 |
| 最佳对接分 | −9.958 | −9.958 |
| 重对接 RMSD | 0.211 Å | 0.211 Å（pose 1） |
| 盒子 center / size | (19.459, 29.766, 14.159) / (20.361, 20.806, 18.122) | 一致 |
| 蛋白 / 受体 / 配体原子 | 1496 / 1811 / 38 | 一致 |
| 配体分子式 | C27H37N3O7S | 一致 |
| stage-3 决策 | advance | advance |

> 这次运行是**新算的**：`runs/hiv1_1t3r_darunavir-20261101-164533/` 由本次解压出来的包
> 现场生成，`evidence/reference_run/` 那份 2026-10-30 的历史运行没有被使用或覆盖。

同一台开发机上另外验证了 `--mode jak2`（02→03 原链路，需要外部 02 交付）：
退出码 0，`job A4S-02-MODULE-001-JAK2-001`，最佳对接 −8.833 kcal/mol，
重对接 RMSD 0.082 Å，`validate OK 10 outputs validated`，19.7 s。

已知解压差异：用 **Git-Bash 自带的 `unzip`** 解压时，`docs/方法说明.md` 这类中文文件名
会变成乱码（MSYS `unzip` 不读 zip 的 UTF-8 文件名标志）；用资源管理器 / `Expand-Archive` /
7-Zip 解压正常。zip 内容本身无问题，见 §6 最后两行。
