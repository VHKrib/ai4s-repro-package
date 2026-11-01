# A4S-02-MODULE-001 — 02本地准备模块 v1.0.0

仅支持已批准的 **JAK2/6VGL/RXT smoke_test**：模型1，蛋白auth A / label D；RXT auth A:1201 / label H。固定科学配方保持SMOKE-001-R1不变。模块不要求01或03在线，不运行Vina、筛选或活性/亲和力评价。03状态仍为待联调。

## 单一准备入口

在本模块目录，使用已有锁定环境；输出目录必须不存在：

```sh
"/path/to/verified/python" -B scripts/run_prepare.py \
  --request cases/jak2_6vgl.json --out runs/new_jak2_run
```

当前实测环境是CPython3.11.9/macOS arm64，RDKit2026.9.1、Meeko0.8.0、Gemmi0.7.5，其余版本见requirements.lock.txt及结果的records/runtime_environment.json。没有自动安装或升级步骤。依赖缺失为FAIL/1；环境版本/模板不在批准范围为BLOCKED/2。跨操作系统尚未验证；同环境的目录迁移（含空格）已验证。

入口从原CIF、FASTA、01说明、请求与冻结CCD定义开始，真实执行去标签/OXT、受体准备、CIF配体重建/加氢、配体准备/读回和盒计算。**不复制任何旧SDF、PDBQT或盒成品。** 最终stdout仅有一个状态JSON，逐步stdout/stderr和真实命令单独写入新run的records/logs及records/commands.jsonl。

已交付的新运行结果位于 `runs/jak2_acceptance_001/`。不要将其作为新运行输出覆盖；复跑请选另一个不存在的目录。

## 输入契约

请求schema为 `a4s.prepare_request.v1`，以下字段必需；实例见cases/jak2_6vgl.json。

| 字段 | 规则 |
|---|---|
| schema_version、job_id、mode、target_id | mode仅smoke_test；目标仅JAK2:O60674；job_id用于结果关联，不作为文件路径 |
| source_structure | 6VGL source_id、相对路径、完整SHA-256 |
| sequence、handoff | FASTA及新版01说明的相对路径、SHA-256 |
| chemical_definition | RXT原始CCD的相对路径、SHA-256；不是准备后SDF |
| prepare_profile | id、version、相对路径、SHA-256；仅批准的jak2_6vgl_rxt/1.0.0摘要可执行 |
| model、protein、reference_ligand | 必须精确匹配已批准模型、两套链名及配体实例 |
| search_box | reference_heavy_atom_AABB、padding5.0、angstrom |

**所有输入相对路径均以请求文件所在目录解析**，包括配方和CCD；可使用示例中的`../inputs/`。`--out`相对路径以启动命令的当前目录解析。输出job与manifest中的路径均相对各自run根目录解析，禁止绝对路径或越界输出引用。历史执行目录和解释器绝对路径只保留在运行元数据中，不用于解析科学文件。

科学策略位于profiles/jak2_6vgl_rxt_v1.json，并由代码批准完整文件摘要；不会把请求中的任意字符串解释为删除残基、改模板、换WT或改位点指令。源材料也按本配方固定哈希限定。新材料、新配方、JAK1或其他链需要另行审阅，本版返回BLOCKED。

## 输出契约与状态

沿用 `a4s.smoke_job.v1`：原source_structure、protein、reference_ligand、outputs、search_box、termini/PTR、preparation_status等字段保留；新增inputs、request、prepare_profile、module_version、run_metadata，说明输入和配方的关联。

| 结果 | 含义 |
|---|---|
| READY_FOR_03 / 0 | 02完整科学检查与最终完整性检查通过 |
| FAIL / 1 | 执行、文件完整性或技术错误；不输出READY |
| BLOCKED / 2 | 信息不足或超出固定配方/环境支持范围 |

RUNNING、PREPARED_PENDING_VALIDATION是内部过程状态，ready_for_03=false。失败保留已有日志和中间文件作为证据；不会制造成功占位文件。已有输出目录拒绝覆盖并返回FAIL/1、result=null，原run保持原状。失败请求仍保存实际请求副本、哈希及可识别job_id。

核心产物在每个run中为：outputs/receptor.pdbqt、ligand.pdbqt、reference.sdf、ligand.sdf、ligand_roundtrip.sdf、vina_box.txt；并保留域PDB、Meeko JSON及准备后PDB。job.json给出所有相对路径、SHA-256、假设与状态。记录含原子对应、全部建模H、对称命名变化、来源与盒绑定。

## 两种只读检查

完整性检查只用标准库，本轮在关闭第三方包加载的`-S`模式实测：

```sh
python3 -S -B scripts/check_integrity.py --root runs/jak2_acceptance_001
```

成功为 **INTEGRITY_PASS/0**，范围仅路径、必需文件、哈希、任务状态、输入/配方关联和受体/配体/盒绑定；不重新验证结构化学或质子化。

完整科学复核需要锁定环境：

```sh
"/path/to/verified/python" -B scripts/check_science.py --root runs/jak2_acceptance_001
```

两个入口均不改写输入、job、盒或清单。生成流程在提升为READY前调用完整科学复核，其SCIENCE_PASS不等同于已提交03。每个run的records/manifest.json与SHA256SUMS属于该run；根PACKAGE_SHA256SUMS与records/package_manifest.json覆盖交付代码、原始资料、证据及选定run。校验失败不能通过重写清单自动消除。

## 科学方案及验证边界

仅排除CIF明确标注的表达标签825–839；保留840–1132的293残基、2421实验重原子和PTR1007。N端NASP游离铵基、C端CGLY羧酸盐；1个OXT按R1已批准的反射平面几何/1.25 Å构造；PTR为−2模板。新增氧及H均为modelled，实验重原子不移动。干式刚性、默认HIE等化学状态继续作为smoke假设；没有生理pH验证。Meeko对PTR环原子及末端O/OXT的对称命名变化保留映射。

科学比较单独评价10项核心文件字节、身份/坐标/参数和来源关联。时间、解释器路径、运行目录与job_id是运行元数据，变化不据此判定化学失败。原R1报告与复现标准未修改。

测试入口：`python -B tests/test_module.py --evidence-dir records/NEW_tests --run-out runs/NEW_run`。只覆盖本批封装风险；异常与迁移在临时副本进行。实际证据见records/acceptance_tests/，结果和必要日志摘要见 [REPORT.md](REPORT.md)。原始CCD来源与获取哈希见definitions/RXT_CCD_provenance.json；Meeko许可原文随definitions/meeko_LICENSE保存。
