# SciencePal Biology Agent × AF3 能力摸底报告

- 卡片：`t_ef07cf4c`（父卡 `t_56ad28cc`）
- worker session：`local-25e363b33f364110262c66374263c5029f802cd9808d9ead69dd3e47499bf31f`
- 时间：2026-10-31 00:10–00:25（UTC+8）
- 成本：**2 个 agent run**（用户授权上限 2，已用满，0 重试、0 二次 initiate）

---

## 一、结论（先行）

**能。SciencePal 的 Biology Agent 是真 AF3 执行器，可以作为 stage3 `af3` 引擎的数据源 —— 但只能承担「结构补全 + 置信度」这一件事，且必须由我们做 manifest 对齐。**

三条判定：

1. **能力是真的，不是自述。** run 2 在它的云 sandbox 里用 `/usr/local/bin/run_alphafold3`（AF3 权重 `af3.bin.zst` 973 MiB + HMMER 全套）真跑完一个蛋白-配体复合物，输出原生 AF3 产物（`*_model.cif` / `*_summary_confidences.json` / `*_ranking_scores.csv`），日志有 `Fold job minimal_atp_binding done`、GPU 0 显存 38023/97871 MiB、推理 67.72 s。
2. **产物与 stage3 af3 适配器 schema 天然对齐。** 真实产物喂进 `stage3_agent/engines/af3.py`，`confidence_score=0.94 / confidence_type=ranking_score / affinity=null / geometry_source=af3_confidence`，管线 `run` 退出 0、`validate` 通过 10 个产物、决策强制 `needs_review`（decision_codes 含 `af3_confidence_only`）。见 §五。
3. **但它不是 API 级 AF3 服务。** 公开 API 245 个端点里没有任何结构预测端点（`alphafold`/`af3`/`iptm`/`protein` 命中 0），公开文档 13 章也没提 AF3；每次 AF3 都必须通过**一次 agent run（LLM + shell）**驱动。因此对接方式是「agent 当执行器 + 我们做落盘与 manifest 转换」，不是「调接口拿结构」。

**建议路线（路线 A，最小改动）**：stage3 侧完全不动（`engine.name="af3"` + `engine.options.manifest`）；SciencePal 侧固定提示词契约，把 AF3 产物拉到本地后跑 `af3_to_stage3_manifest.py` 生成 manifest。详见 §六。

**必须提前告知下游的两条硬限制**：

- 沙箱**没有 630 GB 序列数据库**，只能 `--run_data_pipeline=false` + 单序列 MSA。科学后果：ipTM/pTM 会被显著压低（toy 案例 ipTM 0.41、pTM 0.56），只能当结构补全与对照，不能当结合证据。这与 stage3 现有设计（affinity 恒空、强制 needs_review）一致，但**不能**用来排序候选。
- 沙箱宿主内存上限 **12288 MB（可用约 9736 MB）**、磁盘 30 GB、GPU 与其他人共用（起步时 GPU0 已被占 37923 MiB）。真实 JAK2（~290 aa）+ RXT 这种尺寸**首次上线必须盯着跑**，未验证。

---

## 二、成本记录

| 项 | run 1（问答） | run 2（最小 AF3 试跑） | 合计 |
|---|---|---|---|
| `POST /api/agent/initiate` | 1 次，HTTP 200 / 5.63 s | 1 次，HTTP 200 / 5.43 s | **2 次（上限）** |
| 重试 | 0 | 0 | **0** |
| run id | `5a88c689-171b-4184-856b-f1248a14e2fc` | `af5d997c-0a52-4c4f-acdd-cd3494917fb2` | — |
| thread id | `28f080a0-efad-434e-ab39-edc8c663b906` | `39b21f72-da85-4ec4-95ce-c37ffc62f3eb` | — |
| 终态 | `completed`（52.3 s 轮询到） | `completed`（357.2 s 轮询到） | — |
| tokens_used（平台计费口径） | 115,426 | 1,355,131 | **1,470,557** |
| token_budget | 36,000,000 | 36,000,000 | — |
| 模型 | `platform::codex/gpt-6.1-sol-high`（实际应答 deepseek-v4-flash） | 同 | — |
| sandbox | 未查询（问答 run 不需要文件） | `28c3f1bb-1ba5-4c6e-8717-6c53c595b072` | 2 个 sandbox（每个 run 1 个） |

