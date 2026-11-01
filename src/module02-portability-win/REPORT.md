# A4S-02-MODULE-001 执行报告

日期：2026-10-27（Asia/Hong_Kong）。模块版本1.0.0；本轮仅JAK2/6VGL/RXT、mode=smoke_test。**02封装及本地验收通过，READY_FOR_03/0；03仍待联调，尚无跨模块自动化通过结论。**

## 目标

从原CIF、FASTA、新版01交接说明、任务请求及冻结原始CCD定义，经单一入口真实生成受体、配体、参考SDF、盒和结果清单。本轮不需要01/03参与，不增加Agent、Skill、前端、Vina或科研功能。

工作目录为 `/Users/cityu/.codex/.chatgpt-projects/g-p-6ad38b0f13a081919e855e893d9e2c43/module02-module001`。根AGENTS.md适用，sources/只读。项目根及本模块、PREP/INTAKE/SMOKE/R1目录均非Git仓库，分支、commit和Git未提交状态不适用；旧EvidenceScope仍为独立main、无commit、原有未跟踪文件状态。379项旧文件SHA-256、旧工程/环境元数据和旧Git状态保护检查均通过。

保留原R1 ZIP，SHA-256仍为 `f4497ece7ea3b85c4eae8f8174ac72e40a3346fbd872b1eeeb241bc8784a0e99`。没有修改其报告、复现标准、科学输入或输出。

## 改动

新增标准库入口scripts/run_prepare.py、版本化批准配方和请求示例；复用原SMOKE配体重建/读回代码、R1去标签/OXT及完整受体内容检查。接口调整仅为显式run目录、从原CIF生成所需记录和给新产物绑定哈希；没有重写化学方法。具体旧源码摘要、函数名及调整范围见records/implementation_reuse.json。

配方 `jak2_6vgl_rxt/1.0.0` 的完整摘要为 `90dec5aa54cf592fefcac449b62547e24859e5b8636238c0818ab96eee45ba10`。代码仅批准此摘要，输入选择、源哈希、环境及模板也受限定；不让任意字符串触发删除残基、换链、默认WT或改变端基/PTR。缺必要信息/范围不符为BLOCKED/2，文件完整性或执行错误为FAIL/1。

沿用a4s.smoke_job.v1输出契约，在原字段之外增加inputs、request、prepare_profile、module_version、run_metadata以保存任务与来源关联。运行日志和最终stdout分开；stdout是单一结构化结果。失败请求也保留其原文哈希及可识别job_id，失败不留新READY声明。已有目录直接拒绝，旧run不改写。

输入相对路径全部以请求所在目录为基准；--out相对启动目录；输出job/manifest路径相对各自run根目录。科学脚本没有写死作者目录，也不导入相邻旧工程。历史绝对路径仅是本次解释器、命令及运行目录的元数据。

### 实际统一命令与环境

请求：`cases/jak2_6vgl.json`；已生成输出：`runs/jak2_acceptance_001/`。工作目录为本模块根。

```sh
/Users/cityu/.codex/.chatgpt-projects/g-p-6ad38b0f13a081919e855e893d9e2c43/module02-prep/.venv/bin/python -B scripts/run_prepare.py \
  --request cases/jak2_6vgl.json --out runs/jak2_acceptance_001
```

上式为同一实际命令在模块根下的相对路径写法；完整实际argv、cwd、状态与退出码在records/acceptance_tests/commands.jsonl和A_raw_to_complete.stdout.json。该已交付目录现在存在，重复执行会按设计拒绝；复跑应换新的--out。

实际解释器为上述完整路径，Python3.11.9/arm64；RDKit2026.9.1、Meeko0.8.0、Gemmi0.7.5、NumPy2.4.6、SciPy1.17.1、tqdm4.70.1。没有新增/升级依赖，软件锁定与模板摘要沿用原环境。环境实测记录位于run内records/runtime_environment.json。

## 验证

### 从原始资料真实构建的证据

