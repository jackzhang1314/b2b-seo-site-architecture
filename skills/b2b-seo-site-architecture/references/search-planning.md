# 从原始导出到逐页规划

客户交企业与产品资料、目标市场和语言、种子词拓展表、竞品 Top Pages、逐词排名表即可。Codex 先读原表表头和一小段数据，把各列含义与来源日期记入清单。CSV/TSV 可直接读，Excel 等格式先在当前环境转换并保留工作表及原行关系。原表清洗、字段映射、脚本调用、语义决定及生成计划由 Codex 完成。

## 先核计数口径

- 复用 [大词库工作流](large-keyword-workflow.md) 的 build、frequency、drilldown、rankings。TF 是词或连续词组出现的次数，DF 是包含它的不同查询数。去重键为规范查询、市场、语言。不同供应商、日期和 URL 的排名观察全部保留。
- 原词、语义猜测及模拟来源分列。缺指标保留空值，已观测的零有效。量与 KD 在连接竞品排名之前按每个查询选取；有重复导出时一份需求只计一次。选择精确到工具、数据库范围、市场、语言、日期、证据性质和指标定义。默认每个市场、语言、指标只有一套已知口径时自动采用。多套并存时写清本次采用哪套，原始观察均保留。同口径值有冲突时留空待核。
- 例如计划可指定 `"metric_basis":{"volume":{"provider":"Semrush","observed_at":"2026-10-01","evidence_class":"provider_export","unit":"searches/month","scope":"US database","metric_type":"monthly"}}`。字段值应与词库 build 清单的实际定义一致，未登记的 unit/scope/metric_type 用空值。KD 用 `difficulty` 单独选取。每个维度和具体值独立按成员求并集，展示已知月量、有量数/总数、KD 有值数及 <30、30–<60、60–100、未知四档词数。这些区间用于展示。
- 工具若明确多个近似词共享一个月量，可把原表的共享估值编号映射至 build 的 `columns.volume_group`，仅在同一口径计一次；跨页面或属性的合计同样可能重叠。不同市场分别汇总。按查询数量、已知搜索量以及部分承接、待核和排除分别读覆盖统计。

## 实页与价格

排名来源清单的 `columns` 可以映射每行的 provider、competitor_id、domain、market、language 和 observed_at。单一来源继续用清单顶层的固定值。词库 build 也支持 market、language、provider 和 observed_at 按行映射。若同列在不同工具表示不同含义，按实际定义分别映射。

Top Pages 的 kind 为 `pages`，映射 url 和 page_estimated_traffic。有商品价格时可追加 price（报价原文或需询价）、price_type、currency、unit、minimum_quantity、configuration、delivery_basis、destination、tax_freight、valid_until。单独的报价表可用 `kind:"prices"` 加 url 和 price。每条保留来源行、URL、市场与日期。只有整页流量的 URL 可实访，排名词留空。逐词明细可从该 URL 的各次观察展开；报价、逐词流量、整页流量和搜索量均各用各的口径。商品标价证据只用于解释竞争者的报价条件。客户售价和交付承诺取自客户资料。

## 编排 0.5.0 计划

沿用页面、采购任务组、模板及模块编号。以下是 Codex 整理计划时填写的内容，最小全例见 [minimal-plan.json](../examples/minimal-plan.json)。

| 位置 | 编排内容 |
| --- | --- |
| 顶层 dimension_values | 每个具体取值有 id、dimension、value、definition 和完整成员。可给 keyword_ids，或给 market、language、aliases，也可两种合用。exclude_keyword_ids 记录审阅后的歧义例外。可指定一个成员作为 representative_keyword_id。全库只有字面证据时先保留候选，确认语境后写入此表。 |
| 每页 search_scope | action 为 new、reuse、revise，approval 为 proposed 或 confirmed。给 priority_reason、boundary、adjacent_page_ids。peer_reviews 中每条有 review_id、reason，说明已实访的同类页如何帮助决定。price_refs 可指定本次排名/价格分析的 source_ref。待确认的归属如实保留。 |
| 每页 design_brief.module_notes | 核心页每个实际启用的模块写 module_id、buyer_question、content_note 与 required_facts 数组。无词的信任、导航或询价模块同样说明作用。 |
| 每条 decisions | 每个原词恰好一行，沿用 keyword_id、group_id、page_id、disposition、reason、evidence_ids。页／章节／筛选的词绑定该页启用的 module_id，coverage 取 full 或 partial。部分承接增加 remaining_need。defer 取 pending，exclude 取 out_of_scope，两者目标页和模块均留空。 |
| 分组依据 | 产品、方案、供货和场景先确认业务边界。同一词可属于多个维度，仅有一个主承接页。可比参考需解释购买任务、产品范围、实访页型及为何适用。缺证据的拆页保留疑点。 |