只读部分（§三）**0 credit**。`GET` 类调用不产生 run。

---

## 三、证据 A：只读摸底（0 credit，全部真实 HTTP）

| 调用 | HTTP | 耗时 | 结论 |
|---|---|---|---|
| `GET /api/agent-catalog` | **200** | 0.54 s | 见下方原文 |
| `GET /api/agent-catalog?usable_only=true` | **200** | 1.23 s | 同 6 个内置 agent |
| `GET /api/agent-catalog/{agent_id}` | 404 | 0.15 s | 无单条详情端点 |
| `GET /api/agent-run/{prev_run}/skills` | **403** | 0.90 s | key 权限外 |
| `GET /api/mcp/servers` | **403** | 0.15 s | key 权限外 |
| `GET /api/skills/catalog`（含 `agent_type=biology` / `query=alphafold` / `query=protein` 4 个变体） | **403** ×4 | 0.15–0.65 s | key 权限外 |
| `GET /api/thread/{prev}/tool-calls` | **403** | 0.15 s | key 权限外 |
| `GET /api/sandbox/tool-policy/{thread}` | **403** | 0.16 s | key 权限外 |
| `GET /api/user/agent-library`、`/api/token-management/me/selectable-models` | **403** | 0.14–0.15 s | key 权限外 |
| `GET /api/use-statistics/agent-types` | **200** | 0.14 s | 14 种 agent 类型（含 antibody_agent，但 catalog 只放 6 个） |
| `GET /api/thread/{tid}/agent-runs` | **200** | 0.48 s | 拿到历史 run 的 tokens_used |
| `GET /api/skills/sciencepal/download` | **200** | 0.97 s | `application/zip` 28,153 B，11 个条目 |
| `GET https://sciencepal.ai/openapi.json`（站点根，非 /api） | **200** | 1.89 s | 362,252 B，245 个 path |

403 的统一报文：`{"detail": "API keys are limited to SDK catalogs, agent, run and sandbox endpoints"}` —— 与父卡结论一致，**拿不到任何工具清单，只能靠 run 内实测**。

### 3.1 Biology Agent 目录原文（`/api/agent-catalog`，HTTP 200）

```json
{"source":"builtin","agent_id":"10000000-0000-0000-0000-000000000002",
 "runtime_agent_type":"biology","slug":"biology_agent","input_prefix":"@biology_agent ",
 "name":"Biology Agent",
 "description":"计算生物学与生物信息学专家，支持基因组分析、NGS/RNA-seq、单细胞测序、AI生物分子建模",
 "tool_profile":"standard","editable":false,"manageable":false,"restricted":false,
 "allowed_subject_types":["all","domain","email"],"version":null,
 "updated_at":"2026-05-19T02:20:38.829699+00:00"}
```

→ 只有「AI生物分子建模」这一句泛称，**没有** AlphaFold/AF3/pdb/cif/ipTM 任何字样；`tool_profile=standard`，和 General/Material/MIMIC/Survey 完全一样（不是专门的建模 profile）。

### 3.2 OpenAPI 规格（245 path）里没有结构预测能力

- `alphafold`、`af3`、`af2`、`colabfold`、`esmfold`、`openfold`、`boltz`、`rfdiffusion`、`iptm`、`protein`、`docking`、`vina`、`lammps` 在整份 spec（path 名 + description + schema）中命中 **0**。
- `fold` 的 15 次命中全部来自 `/api/workspace/folders`、`/api/projects/{id}/workspace/folders`。
- 唯一的 `cif` 命中都是 `specific` 之类子串。
- 与 AF3 沾边的 path 一个都没有；最接近的是 `/api/sandbox/tool-policy/{thread_id}`、`/api/thread/{tid}/tool-calls`（都被 key 403）。

