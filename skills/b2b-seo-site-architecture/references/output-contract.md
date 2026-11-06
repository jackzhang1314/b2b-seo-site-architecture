# 输出与校验

图、表、菜单和模板清单复用同一份 `architecture-plan.json`。它是Codex完成研究后的决定记录，用户不用填写JSON。原始表格、竞品观察和分析分别保留。

## 面向用户的交付包

先把成果解释成四项核心规划与两项配套材料，再提供文件。多个成果可以在同一个HTML入口中查看，不必凑成六个独立文档。

| 成果 | 用户实际得到什么 | 主要载体 |
| --- | --- | --- |
| 网站架构图 | 核心页、博客入口和必要分类的父子层级；不混入逐篇文章标题 | index.html、mindmap.md |
| 核心页面与独立文章计划 | 逐页/逐篇名称、类型、上级、建议URL、主辅词、职责、提纲、询价动作、优先级与资料缺口；核心页与文章分别查看 | index.html、pages.csv、content-plan.csv |
| 导航方案 | 主菜单、下拉、页脚和询价入口；区分菜单组与实际页面 | index.html、navigation.csv |
| 模板设计清单 | 需要设计哪些页型、哪些页共用；各模板的模块、字段、变体、代表页与设计顺序 | index.html、templates.csv |
| 研究依据与取舍 | 词频和类目来源、竞品URL排名/需求汇总与菜单/模块观察、逐词去向，以及合并、筛选、暂缓、排除理由 | ranked-url-summary.csv、HTML证据区、分析CSV、接收与实访记录 |
| 设计交接说明 | 规划范围、模板/样板安排、每页独有内容、待补资料及后续设计入口 | design-handoff.md |

交付时用实际数字说明“规划多少页、需要设计多少类模板/变体、先做哪些代表页”，不要把URL数量直接当作设计稿数量。由此可以核对网站建设范围和估算设计工作量，并把决定交给设计师或建站人员。结果是带依据的规划；资料待核节点、未访问竞品和未核排名继续明确标出。

原表接收/缺口/补数任务与竞品实访记录由Codex随项目保存，链接到成果入口；下方脚本验证/生成已有决定并汇总排名URL，不自动访问网页、选择模板或引导登录。缺关键输入时先按[补数指引](data-collection-guide.md)带用户收集，已有资料足够的部分先分析。页面正文、视觉设计稿和客户网站不属于本阶段自动完成的成果。

## 生成与检查

```sh
python3 SKILL_DIR/scripts/architecture.py --plan /绝对路径/architecture-plan.json --keywords /绝对路径/industry-keywords.csv --analysis-dir /绝对路径/本轮runs目录 --out /绝对路径/新成果目录
```

Python 3.11+、仅标准库。所有输出写新目录。脚本不访问网页、不调用模型、不替代页面或模板决策。

`--analysis-dir`指向本轮选定的工具输出集合，例如`runs/library`、`runs/frequency`、`runs/ton`、`runs/semantic-prepare`、`runs/semantic`、`runs/urls`。其直接子目录需有`run.json`，也可直接传一个工具输出目录。词库必须与`--keywords`字节一致，其他分析须有同一输入哈希；每轮只放一份build和一份rankings，不混入历史运行、截图或任意客户文件。已审阅子集的分析另留研究记录，不和完整词库运行混装。

生成器自动复制完整分析到`analysis/`，写`analysis-manifest.json`与`analysis-index.md`；存在rankings分析时再把全量URL汇总与整页观察导出到成果根目录，并在HTML“竞品排名页”展示最多500组。仅有整页流量而没有词明细的URL也保留。交付整个成果目录或它的ZIP，不只发送HTML单文件。没有附带目录时兼容旧用法，但明确标记`analysis_delivery.status=not_attached`，不能称完整分析交付；有排名分析时还应核对`rankings_attached=true`及URL/整页记录数量。

