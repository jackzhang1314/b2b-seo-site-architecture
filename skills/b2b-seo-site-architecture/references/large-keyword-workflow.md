# 万级词库：代码统计、语义判断与竞品页面证据

用户提供原始Excel/CSV、现成词库和排名URL表即可；下面的映射、命令和中间文件由Codex处理。先读表头与实际来源，不要求用户清洗或手写JSON。Excel/PDF使用当前可用的文件工具读取，保留原件并核对转换行数；内置脚本直接读取CSV/TSV（含常见UTF-8/UTF-16导出），不自称直接解析所有文件格式。

## 1. 原表全量接收，保存关系

内置`library.py`将同市场/语言的相同词去重，保留每一条输入来源、原始字段和各来源指标，不把跨竞品的重复出现算成需求频次。已有规范词库直接复用ID；不要为分批重新编号。

```sh
python3 SKILL_DIR/scripts/library.py build --manifest sources.json --out runs/library-01
```

Codex根据实际字段创建`sources.json`，每个文件一个来源。例如：

```json
{"sources":[{
  "source_id":"seed_us", "path":"original.csv", "provider":"semrush",
  "report":"实际报告名称", "observed_at":"实际数据日期",
  "evidence_class":"user_provided", "discovery_type":"user_supplied",
  "context":{"market":"US","language":"en"},
  "columns":{"keyword":"Keyword","volume":"Volume"}
}]}
```

示例上下文不是默认事实；国家/语言未知就保持未知。`columns`支持keyword、market、language及volume/difficulty/cpc/ads_competition/clicks/impressions，其他原列完整保留到`raw-records.jsonl`。排名表也作为词源导入，把种子表漏掉的词纳入；URL、Position、逐词Traffic和页面流量由下面的独立证据流程读取，不把Traffic映射为Volume。声明为模型候选时用`generated_candidate`且不能带假指标。

`source_id`在同一清单中唯一，只用ASCII字母、数字、下划线和连字符（如`seed_us_01`），不含点号或空格。教学MOCK统一设`evidence_class=simulation`，`discovery_type=user_supplied`，并保留实际provider标签如`MOCK`和原始Evidence列；后面的排名来源、页面流量来源及计划的`source.kind`也保持`simulation`，不用改成user_provided。真实工具导出可统一用`provider_export`；词库导入继续兼容旧值provider_data/first_party/direct_observation，但这些旧值不能直接冒充排名来源枚举。关键词的来源性质完整保存在lineage、source-register和指标观察表。

目录内有`keywords.csv`、`lineage.csv`、`keyword-observations.csv`、`raw-records.jsonl`、`source-register.json`和`run.json`。同名XLSX用于人工阅读，单元格写成文本；CSV供程序继续处理。原始空值、N/A、真实0、格式错误分开保存。数字小数/千位口径不明先核对，可显式设置`decimal_separator/thousands_separator`；K、M、百分比不擅自猜换算。

## 2. 全库统计，逐个维度下钻

```sh
python3 SKILL_DIR/scripts/cluster.py frequency --keywords runs/library-01/keywords.csv --ngram 3 --min-df 1 --out runs/frequency-01
python3 SKILL_DIR/scripts/keyword_evidence.py drilldown --keywords runs/library-01/keywords.csv --term ton --out runs/ton-01
```

frequency统计连续1–3词：TF为出现总次数，DF为包含它的不同关键词数，分母按市场/语言分开。重复词可令TF大于DF，二者都不是搜索量；词频高也不是独立建页的充分条件。默认不做词干替换，不删掉停用词后拼出不存在的相邻词组。`--min-df`仅过滤统计展示，不删除原词；若使用`--stopwords`或`--facets`显式词典，记录其内容和理由。

drilldown回查全部匹配原词，输出`term-members.csv`、`term-variants.csv`和`cooccurring-terms.csv`。例如看到`ton`后，可以查1 ton/2 ton/3 ton各自覆盖多少原词、与supplier/landscaping等怎样共现。`2-ton`、`2ton`这类数字连写单独标为拼写线索；不自动与`2t`、`2000 kg`或不同吨制换算合并。

据此形成产品实体、规格、材料、工艺、用途、供应方式和信息需求等候选维度，再关联到独立类目、页面章节、筛选属性、详情字段或暂缓。词频只是证据视图，属性用途按[属性规范](language-hierarchy-and-attributes.md)判断。

## 3. 保留竞品全部收到的排名页与博客页

```sh
python3 SKILL_DIR/scripts/keyword_evidence.py rankings --keywords runs/library-01/keywords.csv --manifest ranking-sources.json --out runs/urls-01
```

`ranking-sources.json`可同时包含排名表和Top Pages等页面流量表：

```json
{"sources":[
  {"source_id":"competitor_rank", "kind":"rankings", "path":"rankings.csv",
   "provider":"ahrefs", "competitor_id":"C01", "domain":"competitor.example",
   "market":"US", "language":"en", "observed_at":"实际数据日期", "evidence_class":"simulation",
   "columns":{"keyword":"Keyword","position":"Position","url":"URL","estimated_traffic":"Traffic"}},
  {"source_id":"competitor_pages", "kind":"pages", "path":"top-pages.csv",
   "provider":"ahrefs", "competitor_id":"C01", "domain":"competitor.example",
   "market":"US", "language":"en", "observed_at":"实际数据日期", "evidence_class":"simulation",
   "columns":{"url":"URL","page_estimated_traffic":"Traffic"}}
]}
```