新run的records/staging.json记录首个科学步骤前的6件输入：请求、配方、CIF、FASTA、01说明、原始CCD；**prepared_products_present=false**。当时没有受体/配体PDBQT、SDF、PDB或盒成品。冻结CCD是原始化学定义，不是旧处理结果；保留原获取地址、日期和哈希，没有联网替换原材料。

实际records/commands.jsonl包含12步，全退出0；关键步骤如下（每项均有独立完整stdout/stderr）：

| 步骤 | 真实操作 | 退出码 |
|---|---|---:|
| 03 | CIF/标签/UniProt/FASTA审计，生成配体组分表 | 0 |
| 04 | 新建去标签域，按已批准几何补OXT | 0 |
| 05 | meeko.cli.mk_prepare_receptor，新制刚性受体/JSON/PDB | 0 |
| 06 | 从CIF指定RXT实例及原始CCD重建reference.sdf和加氢ligand.sdf | 0 |
| 07 | meeko.cli.mk_prepare_ligand，新制ligand.pdbqt | 0 |
| 08 | meeko.cli.mk_export，真实读回SDF | 0 |
| 09/10 | 完整受体及配体内容检查 | 0 |
| 11 | 从本次生成的reference.sdf计算盒并绑定本次新受体/配体 | 0 |
| 12 | 提升READY之前完整科学验收：SCIENCE_PASS | 0 |

受体关键实际命令：

```sh
python -B -m meeko.cli.mk_prepare_receptor --read_pdb inputs/domain_terminal_model.pdb \
  -o outputs/receptor -p outputs/receptor.pdbqt -j outputs/receptor.json \
  --write_pdb outputs/receptor_prepared.pdb \
  --set_template A:840=NASP,A:1007=PTR,A:1132=CGLY \
  --debug_fn records/logs/meeko_receptor_debug.log
```

实际日志写出 `Template padding will be used`、`gasteiger charges will be read from template file`，以及三份新受体文件；退出0。

配体关键实际命令：

```sh
python -B -m meeko.cli.mk_prepare_ligand -i outputs/ligand.sdf -o outputs/ligand.pdbqt \
  --charge_model gasteiger --merge_these_atom_types H --add_index_map -v
python -B -m meeko.cli.mk_export outputs/ligand.pdbqt -s outputs/ligand_roundtrip.sdf
```

日志包含真实Molecule Setup、原子坐标/电荷/类型/合并标记；退出0。上述python在记录中均为本次解释器完整路径，工作目录均为新run；没有调用旧工程产物或将旧SDF改名充当原件。

### 科学内容与R1基线

固定对象再次核对为6VGL模型1，蛋白auth A/label D，RXT auth A:1201/label H。仅排除表达标签的12个已观测残基/82重原子，保留840–1132的293残基/2421实验重原子，位移0 Å。端基仍为NASP/CGLY，PTR1007仍为PTR−2；新增1个建模氧及2402个建模H，PDBQT为2422重原子+559极性H。原有对称命名变化与实验/建模来源逐原子映射保留。

本轮reference/ligand均由CIF/CCD重建；RXT为C17H18N6、形式0、CAV=R，23重原子，必要加18个H，Meeko合并17个C连接H；读回身份、手性、键与实验重原子坐标通过。最大重原子位移0 Å；不将加氢或读回成功称为生理质子化验证。

搜索盒从本次reference的23个实验重原子包围盒、每面5 Å重新计算，中心(56.590,−6.306,23.830) Å，尺寸(17.126,18.178,14.554) Å；与新受体保持同一坐标系。新绑定记录同时关联新受体、配体、参考SDF、选定域和源CIF。

**10项核心科学文件与R1逐字节一致**：两份域PDB、受体PDBQT/JSON/准备后PDB、reference.sdf、ligand.sdf、ligand.pdbqt、读回SDF、盒文件。没有科学差异需要更改处理方案。job及绑定记录因新增请求/配方/配体关联字段而不同；运行时间、job_id、目录和解释器路径归为运行元数据，单独检查关联正确性，未据此判化学失败。R1旧标准原样保留。

### 正常、迁移与异常测试

最终测试命令：

```sh
python -B tests/test_module.py --evidence-dir records/acceptance_tests --run-out runs/jak2_acceptance_001
```