首次编排可运行[最小有效示例](minimal-example.md)，包含完整设计字段与常见报错的对应位置。发行版本0.4.1沿用0.4.0计划结构，不需迁移已有计划。

## JSON结构

- `schema_version`: 新计划为`0.4.0`；兼容既有`0.3.0`、`0.2.0`和`0.1.0`非博客计划，不改写历史输入/输出。
- `project`: `name, site_url, market, language, mode, scope_note, exclude_blog`。mode为`simulation`或`real`；exclude_blog为布尔值，新计划通常false以允许博客；用户排除博客时true，旧0.1.0必须为true。允许博客不要求凑足文章或分类。
- `sources`: `id, kind, label, reference, scope`。kind为`user_provided/provider_export/browser_observation/historical_sample/model_inference/simulation`。reference保存实际路径或HTTP(S)URL；scope标覆盖和限制。
- `offerings`: `id, label, status, evidence_ids`。status为`supported/unknown/not_offered`；教学假设用simulation来源支持，并将project.mode设为simulation。
- `competitors`: `id, name, domain, role, visit_status, menu_observed, summary, coverage, evidence_ids`。visit_status为`observed/partial/blocked/not_started/simulated`。可加`sample_group`（`top_competitor/low_authority_high_relevant_traffic/other`）和`selection_reason`；使用分组时必须写入选理由。权重指标名称/数值、引用域、相关流量、日期等保留原表及来源说明，未核值注明未知。模拟排名站不能冒充已访问。
- `groups`: `id, label, buyer_task, rationale`。每条词的主组通过decisions关联；跨市场/语言建独立主组，可以用相近label表达共同主题。
- `templates`: `id, name, mode, allowed_page_types, modules, fields, optional_modules, variants, representative_page_ids, rationale, design_order`。mode为`reusable/bespoke`；variants至少有`default`；design_order为正整数。代表页覆盖所有实际使用的变体。实例和数量从pages计算，不再手填。
- `pages`: `id, name, name_en, parent_id, path, page_type, template_id, variant, state, offering_ids, primary_keyword_id, objective, sections, unique_content, cta, priority, fact_gaps, evidence_ids, rationale`。
  - 只有一个根home页，parent_id为空且path为`/`；其他页面有有效父级。
  - path为不含query/fragment的站内根路径；它不必复制页面父子路径。旧站实际URL另存原始清单，不为迁就原型改旧网址。
  - page_type为`home/category_overview/category/product/attachment/application_hub/application/capability/company/resource/utility/inquiry/blog/blog_category/article`。
  - `article`在完整计划中保留稳定ID与关键词归属，但输出时单独进入文章计划；父级必须为blog/blog_category，不能作为任何页面的父级。博客关闭时3种博客页型均不允许。
  - state为`proposed/conditional`；unknown/not_offered业务不能支持proposed页，conditional页不进入正式菜单。
  - primary_keyword_id可为空；非空时必须是分配给本页的词。章节/筛选词不会因为被承接就自动成为整页主词。
  - sections、unique_content和fact_gaps是字符串数组；即使共用模板，独特内容与事实缺口也按页写。
- `navigation`: `id, label, kind, location, parent_id, page_id, order, rationale`。kind为`page/group`；group没有page_id且必须有子项。location为`header/footer/action`；order为非负整数。这里是全站菜单，不允许article目标；文章通过博客列表/分类等内容入口到达。
- `decisions`: `keyword_id, group_id, disposition, page_id, reason, evidence_ids`。disposition为`page/section/filter/defer/exclude`；前3种必须有目标页，后2种不能假填URL；每条词恰好一行。
- `ranking_observations`: 可空。每行为`id, keyword_id, competitor_id, url, position, market, observed_at, evidence_class, source_id`。position用字符串保留，缺失为空；evidence_class与source.kind一致。只用provider_export/user_provided/historical_sample/simulation来源。可加`estimated_traffic`：该词对该URL的同口径工具估算，非负数或数字字符串；空、N/A、missing等为缺失，不补零。它不是搜索量，也不能填写重复的整页流量。不同竞品、URL、日期的关系保留，缺URL原行留在接收问题表。
- `competitor_page_reviews`: 可选。每行`competitor_id, url, status, page_type, menu_path, modules, summary, evidence_ids`。status为`observed/partial/simulated/blocked/not_started`；observed/partial须有browser_observation来源，simulated须有simulation来源；另可记录blocked/not_started但不填写观察到的模块。page_type为实访描述，不凭URL推断；modules为模块顺序数组，summary写适用或不适用理由。访问时间保存在证据来源中，与排名数据日期分开。