### 3.3 公开文档：13 章使用手册，全程不提结构预测

用真实浏览器抓 `https://sciencepal.ai/help`、`/help/agents`、`/help/sdk`（Next.js 客户端渲染，raw HTML 无正文，必须浏览器取 `innerText`）：

- `/help`（概览）：`领域智能体：生物、材料、综述、医疗、MIMIC 等预置专家`、`沙箱即计算机：每个任务在独立环境中运行，可执行命令、浏览网页、读写文件`。
- `/help/agents`（第 4 章）：只讲怎么点选/固定/分享 agent，无工具清单。
- `/help/sdk`（第 12 章）：讲 API key、`GET /api/agent-catalog?usable_only=true`、SDK 0.2.3 安装、SSE 事件（`示例输出结构化的 SSE 事件`）。
- 三页关键词命中：`alphafold=0, af3=0, protein=0, fold=0, pdb=0, cif=0, iptm=0, docking=0`；`结构` 仅 2 次，都是「结构化响应事件」这种无关用法。

**链接**：https://sciencepal.ai/help ｜ https://sciencepal.ai/help/agents ｜ https://sciencepal.ai/help/sdk

### 3.4 下载的 Skills 包里也没有 AF3 工具声明（附带一个安全问题）

`GET /api/skills/sciencepal/download` → zip 11 条目：`SKILL.md`、`whl/sciencepal_sdk-0.1.1-py3-none-any.whl`、`agents/openai.yaml`、`scripts/{start_agent_run,poll_and_download,download_sandbox_files,stop_agent_run,get_agent_run_status,poll_with_notify,common}.py`、`.env`。

- `SKILL.md` 原文只说：`Professional agent for research in material, plasma, biology, protein and patent et al.`、`includes general, biology, material, protein, plasma and patent agents` —— 提到 protein agent，但 `/use-statistics/agent-types` 与 catalog 里都没有 protein agent，属于文档与目录不一致。
- **安全发现（已处置）**：zip 里的 `.env` 含 `SCIENCEPAL_ACCESS_TOKEN=`，值与我们使用的 API key 逐字符相同（即用哪个 key 请求，服务端就把该 key 注入到下载包内）。本地解压后已确认命中并**整包删除**（`skills_pkg/`、`sciencepal_skills.zip` 已删），复检 key 明文命中 0。**建议：任何人别再直接解压这个包；如需要，先脱敏再落地。**

---

## 四、证据 B：问答 run（run 1 = `5a88c689…`，HTTP 200 / 5.63 s，completed）

提示词：4 问（能否跑 AF3 / 30 秒内快检贴原始输出 / 输入输出格式 / 缺什么），明确禁止长任务。**禁止推理，只做环境快检**。

### 4.1 回答正文（SSE 取回，原文，963 字）

> **1. 能否运行 AF3/结构预测:能。** Sandbox 具备 AF3 运行条件:GPU 为 4× NVIDIA H20(141GB 显存),`run_alphafold3` 包装脚本存在,模型权重 `/app/alphafold3/models/af3.bin.zst` 与 HMMER 工具(`/app/af3_hmmer/bin/`)均已就位。但按你的要求,本次未发起任何推理。
>
> **2. 快检命中结果(原样):** `boltz 2.2.1`(位于 /app/boltz)、`fair-esm 2.0.0`、`which af3 af2 colabfold_run esmfold`:**未命中**、GPU:`NVIDIA H20` × 4
>
> **3. 输入/输出格式(可用时):** 输入:JSON 序列文件,支持 protein / dna / rna / ligand(CCD 码或 SMILES);`--run_data_pipeline=false` 时需在 JSON 内提供 `unpairedMsa`/`pairedMsa`/`templates`。输出:`.cif` 结构文件、`_confidences.json`、`_summary_confidences.json`。置信度字段:含 `ranking_score`、`ipTM`、`pTM`、`chain_pair_iptm`。
>
> **4. 不可用情况说明:** 当前可用,无缺失项。唯一注意点:若走完整数据管线需约 630GB 序列数据库(需挂载 `--db_dir`);本沙箱磁盘有限,建议用 `--run_data_pipeline=false` 推理模式。