上面用`.example`明确表示示例；真实导出改成真实域名、来源和日期，证据可为provider_export/user_provided/historical_sample。`rankings`与`pages`各自独立保存Traffic，不能复制整页流量到每个关键词。来源缺必要上下文时先登记待核，不为让脚本运行编造日期/语言。单文件多个市场/竞品时由Codex按原列拆分并保留行映射。

输出保留全部收到的排名位置，额外计算Top30/Top10数量，不截掉31名以后或博客记录。收到全量有流量页面时全部登记；输入只导出1–30就只代表这个范围，不叫全市场全量。即使Top Pages中的某URL没有对应关键词明细，也保留它和“词明细缺失”的事实。

- `ranking-observations.csv`：逐条排名关系与原排名/流量；重复行也保留追溯。
- `page-traffic-observations.csv`：独立页面流量观察。
- `competitor-url-summary.csv`：同工具、竞品、国家、语言、数据日期、原URL聚合；去重关键词数、前30/前10词数、最佳位置、收到范围内逐词估算、独立整页估算、样本词。
- `keyword-url-memberships.csv`：完整词—URL组对应，可作为语义审阅的页面证据。
- `input-issues.csv`：缺/异常URL、未知词、缺/异常排名或指标；所有原行保留在raw-records.jsonl。未知词先回到词库合并，不静默丢弃。

不同来源的重复词不重复加流量；同一快照有冲突则标conflicting，缺失则留空而非0。模拟与真实证据即使provider和日期相同也分组，不相加。summary中的关键词计数与最佳位置只基于已关联到词库的词，unknown_keyword_rows单独报告。原URL不因为跳转而被覆盖；最终访问URL在浏览观察记录中另记。

URL聚合只说明竞争对手用一个页面承接了哪些词，不直接证明本站应该同页。不能凭`/blog/`路径自动宣称已经识别或访问文章页：按业务相关性、排名词和页面流量安排研究，再用浏览器确认商业页/文章页的任务、模块与模板。商业页用于核心架构，文章页用于独立内容计划，两者都保留来源。

## 4. 让模型判断含义，代码核对覆盖

先读统计与URL组，建立可复用的实体/采购任务定义，再审阅重要候选、代表词及歧义。大词库按产品线、属性或已有页面证据分批；可复用已明确的分配规则，但保存规则、覆盖成员和例外。不能因为一个词包含`how`就直接判为低价值，或因为共用`excavator`就把租赁、采购和维修全部并到一页。

```sh
python3 SKILL_DIR/scripts/cluster.py semantic-prepare --keywords selected-keywords.csv --batch-size 200 --out runs/semantic-input-01
python3 SKILL_DIR/scripts/cluster.py semantic-check --keywords selected-keywords.csv --assignments proposals.json --out runs/semantic-01
```

prepare只分批，不调用模型、不生成语义组。selected可以是全库，也可以是有明确筛选规则的当前产品线；其余词仍在完整库中。proposals由Codex判断后填写：

```json
{"method":"Codex采购任务语义判断", "model":"实际可见版本或not exposed",
 "clusters":[{"cluster_id":"C01","label":"Mini Excavator Purchasing","buyer_task":"选型采购",
   "intent":"commercial","entity_scope":"实际小型挖掘机产品范围","constraints":"具体型号与吨位差异另核",
   "rationale":"采购任务一致，但不自动视为同页"}],
 "memberships":[{"keyword_id":"实际ID","cluster_id":"C01","assignment_reason":"具体实体与任务依据","review_state":"assigned"}]}
```

每个当前输入词恰好一个主关系；歧义用review、离群用outlier，未判断不伪装assigned。跨批统一簇定义后再核验，避免一批一个重复主题。semantic-check检查漏词、重复、未知ID、空簇与混市场，不证明语义判断正确。最终逐词去向可以是page/section/filter/defer/exclude，暂缓说明原因和剩余覆盖。

## 5. 官方分组作为独立参照

有真实导出或授权页面证据时，用`cluster.py provider --keywords ... --manifest topic-sources.json --out ...`保留原生主题。每个来源含`source_id,path,provider,source_verified,provenance_ref,observed_at,context,columns,levels,hierarchy_explicit`；provider为ahrefs/semrush/unverified；columns映射keyword及可得market/language；levels每项为`level,field`，可另含id_field/type_field。只有明确的层级才设hierarchy_explicit=true，缺失值保留not_returned/missing，不让模型补原生标签。

Ahrefs有按Terms查修饰词和Parent Topic的不同视图；Semrush有分组、子组及按关键词数量/搜索量查看的方式。它们可帮助发现维度；本地TF/DF是透明的自研统计，不声称复刻供应商内部算法。来源：[Ahrefs官方示例](https://ahrefs.com/academy/how-to-use-ahrefs/keywords-explorer/matching-terms)、[Semrush官方分组说明](https://www.semrush.com/kb/617-keyword-magic-tool-manual)。

## 交付与规模边界

完整记录留在CSV/XLSX/JSON，HTML按需查看核心架构和摘要；明确预览行数与全量数量，不能让HTML一次铺几万行。新输出目录保留旧运行，来源登记含哈希和方法。分别报告接收/统计完成、语义已审阅/待核、竞品实访、架构已定/暂定，不能用运行通过替代真实研究。规模测试只说明该版本在所测环境的数据处理能力，不证明真实语义质量、排名或询盘效果。

最终生成时用`architecture.py --analysis-dir runs`附带本轮分析；runs只放本轮选定且来自同一完整词库的输出，不能指向混有历史版本的总目录。原始完整分析随成果复制，整页流量和仅有流量的URL进入HTML及根目录导出；逐词Top30规划摘要仍独立保留，具体文件和检查项见[输出契约](output-contract.md)。
