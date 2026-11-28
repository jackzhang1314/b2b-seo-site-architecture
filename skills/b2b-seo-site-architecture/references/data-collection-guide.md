# 怎样登记和读取网站架构资料数据

## 建目录或登记位置

在用户当前的建站项目操作。已有的目录或原表直接登记，包括分散存放的位置。用户要求新建时，可在项目根的「网站架构资料」下按下例建立四个目录。项目内用相对项目根的路径，项目外用用户提供且能定位的完整路径。一个用途有多个位置时逐一写明。

在项目根的大写 `AGENTS.md` 中登记，文件尚不存在就创建。只有三级标题固定，下例的目录名和列表写法可随项目调整。

```markdown
### 网站架构资料数据

- 01 产品种子词拓词表 — 按产品种子词导出的买家查询，存放在 `网站架构资料/01 产品种子词拓词表/`
- 02 竞品排名关键词 — 各同行的原词、自然排名和具体页面 URL，存放在 `网站架构资料/02 竞品排名关键词/`
- 03 竞品 Top Pages — 各同行按页面 URL 导出的整页报告，存放在 `网站架构资料/03 竞品 Top Pages/`
- 04 同行网站名单 — 已知同行的官网域名或网址，存放在 `网站架构资料/04 同行网站名单/`
```

可在同一小节另记公司介绍、产品目录的现有路径。已有小节时就地补写变动的用途或路径，保留其余说明。把小节放在工作台的 `<!-- b2b-seo:architecture:start -->` 与 `<!-- b2b-seo:architecture:end -->` 所围区域之外，也避开 WordPress Builder 的托管区。写前重读、写后核对当前项目的四条路径。把四个实际位置交给用户即完成这一步，即使目录仍为空。

## 收到读取或规划指令

1. 先读根 `AGENTS.md` 的上述小节，接受普通中文、列表或表格。以当前项目根解析相对路径，打开指向的文件或目录，包括目录内的分站、分市场子目录。核对资料来源、工作表、表头和实际记录。文件名、文件扩展名和文件数量仅作线索。
2. 有断开的路径或旧项目没有小节时，先用用户已交的附件、四条路径和可识别的项目原件。旧工作台优先用用户指定版或同一市场、语言和资料性质的采用版，从 `seo-project-index.json` 的 `originals` 或 `input-files.csv` 找到已附存的原件。仅有清单或清洗结果时记录可用范围。定位到搬迁后的真实原件再改指针。归档副本按来源、日期和文件摘要核对，避免重复入库。只交附件且尚无建站项目时，可直接接收分析。
3. 结合供货范围、国家与语言，按同行域名逐一核对 02 和 03。一个工作簿可以含多家或多种报告，一家也可能有多批文件。对照实际工作表和行内 URL，映射不同工具、语言的同义表头。单元格缺值单独标记，保留有效行。保存 Excel 原件，需要 CSV 时由 Codex 提取对应工作表并保留原表、工作表和来源行的对应关系。
4. 缺名单时先从已交报告提取域名，再按下文查找和核验相关同行。对仍有缺口的部分，只列实际问题、缺哪家哪张报告或哪一列、在哪里导出、应存到哪个实际位置。区分路径失效、目录为空、放错报告、表无有效行及某个同行未覆盖。工具明确显示该市场无结果时登记实际范围和日期。已有材料足以完成的工作直接继续。齐备后按本次任务建库和规划。更新既有工作台时按原有修订流程生成草案。

## 01 产品种子词拓词表

核对原词列（如 Keyword 或 Query）及可用数据行，保留已知 Volume、KD、种子、市场、来源和日期。只有产品目录时，Codex 先提取产品、服务和买家常用的英文别名。例如玻璃酒瓶可从 `glass liquor bottles`、`spirits bottles`、`whiskey bottles` 分别拓展。