### 4.2 底层 tool 调用原文（不是 LLM 的转述，是 sandbox 真实 stdout）

```
execute_command (3,099 ms)
  pip list 2>/dev/null | grep -iE "alphafold|af3|colabfold|esm|boltz|chai|fold";
  echo "---WHICH---"; which af3 af2 colabfold_run esmfold 2>/dev/null; echo "---GPU---"; nvidia-smi -L
→ boltz                                    2.2.1           /app/boltz
  fair-esm                                 2.0.0
  ---WHICH---
  ---GPU---
  GPU 0: NVIDIA H20 (UUID: GPU-64f84be9-…)
  GPU 1: NVIDIA H20 (UUID: GPU-6160c9af-…)
  GPU 2: NVIDIA H20 (UUID: GPU-690336ee-…)
  GPU 3: NVIDIA H20 (UUID: GPU-7be78776-…)

execute_command (1,658 ms)
  which run_alphafold3 2>/dev/null; ls /app/alphafold3/models/ 2>/dev/null; echo "---";
  ls /app/af3_hmmer/bin/ 2>/dev/null | head -3
→ /usr/local/bin/run_alphafold3
  af3.bin.zst
  ---
  alimask
  easel
  esl-afetch
```

注意 `which af3/af2/colabfold_run/esmfold` 全部未命中 —— 说明**不存在命令行 `af3`**，入口是 `/usr/local/bin/run_alphafold3`（包装脚本，内容见 §5.1）。所以「用 `which af3` 判断有没有 AF3」会得到错误结论。

---

## 五、证据 C：最小 AF3 试跑（run 2 = `af5d997c…`，HTTP 200 / 5.43 s，completed 357.2 s）

### 5.1 入口脚本与权重（run 2 实测 `cat`）

```bash
$ cat /usr/local/bin/run_alphafold3
#!/bin/bash
export PATH="/app/af3_hmmer/bin:${PATH}"
cd /app/alphafold3
exec /opt/conda/bin/conda run --no-capture-output -n alphafold3 \
    python run_alphafold.py "$@"

$ ls -la /app/alphafold3/models/
-rw-r--r-- 1 root root 1020533706 Apr  9 2026 af3.bin.zst      # ≈973 MiB

$ ls /app/af3_hmmer/bin/     # 39 个 HMMER/Easel 可执行文件：hmmsearch/jackhmmer/nhmmer/…
```

资源（同一次 `nvidia-smi`/`free`/`df`）：

```
index, name,        memory.total, memory.used
0,     NVIDIA H20,  97871 MiB,    37923 MiB   ← 起步时已被占用 37 GB
1,     NVIDIA H20,  97871 MiB,    48424 MiB
2,     NVIDIA H20,  97871 MiB,    47878 MiB
3,     NVIDIA H20,  97871 MiB,    13977 MiB
overlay  30G  444K  30G  1% /workspace
宿主内存上限 12288 MB（可用 ~9736 MB）—— submit_job 的内存预估 12000 MB 因此被拒，降到 4096 MB 才放行
```

### 5.2 实际执行的 AF3 命令（`submit_job` 参数，原文）

```bash
cd /workspace/af3_job && CUDA_VISIBLE_DEVICES=0 XLA_PYTHON_CLIENT_PREALLOCATE=false \
  TF_FORCE_UNIFIED_MEMORY=true XLA_CLIENT_MEM_FRACTION=3.2 \
  XLA_FLAGS="--xla_gpu_enable_triton_gemm=false" \
  /usr/local/bin/run_alphafold3 \
    --json_path=/workspace/af3_job/input.json \
    --model_dir=/app/alphafold3/models \
    --output_dir=/workspace/af3_job/out \
    --run_data_pipeline=false \
    --flash_attention_implementation=triton
```

输入（修好后的最终版，`minimal_atp_binding`，24 残基肽 + ATP）：