该调度命令退出0、总状态PASS；下表给出每条真实子命令状态（不是将预期异常当成功运行）：

| 场景 | 实际状态 | 退出码 | 关键结果 |
|---|---|---:|---|
| A：空的新目录从原始输入全流程 | READY_FOR_03 | 0 | 全部生成步骤真实执行 |
| A：完整科学只读复核 | READY_FOR_03 | 0 | 科学内容通过、文件哈希未变 |
| A：仅标准库接收检查（-S） | INTEGRITY_PASS | 0 | 56项清单文件核对，哈希未变 |
| B：含空格的新目录、无相邻旧工程 | READY_FOR_03 | 0 | 10核心文件字节相同，化学身份/坐标/参数一致 |
| B：迁移后的标准库检查（-S） | INTEGRITY_PASS | 0 | 关联正确、哈希未变 |
| C：label链改为X | BLOCKED | 2 | UNSUPPORTED_SELECTION_OR_POLICY；未换链 |
| C：源哈希改为64个0 | FAIL | 1 | INPUT_HASH_MISMATCH；未继续科学处理 |
| C：缺少序列字段 | BLOCKED | 2 | REQUEST_FIELDS_NOT_SUPPORTED |
| C：把配方排除范围改为825–850且更新其自报摘要 | BLOCKED | 2 | PROFILE_NOT_APPROVED；不能扩大删除范围 |
| 缺少科学依赖（-S关闭加载） | FAIL | 1 | MISSING_SCIENTIFIC_DEPENDENCY；无READY |
| D：临时篡改新配体PDBQT | FAIL | 1 | HASH_OR_SIZE_MISMATCH；检查不把内容改回 |
| E：再次指定已有正式输出目录 | FAIL | 1 | OUTPUT_EXISTS、result=null；原run全部哈希不变 |

迁移测试另改job_id并重新执行生成；运行目录、时间、job_id确实变化，metadata关联仍正确。原始材料和脚本都随模块迁移，没有相邻EvidenceScope或旧SMOKE目录。证据包包含迁移后的真实命令、起始清单、元数据、job快照及Meeko关键日志，位于records/acceptance_tests/migration_evidence/；不要求教授访问Mac临时目录。

只读检查前后哈希比较以全部文件为范围；篡改只发生在临时副本。错误输入的run记录为FAIL/BLOCKED、ready=false，并保留实际请求及其哈希/job_id；未产生成功占位文件。

## 阻塞与限制

本轮02封装无技术阻塞。支持范围限定为冻结的6VGL/JAK2/RXT配方与锁定环境；JAK1、新CIF/链/候选、任意蛋白清洗或通用修复仍未支持。环境缺失会明确失败，模块不安装依赖。干式孤立域、自由端基、PTR二阴离子、默认HIE与共晶初始坐标均保留为smoke假设；没有生理状态、活性、亲和力或独立姿态准确率结论。

轻量检查只确认路径、必要文件、哈希、状态及绑定，成功明确叫INTEGRITY_PASS；它不重新解析结构或证明质子化正确性。完整科学检查仍使用现有依赖并在准备结束前实际运行。科学产物可重复与模块元数据可变化分别报告。

## 下一步与交付

交付A4S-02-MODULE-001_handoff.zip，含模块代码、批准配方、可运行请求、原始材料/CCD、一个真实干净构建run、只读验收、最终针对性测试证据、软件锁定和本报告。不含虚拟环境、缓存、旧成品、旧失败日志合集或开发中重复run。原R1包在旧目录保留。

先由教授验收本02模块封装，再另批接入JAK1。本批不要求01或03执行；03/Vina实际读取、SciencePal全链路与跨模块自动化仍未验收。

## 核心SHA-256与实际执行证据

以下core列表由本次新run实际计算，与R1基线逐项一致。