- **Semrush** 打开 Keyword Magic Tool，选目标国家，逐个输入种子。用 Broad、Phrase 或 Exact 查不同匹配范围，用 Related 补相关主题，导出 CSV 或 Excel。[Keyword Magic Tool](https://www.semrush.com/kb/617-keyword-magic-tool-manual)
- **Ahrefs** 打开 Keywords Explorer，选目标国家，从 Matching terms 拓词，按需补 Related terms 或 Search suggestions。记录种子、报告和日期，保留 Keyword、Volume、KD 及已有原列。[Keywords Explorer](https://ahrefs.com/academy/how-to-use-ahrefs/keywords-explorer/matching-terms)

AI 可按产品目录枚举“材质 × 工艺”或“酒种 × 容量”等组合。已有 AI 词表照收，标为候选。通过工具数据、搜索结果、客户询盘和供货能力核验，未知搜索量与 KD 留空。竞品词里发现的新叫法可再作种子。

## 02 竞品排名关键词

按同行和目标市场取得实际可得的较完整自然排名原表，保留每行的原词、自然排名位置和排名页面具体 URL，以及可得的搜索量、KD、逐词流量等列。Semrush 使用 Organic Rankings 的 Positions 报告，Ahrefs 使用 Site Explorer 的 Organic keywords。课堂演示可筛 Position 1–30，收下该范围所有可得词。正式建库保留更广排名原件，另算前 30 名视图。记录实际筛选、导出日期、行数及工具限额。[Semrush Positions](https://www.semrush.com/kb/494-organic-rankings-positions-report) · [Ahrefs Organic keywords](https://ahrefs.com/academy/how-to-use-ahrefs/site-explorer/organic-keywords)

## 03 竞品 Top Pages

打开同站、同目标市场的 Semrush Top Pages（或 Organic Rankings 的 Pages）或 Ahrefs Site Explorer 的 Top pages，核对报告按具体页面 URL 汇总。Top Pages 中的 Top keyword 只代表该页的代表词。逐词排名要回到 02 的明细核对。保留原表里的整页自然流量、排名词数（Keywords）、首要词和其他原列。确认来自 Top Pages 的报告即使缺流量列，也能保留 URL，流量记为未知。课堂可筛 `Keywords > 1`。正式项目保留可得的完整页面范围，包括没有逐词明细的 URL。逐词排名表中的 Traffic 属于单个查询，不能用它充当整页报告。[Semrush Top Pages](https://www.semrush.com/kb/1614-top-pages) · [Ahrefs Top pages](https://ahrefs.com/academy/how-to-use-ahrefs/site-explorer/top-pages)

例如某同行已经交了带原词、排名和 URL 的报告，只缺美国市场的 Top Pages，就给出该域名、Top Pages 报告入口、页面 URL 和可得的整页指标，请学员把导出存入已登记的 03 路径。若 02 的某张表缺排名位置，则只补该同行的 Positions / Organic keywords 原表。

## 04 同行网站名单

接收表格、文本或已有提名中的官网域名或网址。正式项目可以分几天逐批扩大行业覆盖，保存全名单、已拿到的报告和待补范围。课堂围绕一张同类页面挑约 5 家做对标，这个样本用于比较页面。词库使用全部相关同行及实际可得数据。

1. **按采购商的搜法找。** 用“产品或别名 × manufacturer / supplier / OEM / wholesale × 供应商所在国或当地叫法”分轮公开检索。例如 `custom liquor bottle manufacturer`、`glass bottle supplier in Vietnam`。接着用新发现的产品名和企业补查。记录每轮查询、已查市场和新合格域名。主要维度已覆盖、连续两轮无新增时可收尾，列出覆盖缺口。
2. **用行业词反查。** 有 Ahrefs 时，将相关词表放回 Keywords Explorer，选买家搜索的目标国家，打开 Traffic share → By domains，导出域名。词多时按报告允许的规模分批。只有 Semrush 时，可输入一家已核验的同行域名，从 Organic Rankings → Competitors 补找共享排名词的网站。[Ahrefs 按词表找域名](https://ahrefs.com/academy/how-to-use-ahrefs/keywords-explorer/traffic-share-domains) · [Semrush 自然搜索竞品](https://www.semrush.com/kb/496-organic-rankings-competitors-report)
3. **核验后出名单。** 合并两路结果、客户提名和已有排名表里的域名，按根域去重，确属不同经营主体的另列。点开官网产品、OEM、批发或询价页，记录公司与域名、角色、产品、国家、发现来源和证据 URL。保留有企业采购证据的同行，纯零售、平台和目录另列，身份存疑标待核。有 Ahrefs 可用 Batch Analysis 按同一目标国家和域名范围比较自然流量，结合指定行业词流量和产品相关性排优先级。保留完整合格名单。[Ahrefs 批量比较](https://help.ahrefs.com/en/articles/79496-how-to-use-batch-analysis-to-analyze-many-urls-domains-at-once)

## 需要 Codex 代为导出时

**用户授权代办时**，Codex 用 Computer Use 打开用户指定的 Semrush 或 Ahrefs 网页，复用已授权的登录状态，或按网页流程登录。需要本人输入密码或二次验证时，让用户在网页完成，随后继续筛选、导出并核对下载文件。按本次授权和账号现有额度处理各站。浏览器不可用时给手工导出步骤，报告限行时登记可见总行数、实收行数和未覆盖范围，利用已有样本继续分析。

收到文件后，Codex 打开原表核对表头、记录数、域名、市场、日期和筛选范围，按站确认两份报告的覆盖，继续合表、去重和页面规划。逐词排名和整页流量各自保留原行。只有工厂资料时先交种子、已核验的同行和具体补数步骤。工作台的需求和指标据实际收到的资料生成。