```json
{"name":"minimal_atp_binding",
 "sequences":[
   {"protein":{"id":["A"],"sequence":"MKTAYIAKQRQISFVKSHFSRQ",
               "unpairedMsa":">A\nMKTAYIAKQRQISFVKSHFSRQ","pairedMsa":"","templates":[]}},
   {"ligand":{"id":["B"],"ccdCodes":["ATP"]}}],
 "modelSeeds":[1],"dialect":"alphafold3","version":1}
```

**踩坑（对下一次很重要）**：第一次提交用的是 `{"ligand":{"ccd":"ATP"}}`，AF3 直接报 `Unexpected JSON keys in: ccd`；正确键是 **`ccdCodes`**（源码 `folding_input.py::Ligand.from_dict` 的 `_validate_keys(..., {'id','ccdCodes','smiles','description'})`）。agent 在同一个 run 内自查源码修好，没额外花钱。

### 5.3 运行日志（`run.log` 原始片段，71442 B）

```
Unable to initialize backend 'tpu': ... libtpu.so: cannot open shared object file   ← 无害（无 TPU）
Running AlphaFold 3. ... WEIGHTS_TERMS_OF_USE.md ...
Found local devices: [CudaDevice(id=0)], using device 0: cuda:0
Building model from scratch...
Checking that model parameters can be loaded...
Running fold job minimal_atp_binding...
Output will be written in /workspace/af3_job/out/minimal_atp_binding
Skipping data pipeline...                                    ← --run_data_pipeline=false
Writing model input JSON to .../minimal_atp_binding_data.json
Predicting 3D structure for minimal_atp_binding with 1 seed(s)...
pipeline.py:266] Calculating bucket size for input with 53 tokens.   ← 53 tokens
pipeline.py:272] Got bucket size 256 for input with 53 tokens, resulting in 203 padded tokens.
Featurising data with 1 seed(s) took 6.75 seconds.
Running model inference with seed 1 took 67.72 seconds.
Running model inference and extracting output structures with 1 seed(s) took 67.74 seconds.
Fold job minimal_atp_binding done, output written to /workspace/af3_job/out/minimal_atp_binding
Done running 1 fold jobs.
```

GPU 0 收尾时 `38023 MiB / 97871 MiB, 100 %` util。conda 环境 `alphafold3`，python 3.12，tokamax autotuning cache miss on `nvidia_h20`（首次编译，正常）。

### 5.4 落盘产物（全部从 sandbox 经 `files/content` 拉回，字节数与原目录逐一致）

| 文件 | 字节 | sha256 | 说明 |
|---|---|---|---|
| `minimal_atp_binding_model.cif` | 26,417 | `253b42c50dbd4b2324933e4129c1f8c2b9728e6a6cff4f1a1f4f9a473d876fb4` | 真 AF3 mmCIF：`data_minimal_atp_binding`、187 条 `ATOM`、`HETATM` ATP 在 chain B、B-factor 列是 pLDDT（如 70.13） |
| `minimal_atp_binding_summary_confidences.json` | 330 | `83f959e8ba4bf5af6c9328cb4e91bd659f2cd2822cce7e8bcdf1ab35afa00b2b` | 见下 |
| `minimal_atp_binding_ranking_scores.csv` | 51 | `5351090cb117f197b73975574bb5a5aea7351ca834f782d83c9fb4ae8f2f2d95` | `seed,sample,ranking_score` / `1,0,0.9382528856447905`（**全精度**） |
| `minimal_atp_binding_data.json` | 516 | `b0462143843b4ef7f82daf28faebb17785925732ead967113d47df9243981688` | AF3 回写的规范化输入（version 4） |
| `run.log` | 71,442 | `456b69bf13dabb2954a21f289aceb7b4855ca6f875f4044cfcc916c935f1b9f5` | 上面 §5.3 的来源 |

`summary_confidences.json` 全文（330 B，未改一字节）：