糖浆词库可写 `{"id":"V_MANGO","dimension":"口味","value":"芒果","definition":"表达芒果味糖浆采购","market":"US","language":"en","aliases":["mango"]}`。草莓是同一维度的另一个值。挖掘机的 `["2 ton","2-ton"]` 能匹配目标市场查询中前后不同位置的完整词组，原拼写保留。2t 和 2000 kg 的等价性需核对本企业目录及吨制。别名匹配完整词元，不同词序的同义采购任务由审阅者确认。

买家询问跨系列价格、2 吨配置价格、某一型号报价时，分别考察总目录、系列和型号页的报价模块。预算集合页需要持续可供应的产品、可比价格和独特选型依据。若两种铝合金 CNC 说法都指向同一按图加工任务，则共用服务页。在理由中写明拆页新增的采购答案，合页时指出承接模块。

## 查看 HTML 和 CSV

使用 [架构生成命令](output-contract.md)，加 `--analysis-dir` 附上同批 build、rankings 等完整分析。index.html 是统一工作台，能离线查看整站父子层级、全部原句、意图、词频、原始文件、竞品及商品报价。design.html 提供导航、模板和详细设计简报。把整个输出目录一起交付。每页简报链接能定位到对应页。客户可按属性查看不同查询数与指标，再从拆并表展开具体成员和原排名行。

| 表 | 粒度和用途 |
| --- | --- |
| pages.csv | 每个核心 URL 一行，含市场、语言、主辅词、相邻页边界、采用的同类 URL、价格原行和模块编号。文章计划按约定另列。 |
| keyword-decisions.csv | 每个查询一行，可读目标 URL、角色、组、模块、覆盖程度、剩余需求、原行、指标口径和排名引用。 |
| commercial-planning.csv | 每个取值 × 采购任务 × 承接方式与去向一行。按 planning_id 与逐词表的 planning_ids 取全部成员；按 value_id 可查看跨意图的该值。 |
| dimension-values.csv、demand-coverage.csv | 各取值与维度并集的指标，以及按市场、语言、来源状态和覆盖状态分组的统计。重叠属性或共享估值需按成员重算并集。 |
| content-modules.csv | 用 page_id 与 module_id 一起定位页面模块，查看买家问题、所需资料和全部关键词 ID。 |
| comparable-pages.csv、received-rankings.csv、price-observations.csv | 已选同类页和理由、收到的逐条排名、商品价格条件。compare 与 ranked 是两种不同依据。附有分析时，competitor-url-summary.csv 还保留只有整页流量的快照。 |

On-page 用确认的 page_id／URL 找页，读取对应全部词、主辅角色、模块及剩余问题，查看相邻页边界和可比页原行，再写 Title、H1、Subtitle、H2 和正文。页面尚属 proposed 时，可制作待确认的草稿。旧版计划只展开已有数据，客户补充过的决定才能标为已确认。


### 随工作台交付客户提供的原件

build 与 rankings 会保存它们实际读取的原件及 SHA-256。工作台把去重前的原行、原文件和清理后词库分别展示。词源行与排名行可以点回具体文件和数据记录号。同一原表用于不同分析时保留各次接收记录。只有旧分析输出时，可按已保留的原行核对，缺少的文件标明无法下载。

另有客户交来的竞品名单、产品表、Excel 原件或独立 Top Pages 导出时，Codex 生成 `materials.json`，为生成命令加 `--materials materials.json`。示例如下。path 相对于清单，kind 可用 competitors、catalogue、pages、rankings、material。原始工作簿保留工作表和格式，可下载打开。CSV/TSV 支持离线逐行浏览，超过一百行的原始记录按需从同一交付目录加载。发给客户时打包整个目录。
```json
{"files":[{"path":"competitors.csv","label":"客户提供的竞品名单","kind":"competitors"},{"path":"product-catalogue.xlsx","label":"产品与规格原件","kind":"catalogue"}]}
```
`clean-keywords.csv` 是未改写 ID 的清理后全库，`words-and-phrases.csv` 包含全库连续一至三词的 TF/DF，`intent-groups.csv` 保留整句所属采购任务、代表原句和承接页。词频表按市场与语言分别计数，可在工作台点击任一词组回读全部匹配原句。`input-files.csv` 保存接收记录、原件相对路径及文件摘要。
