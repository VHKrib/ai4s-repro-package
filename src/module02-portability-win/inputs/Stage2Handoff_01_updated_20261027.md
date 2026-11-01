# Stage 01 → Stage 02 交接:JAK1/JAK2 双靶炎症抑制剂

- **eligibility**:`ready`
- **生成说明**:仅填写本轮真实取得的信息;未知项已列入缺口。所有数据来自 UniProt、RCSB PDB 与文献检索,未编造 SMILES、残基或 PDB ID。

## 1. 最终确认的目标蛋白

- **名称**:JAK1(Tyrosine-protein kinase JAK1)与 JAK2(Tyrosine-protein kinase JAK2)双靶
- **物种**:人(Homo sapiens)
- **UniProt ID / 序列**:
  - JAK1:P23458(JAK1_HUMAN),1154 aa,序列见 `stage01_output/JAK1_P23458.fasta`
  - JAK2:O60674(JAK2_HUMAN),1132 aa,序列见 `stage01_output/JAK2_O60674.fasta`
- **突变**:unknown(未指定,默认野生型 WT,未做突变假设)
- **结构域或区域**:JH1 激酶催化域(ATP 结合口袋/铰链区),与所选实验结构一致
- **用户是否确认**:是(用户选定 JAK1/JAK2 双靶,人源)

## 2. 最终选定的结构

- **主计算结构(用户已确认)**:
  - JAK1:`stage01_output/structures/3EYG.cif`(野生型,1.90 Å,含托法替尼,文献引用最多)
  - JAK2:`stage01_output/structures/6VGL.cif`(野生型,1.90 Å,含鲁索替尼,无突变)
- **次级/参考结构**:
  - `stage01_output/structures/6AAH.cif`(JAK1-培非替尼,1.83 Å,野生型)——JAK1 交叉验证/参考
  - `stage01_output/structures/6AAJ.cif`(JAK2-培非替尼,2.37 Å,**含 K943A/K945A 工程突变**)——仅作参考,不作主计算对象
- **来源编号**:RCSB PDB 3EYG、6VGL、6AAH、6AAJ
- **类型**:实验(X 射线晶体结构)
- **链**:各结构链 A(JAK1 或 JAK2 JH1 激酶域)
- **若为 AF3**:不适用(未使用预测结构)
- **若为 AFDB**:不适用(均为实验结构)
- **置信度说明**:实验 X 射线结构,分辨率 3EYG 1.90 Å、6VGL 1.90 Å、6AAH 1.83 Å、6AAJ 2.37 Å,均为高分辨率实验结构
- **6AAJ 突变说明**:6AAJ 含 K943A、K945A 两个工程突变(已在 CIF 中核实 `_entity.pdbx_mutation 'K943A, K945A'`),偏离野生型 JAK2,故仅作参考,不作主计算对象。

## 3. 结合位点依据

- **已知结合位点**:JAK1/JAK2 JH1 激酶域的 ATP 结合口袋(铰链区 hinge region)
- **关键残基**:
  - JAK1:Glu957、Leu959(与抑制剂形成氢键);Glu966(选择性位点,对应 JAK2 Asp939)
  - JAK2:Glu930、Leu932(与抑制剂形成氢键);Asp939
- **对应文献/结构**:
  - 3EYG(JAK1-托法替尼):托法替尼与 JAK1 铰链区 Glu957/Leu959 形成氢键
  - 6VGL(JAK2-鲁索替尼):鲁索替尼与 JAK2 铰链区 Glu930/Leu932 形成氢键
  - 6AAH/6AAJ(JAK1/JAK2-培非替尼):培非替尼 1H-pyrrolo[2,3-b]pyridine-5-carboxamide 骨架与铰链区形成三重氢键(文献 BMC 2018,DOI 10.1016/j.bmc.2018.08.005)
  - JAK1 选择性设计:利用 Glu966(对应 JAK2 Asp939)设计 JAK1 选择性抑制剂
- **是否仅为计算预测**:否(结合位点为实验共晶结构确定,非计算预测)

## 4. 已知配体/抑制剂(如有)

| 名称 | SMILES 或结构文件 | 来源 |
|---|---|---|
| Tofacitinib (CP-690,550) | 3EYG.cif 共晶配体 MI1;C16H20N6O | PDB 3EYG;FDA 批准(RA);ChEMBL |
| Ruxolitinib | 6VGL.cif 共晶配体 RXT;(3R)-3-cyclopentyl-3-[4-(7H-pyrrolo[2,3-d]pyrimidin-4-yl)-1H-pyrazol-1-yl]propanenitrile | PDB 6VGL;FDA 批准;ChEMBL |
| Peficitinib (ASP015K) | 6AAH.cif / 6AAJ.cif 共晶配体 9T6;4-[[(1S,3R)-5-oxidanyl-2-adamantyl]amino]-1H-pyrrolo[2,3-b]pyridine-5-carboxamide | PDB 6AAH/6AAJ;文献 BMC 2018;ChEMBL |

## 缺口与阻断项(blocked 时必填)

- **eligibility.status = ready**,无阻断项。
- **设计策略(用户已确认)**:双靶 JAK1/JAK2(沿用托法替尼/巴瑞替尼思路)。Stage 02 需分别以 JAK1 3EYG 与 JAK2 6VGL 为受体进行对接/筛选,评估双靶活性。
- 缺口(非阻断,供 Stage 02 参考):
  1. 双靶设计:需在 Stage 02 分别以 JAK1 3EYG 与 JAK2 6VGL 为受体对接,评估候选分子对两靶的活性与选择性。
  2. 未指定突变:默认野生型(WT)。
  3. 未指定研究结构域:默认 JH1 激酶催化域(ATP 结合口袋),与所选结构一致。