```json
{"chain_iptm":[0.41,0.41],
 "chain_pair_iptm":[[0.26,0.41],[0.41,0.47]],
 "chain_pair_pae_min":[[0.76,4.4],[5.81,0.77]],
 "chain_ptm":[0.26,0.47],
 "fraction_disordered":1.0,
 "has_clash":0.0,
 "iptm":0.41,"ptm":0.56,"ranking_score":0.94}
```

**完整性验证**：agent 写到 `/workspace/af3_job/handoff/` 的 `model.cif` / `summary_confidences.json`，与我另外从 `/workspace/af3_job/out/minimal_atp_binding/` 原生名文件下载的两份，`cmp` **逐字节相同**（说明 handoff 没被改写）。handoff 目录 `list-files` 返回 4 项 110 / 26,417 / 71,442 / 330 B，与本地 sha256 对应。

**置信度读数怎么解读**：ipTM 0.41 很低，直接原因是**没有 MSA**（单序列 MSA + 无模板）。这正是「不能拿它排序」的量化证据，也说明正式使用时若能灌真 MSA，数值会不一样。

---

## 六、证据 D：stage3 `af3` 适配器兼容性（真产物 + 全管线 + validate）

测了两遍：一遍手写 manifest，一遍用新写的转换器生成 manifest。两次都是**同一个真实 AF3 cif + 同一个真实置信度**，夹具里的 `candidates.csv/receptors.csv` 仍是 stage3 自带 fixture（`c1`、`c2`、`ctrl` / `WT`、`M1`），所以只有 `c1@WT` 这一对是真实 AF3 证据，`c2/ctrl` 是夹具占位，**不要误读**。

### 6.1 手写 manifest（`compat_case`）

```
manifest: c1,WT,inputs/model_c1.cif,0.94,0.41,0.56,0.26;0.41;0.41;0.47,sciencepal_af3_run_af5d997c
$ python stage3.py run --config compat_case/case/config.json --quiet   → exit 0
run dir: tiny-20261031-002132
```

`artifacts/03_poses.json`（原样）：

```json
{"candidate_id":"c1","receptor_id":"WT","pose_index":1,"protocol":"af3_single_model",
 "protocol_role":"primary","engine":"af3","affinity_kcal_mol":null,"heavy_atoms":null,
 "ligand_efficiency":null,"centroid_distance_to_crystal_A":null,"site_overlap_fraction":null,
 "min_distance_to_Asp25_oxygens":null,"asp25_contacts_under_3.5A":null,
 "clash_pairs":null,"polar_contacts":null,
 "geometry_source":"af3_confidence","confidence_score":0.94,"confidence_type":"ranking_score"}
```

`artifacts/06_decide.json`：

```
c1  needs_review  af3_confidence_only;confidence_only;no_binding_evidence;reference_check_gap
c2  needs_review  同上（夹具占位，无 AF3 产物）
ctrl control_only
```

`python stage3.py validate` → `OK  10 outputs validated: reference_check, pose_metrics, receptor_matrix, candidates_ranked, decisions, uncertainties, candidates_ranked_jsonl, decisions_jsonl, pose_metrics_jsonl, report`

### 6.2 用转换器生成 manifest（`compat_case_helper`，推荐的对接路径）

新增 `<WORKDIR>\af3_to_stage3_manifest.py`：

```bash
python af3_to_stage3_manifest.py \
  --af3-out  <AF3 产物目录> \
  --map      af3_pair_map.csv        # candidate_id,receptor_id,af3_name
  --out      <case>/inputs/af3_manifest.csv \
  --copy-to  <case>/inputs
```

实测输出：

```
wrote …/compat_case_helper/inputs/af3_manifest.csv (1 rows)
   c1 WT score=0.94 iptm=0.41 ptm=0.56 -> …/inputs/c1__WT__af3.cif
manifest: c1,WT,<abs>/inputs/c1__WT__af3.cif,0.94,0.41,0.56,0.26;0.41;0.41;0.47,af3:minimal_atp_binding_model.cif
$ python stage3.py run --config …/compat_case_helper/case/config.json --quiet  → exit 0   (run tiny-20261031-002352)
decision: c1 needs_review | af3_confidence_only;confidence_only;no_binding_evidence;reference_check_gap
$ python stage3.py validate … → exit 0   OK  10 outputs validated
```