| run内相对路径 | SHA-256 | 与R1 |
|---|---|---|
| `inputs/domain_experimental.pdb` | `40b708d164c77159592a06e4706cae27da539dc713c6e2a44a5a9256e264777a` | 一致 |
| `inputs/domain_terminal_model.pdb` | `b973c8282829237238a8fa07f96184f394af9f01eb48e6ab354eeca48eb5b203` | 一致 |
| `outputs/receptor.pdbqt` | `8b3cd76af7590d1aec6c967f7a71e693f4abf5b6196750cd0bf5ec9881ba27e7` | 一致 |
| `outputs/receptor.json` | `5b26d15e4bce0edbce61852caa1ef3e04c86285a8bb44bf574f64e90d7ca81ab` | 一致 |
| `outputs/receptor_prepared.pdb` | `18f31e42501cb7c101b220bbcfdf7df143a644ea7bfcd841e7a7634a1e4692f6` | 一致 |
| `outputs/ligand.sdf` | `dd8699af30ad458a472e1800f0a3b4b8a6d382cef2824f84b4bc61dcf68cec28` | 一致 |
| `outputs/ligand.pdbqt` | `132eac63b562df0080ecac6893eb2b90180a7dd4c25db4482a13337979cc1b62` | 一致 |
| `outputs/reference.sdf` | `d8eb3ae7b53c319d24fea7040f53a2d89dae39da7e6f8fe15cbf074b0df28667` | 一致 |
| `outputs/ligand_roundtrip.sdf` | `60bd8eebfd106914c0d194653f6a1ce874fde2113b89aa182c22f9b1a1024882` | 一致 |
| `outputs/vina_box.txt` | `54a55c292e4456be57b2eaffc5ca1ed36179ce43780b9adfeaba0479b1477ffa` | 一致 |

批准配方、原始输入摘要均保存在请求、profile与run中，结构源SHA仍为`ba70401d4a19a2e358227d1aa9b09608ab015f9e16abd9aaae7ec2c2745f3c4f`。原始RXT CCD SHA为`eb6239f83887d56a88ce713ebaac5cc27e6edfea9814e59a3b23a7eeb58c28dc`；冻结原件的原获取入口为https://files.rcsb.org/ligands/download/RXT.cif，获取记录原样保留。

**实际完整统一argv（正常路径，退出0）**

```sh
/Users/cityu/.codex/.chatgpt-projects/g-p-6ad38b0f13a081919e855e893d9e2c43/module02-prep/.venv/bin/python -B /Users/cityu/.codex/.chatgpt-projects/g-p-6ad38b0f13a081919e855e893d9e2c43/module02-module001/scripts/run_prepare.py --request /Users/cityu/.codex/.chatgpt-projects/g-p-6ad38b0f13a081919e855e893d9e2c43/module02-module001/cases/jak2_6vgl.json --out /Users/cityu/.codex/.chatgpt-projects/g-p-6ad38b0f13a081919e855e893d9e2c43/module02-module001/runs/jak2_acceptance_001
```

**实际完整迁移argv（退出0，无相邻旧工程）**

```sh
/Users/cityu/.codex/.chatgpt-projects/g-p-6ad38b0f13a081919e855e893d9e2c43/module02-prep/.venv/bin/python -B '/var/folders/y8/s_tmmyqj05dcbb3dpnknm_2c0000gn/T/A4S MODULE move j09jz474/package with spaces/scripts/run_prepare.py' --request '/var/folders/y8/s_tmmyqj05dcbb3dpnknm_2c0000gn/T/A4S MODULE move j09jz474/package with spaces/cases/jak2_6vgl.json' --out '/var/folders/y8/s_tmmyqj05dcbb3dpnknm_2c0000gn/T/A4S MODULE move j09jz474/package with spaces/results with spaces/run 002'
```

全部12项测试的完整命令、实际cwd、退出码和stdout/stderr见records/acceptance_tests/commands.jsonl及同名文件。源码可携带性另作静态确认：scripts/没有作者路径或旧工程导入；正常run的implementation_sha256与交付代码逐项相符。

打包仅收录当前验收run和acceptance_tests；开发期间其他重复run与早期测试留在本地、未覆盖且不进入交接ZIP。ZIP完整哈希和解压后的只读复核回执保存在归档外records/delivery.json、records/final_archive_check.json，避免自哈希循环。包内PACKAGE_SHA256SUMS供整体文件核对；run内清单用于轻量接收检查。