industry-keywords.csv使用现有词库五列：`keyword_id, keyword_original, keyword_normalized, market, language`，保留其他已有字段。已有ID不重编号；规范词+市场+语言唯一。excluded词只能暂缓/排除；要重新采用先在独立审阅中更新并说明理由。

## 0.3.0完整页面设计规格

用户仍提交原始业务资料和表格；下列记录由Codex整理，不要求用户制作JSON。详细研究和取舍方法见[逐页研究与工厂设计](page-research-and-design.md)。

- `site_design`：`visual_direction, typography, spacing, image_style, header, footer, mobile_navigation, fields, evidence_ids`。定义整站视觉与共用区域，fields使用下列字段结构。
- `factory_profile`：`business_role`为`manufacturer/trading_company/mixed/unknown`，另有`identity_statement, evidence_ids, proof_items, page_proof_map`。角色按实际资料核定，未知不变成工厂。
  - `proof_items`每项为`id, topic, claim, status, scope, presentation, missing_action, evidence_ids`。status为`verified/pending/simulation/not_applicable`；verified须有本站企业user_provided资料，不能用竞品浏览或模拟资料证明。topic为certification且verified/simulation时还需`issuer, validity, certificate_scope`，分别核对机构、有效期和适用范围。
  - `page_proof_map`为`page_id, proof_ids, presentation, reason`；核心商业页需映射身份/能力或其他适合实际角色的信任资料，待补项不能作为已具备承诺。
- `research_targets`：全部本轮核心URL目标，每项`id, competitor_id, url, page_type, reason`。明确提供的核心URL逐页登记，重复归并；没有review仍保持not_started，不能悄悄丢掉。
- `competitor_page_reviews`在旧字段上增加`id, review_round, observed_at, follow_up`；已观察/部分/模拟项再有`content_summary, desktop_notes, mobile_notes, module_details`。同URL按正整数轮次追加，不覆盖旧记录。未观察手机写未观察。
  - `module_details`为有顺序的`name, content, layout, fields, interaction, assessment`；fields记录可见内容字段，不猜竞品后台ACF。名称顺序与modules摘要一致。
  - 多轮可按问题回访，不强求固定轮数；最新状态覆盖只计真实observed完成，simulated不算真实访问。
- 每个`template.design_spec`包含`modules, fields, rejected_patterns, research_gaps`。旧template.modules/fields是同顺序名称/标签摘要，需保持一致。
  - 模块：`id, name, purpose, content, desktop_layout, mobile_layout, interaction, required, condition, variants, field_ids, study_ids, decision, evidence_ids`。content为有内容要求的字符串数组；variants为空适用所有变体，否则只作用于列出的变体。field_ids引用本模板/全站字段；study_ids引用已记录的observed/partial/simulated研究，未访页不能当依据。decision说明采用/改造理由。
  - 字段：`id, label, kind, scope, required, format, source, missing_policy, value_status, evidence_ids`。kind为`text/long_text/email/phone/rich_text/image/gallery/number/boolean/choice/link/file/reference/repeater`；scope为`site/page/item`；format说明单位、图片比例、长度、重复记录结构等；required为布尔值。value_status为`available/missing/visitor_input/not_applicable`，available须有资料来源；visitor_input用于联系人、邮箱等由访客填写的表单字段，不当成缺失的企业内容，不预填私人资料。字段ID只是规划引用，不是已实现的后台key。