转换器已处理的三件事（对接时都要注意）：

1. **命名**：优先找 AF3 原生 `<name>_model.cif` / `<name>_summary_confidences.json`，找不到再退回 `model.cif` / `summary_confidences.json`（很多提示词契约会改名）。
2. **`chain_pair_iptm` 是 N×N 矩阵**，CSV 单格装不下，转换器展平成 `0.26;0.41;0.41;0.47`。适配器本身**不消费**这一列（只读 `ranking_score`/`iptm`/`ptm`），所以展平不破坏契约，纯记录用。
3. **`structure_path`**：`config.resolve()` 接受绝对路径，也接受相对 case 根（config.json 所在目录的父目录）的路径。转换器写绝对路径 + 同时拷贝 cif 到 `inputs/`，两种都能吃。

### 6.3 一个已知缺口：`.cif` 不做几何校验

`AF3Engine` 只在 `structure_path.suffix == ".pdb"` 时才 `structure.read_pdb_atoms()` 做一次粗冲突检查；`.cif` 直接跳过 → `clash_pairs=null`。也就是说 **AF3 路线的 clash 检查目前是空的**。要做的话得先 cif→pdb（gemmi/biopython），本次**未做**（见 §八）。

---

## 七、风险与限制（按影响排序）

1. **没有 MSA 数据管线**（要 630 GB 序列库，沙箱磁盘 30 GB）。只能单序列 MSA。ipTM 被压低到 0.41 是直接后果。→ 结论：只用于结构补全/对照，禁止参与亲和力或活性排序（与 stage3 现有 `allow_af3_only_advance=false` 一致）。
2. **成本模型是「一个 run 一个任务」，不是「一次调用一个结构」。** run 2 用 1 个 24 残基 toy 复合物就烧了 **1.36 M tokens / 6 分钟**。真实 panel（如 3 候选 × 2 受体 = 6 次 fold）应在**一个 run 内串行跑完**（AF3 单次推理 ~70 s，6 次约 7–10 分钟），否则 run 数直接爆。这条必须在下一张卡里写成硬约束。
3. **宿主内存 12288 MB（可用 9736 MB）**是最可能踩的坑。toy 案例没问题，JAK2（~290 aa）+ RXT 未验证；`submit_job` 有内存预估前置（12 GB 预估被拒过），真实体系可能直接被拒或 OOM。
4. **GPU 是共享的**（起步时 4 张卡分别已被占 13–48 GB），无 SLA、排队/抢占无保证；一次 fold 的耗时不可控。
5. **产物命名/路径取决于提示词**。必须固定提示词契约（像 run 2 那样写死 `/workspace/handoff/{model.cif, summary_confidences.json, run.log, manifest_row.json}`），否则下游解析会漂。转换器已经做了容错，但提示词仍然要钉死。
6. **`ranking_score` 在 summary_confidences.json 里被四舍五入**（0.94），全精度在 `*_ranking_scores.csv`（0.9382528856447905）。要精度就读 CSV。
7. **`candidate_id` / `receptor_id` 语义是我们自己的**，SciencePal 完全不知道。任何一次 AF3 都必须在我们这边维护 `candidate ↔ AF3 job name` 映射（转换器的 `--map`）。
8. **许可**：AF3 权重与产物受 Google `WEIGHTS_TERMS_OF_USE` / `OUTPUT_TERMS_OF_USE` 约束，产物目录里会自带 `TERMS_OF_USE.md`。生产使用前需要法务确认（我们只是在香港城大 HKAI-SCI 沙箱里跑，是否覆盖商用/对外服务，未知）。
9. **安全**：`/api/skills/sciencepal/download` 会把调用者的 API key 明文注入包内 `.env`。已删除本地副本，key 明文命中复检 0。任何人再下载都要先脱敏。
10. **备选工具存在但无适配器**：同沙箱还有 `boltz 2.2.1`（/app/boltz）、`fair-esm 2.0.0`，以及一个 `tooluniverse-protein-structure-prediction` skill（ESMFold / AlphaFold DB 检索 / RCSB / ProtVar / ProtParam）。它们能出结构，但置信度字段与 stage3 af3 契约不同，接进来需要新写适配器 —— 本次不做。