- 每个核心页及模板代表页有`design_brief`：`audience, primary_message, copy_direction, image_direction, emphasis, module_notes, evidence_ids`。module_notes每项`module_id, content_note`，说明本页内容变化，必须属于其适用模板变体；非代表文章仅保留独立文章计划，不要求逐篇出画稿简报。

结构校验检查引用、状态和覆盖；不能自动判断研究真实性或资料足以证明企业能力。暂缺事实可以规划布局并标占位，不能转换成已核文案。`research_complete`在0.3.0还要求已登记核心URL全部真实观察完成；旧版本仍按原代表菜单覆盖口径。无论版本，都要报告实际范围。

## 0.4.0英文优先、应用层级与属性

完整设计规格沿用0.3.0，再加[英文与属性约定](language-hierarchy-and-attributes.md)。英文站page.name与name_en一致，name_zh保存辅助中文；所有页面都含cta_zh和同序sections_zh。template/module增加name_zh，字段与navigation增加label_zh，design_brief增加primary_message_zh。keyword_analysis.category_candidates增加同序candidate_labels_zh，原始英文关键词保持不变。内部研究说明仍可用中文，访客可见的命名、CTA和章节必须以英文为主。

application详情归application_hub集合页；主导航中的详情通过可点击的集合页入口到达。只有单个独立应用且确无集合必要时可记录standalone_reason；不能绕过多个详情需要集合层级的检查。产品属性记录用product_attributes，关联类目筛选和具体详情字段，详见上述规范。这里的proposed表示规划状态，不代表网站已实现。

图、CSV、面包屑及单页设计任务采用同一份英文/中文名称。HTML只载入最多500条关键词决定、500条排名关系、500组排名URL和500个高DF词组；拆词示例最多20条，词组成员最多20条，单页承接词最多30条、参考URL最多20条。viewer_scope明确记录预览/全量数量及文件链接；HTML搜索只检索已载入记录，完整数据保留在CSV与architecture-plan.json，不删掉未展示部分。

## 竞品URL汇总口径

`competitor-url-summary.csv`来自本轮完整排名分析，按工具、竞品、市场、语言、日期、原URL、证据性质聚合。`received_keyword_traffic`只代表收到范围的逐词估算；`page_estimated_traffic`是独立整页估算，两个字段永不相加。`keyword_traffic_status/page_traffic_status`分别标完整、缺失、部分缺失或冲突。只有页面流量的URL保留，词数量为0不表示流量为0。`page-traffic-observations.csv`保留所有有效整页记录；异常URL及原始行在analysis内的问题表与raw-records保留。两个根目录CSV也带XLSX阅读版。

`ranked-url-summary.csv`按竞品、原始URL、市场、排名数据日期、source_id分组。Top30计数只用位置1–30，按keyword_id去重；超范围/缺排名的原关系仍保留。同一组的逐词流量完整且没有冲突时才给出范围内估算合计；否则合计为空，并报告覆盖数和missing/partial/conflicting状态。重复关系不会重复加流量，不跨来源/日期相加。只汇总收到的排名表范围，不冒充整页或全站全部流量。

关联的页面观察来自competitor_page_reviews，显示观察状态、页型、菜单位置、模块、说明及来源ID。没有观察的URL标not_visited；模拟观察继续明确标识，不能升级成真实页面证据。

## 词频与类目发现记录

完整词库分析增加 `keyword_analysis`：`method`说明分词/计数与解释方法；`tokenized_keywords`逐条包含`keyword_id, keyword, market, language, tokens`；`terms`包含`term, ngram, market, language, tf, df, denominator_keywords, facet_type, keyword_ids`，统计数字为整数。单词表与词组表可复用现有聚类Skill的frequency输出，不要求用户整理。