---

## 八、NOT_RUN（如实列出）

- **未**在真实 JAK2 / 6VGL / RXT 体系上跑 AF3（成本与时间都不允许，只用了 24 残基 toy 复合物验证通路）。
- **未**验证 12 GB 宿主内存对 ~290 aa 蛋白 + 大配体是否够。
- **未**尝试灌真实 MSA（沙箱无序列数据库、无 `--db_dir`），因此无法给出「有 MSA 时 ipTM 能到多少」。
- **未**做多候选批量（一个 run 内多次 fold）验证；只验证了单 fold。
- **未**做 cif→pdb 转换，因此 AF3 路线的 clash 几何校验仍是空（`clash_pairs=null`）。
- **未**验证 boltz / ESMFold / tooluniverse skill 的实际产物。
- **未**验证其他 agent（antibody_agent 等 14 类）是否同样具备 AF3。
- **未**清理 sandbox 内文件（`28c3f1bb…` 里 `/workspace/af3_job` 仍在）。
- **未**做 `/api/skills/catalog`、`/api/mcp/servers`、`/api/thread/*/tool-calls` 的工具清单枚举（key 权限 403，只能用 run 内实测替代）。
- **未**跑 JAK2 管线（按卡片要求）。

---

## 九、产出文件清单

运行目录（证据 + 脚本 + 脱敏日志）：
`<WORKDIR>\runs\t_ef07cf4c_sciencepal_af3_probe_20261031-001019\`

| 路径 | 内容 |
|---|---|
| `af3_out/native/` | **真实 AF3 产物**：`minimal_atp_binding_{model.cif,summary_confidences.json,ranking_scores.csv,data.json}` |
| `af3_out/{model.cif,summary_confidences.json,run.log,manifest_row.json}` | 从 `/workspace/af3_job/handoff/` 拉回的同一批文件（与 native 逐字节相同） |
| `probe_readonly.json`、`probe_catalog2.json`、`probe_skills.json` | 只读摸底的全部 HTTP 响应（key 脱敏） |
| `openapi.json`、`openapi_paths.txt`、`openapi_interesting_paths.txt`、`analyze_spec.py` | 362 KB OpenAPI 规格 + 245 path 清单 + 关键词分析 |
| `help_agents.txt`、`help_sdk.txt`、`doc_*.txt` | 公开文档正文（浏览器 innerText 抓取） |
| `run1_*`、`run2_*`（initiate/poll/text/sandbox/download） | 两次 run 的完整调用记录与 SSE 正文 |
| `run1_tool_io.json`、`run2_tool_io.json` | 两次 run 的**逐条 tool 调用原文**（命令 + stdout + 审计耗时） |
| `run_costs.json` | tokens_used / token_budget 平台口径 |
| `compat_case/`、`compat_case_helper/` | 两个 stage3 兼容性用例（config + 真 cif + manifest + runs + outputs） |
| `check_env_leak.py`、`cleanup_secret.py` | .env 泄密核查与清理（只输出布尔/长度，不打印密钥） |
| `skills_zip_entries.txt`、`skills_zip_kw.json` | 下载包条目与关键词扫描（包体已删除） |

新增/复用的可交付脚本：

- `<WORKDIR>\af3_to_stage3_manifest.py`（**新增**，AF3 产物 → stage-3 af3 manifest，已实测）
- `<WORKDIR>\sciencepal_bridge.py`（复用父卡，initiate/poll/text/sandbox/list-files/read-content）

密钥卫生：对运行目录 + `logs/` + lock 文件做 `grep -F -f D:\AI4S\API.txt`，**明文命中 0**（下载包内的 `.env` 已删除）。全程未打印 key（只打印 `key_len=51`）。