`category_candidates`逐维度包含`id, dimension, signal_terms, candidate_labels, keyword_ids, page_ids, decision, reason, evidence_ids`。它记录发现的具体类目候选及去向，即使候选暂不建页也保留。每条原关键词有拆词结果；词频引用可追溯到原词，跨市场/语言不混算。没有词库时可缺此块，但必须标为分析未完成，不能宣称完整标准流程已完成。

## 输出

| 产物 | 用途 |
| --- | --- |
| index.html | 主视图为核心架构、完整页面设计简报、模板、导航、独立文章计划；词频、明细和研究证据按需展开。可复制单页设计任务并打开本地Markdown。离线可读，不依赖外部脚本 |
| architecture-plan.json | 经验证的决定快照；下游引用稳定页面/模板ID |
| pages.csv | 核心页（含必要博客入口/分类）的层级、URL、关键词、内容和模板 |
| content-plan.csv | 文章选题、所属分类、关键词、内容提纲、优先级、CTA、资料缺口和模板；不等于已写文章 |
| templates.csv | 模板模块/字段/变体、代表页、实例及设计次序 |
| design-modules.csv / design-fields.csv | 0.3.0有顺序的内容、桌面/手机布局、交互、字段类型/格式/来源/缺失处理 |
| product-attributes.csv | 0.4.0产品属性定义、英文/中文、单位与来源、类目筛选安排、产品详情字段绑定及缺值处理 |
| research-report.md | 0.3.0本地Markdown汇总：全部目标URL、逐页/逐轮内容布局与字段观察、采用取舍、来源与待核项 |
| design-briefs.md | 0.3.0完整核心页和模板代表页的可复制AI画稿任务，含工厂证据与事实缺口 |
| navigation.csv | 独立菜单分组与页面目标 |
| term-frequency.csv / tokenized-keywords.csv / category-candidates.csv | 词频、逐条拆词与类目发现证据链（提供分析时生成） |
| keyword-decisions.csv | 每个词的主组、目标或暂缓/排除理由 |
| ranking-observations.csv | 保留关键词—竞品—排名URL关系，不推定真实排名 |
| ranked-url-summary.csv | 按URL汇总排名词/需求与可得估算流量，并关联实访页型、菜单和模块 |
| competitor-url-summary.csv / page-traffic-observations.csv | 附带rankings分析时自动交付全量URL与独立整页流量，保留无逐词明细的URL；附XLSX阅读版 |
| analysis/、analysis-index.md、analysis-manifest.json | 自动附带本轮选定的完整分析、入口链接、文件数量和逐文件哈希；未附带时明确标记 |
| mindmap.md | 缩进树，供导入支持Markdown大纲的思维导图工具；不保证所有XMind版本原生导入 |
| design-handoff.md | 核心页与文章数分别统计，模板/变体和样板、逐核心页及文章代表页要求、资料缺口与WordPress Builder接续阶段 |
| validation.json | 输入摘要、覆盖、计数及限制；通过只表示结构一致 |

CSV供程序继续处理；用户优先看HTML表格，避免表格软件把外部字符串识别成公式。需Excel时使用当前可用的表格工具生成阅读版，核对原值并保留原CSV。

## 校验边界

检查树循环、重复页面/URL、失效父级和菜单目标、文章混入全站菜单、模板及代表页、逐词覆盖、主词归属、市场与来源引用、模拟标注和未确认业务进入菜单。0.4.0另核英文主名称/中文辅助、应用集合层级与可点击导航、属性字段类型与引用、真实值缺失时不能启用筛选。结构检查不证明来源真实、语义正确、竞品全量完成或视觉设计已经完成。0.3.0及0.4.0数据结构下，`research_complete`还要求已登记的本轮核心URL全部真实观察完成；旧0.1.0/0.2.0沿用名单代表结构实访口径。均不自动代表每条排名URL都已回读，须结合目标清单、接收记录与逐页实访记录说明实际覆盖。
