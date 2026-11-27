# 怎样补齐网站架构需要的四份资料

先读客户给的项目文件夹、附件和对话。用工厂实际供应的产品、服务、目标国家和语言确定范围。按下列四项标记已收到、部分覆盖、缺失。资料齐全就开始分析。部分缺失时说明已有资料能做什么，给客户一个带真实产品词或域名的下一步，其余工作继续。

## 1. 产品核心词或拓词表

有词表就接收原件。只有目录时，Codex 先提取产品、服务和买家常用的英文别名。例如玻璃酒瓶可从 `glass liquor bottles`、`spirits bottles`、`whiskey bottles` 分别拓展。只问会改变搜索范围的未知项。

- **Semrush** 打开 Keyword Magic Tool，选目标国家，逐个输入种子。用 Broad、Phrase 或 Exact 查不同匹配范围，用 Related 补相关主题，导出 CSV 或 Excel。[Keyword Magic Tool](https://www.semrush.com/kb/617-keyword-magic-tool-manual)
- **Ahrefs** 打开 Keywords Explorer，选目标国家，从 Matching terms 拓词，按需补 Related terms 或 Search suggestions。记录种子、报告和日期，保留 Keyword、Volume、KD 及已有原列。[Keywords Explorer](https://ahrefs.com/academy/how-to-use-ahrefs/keywords-explorer/matching-terms)

AI 可按产品目录枚举“材质 × 工艺”或“酒种 × 容量”等组合。已有 AI 词表照收，标为候选。通过工具数据、搜索结果、客户询盘和供货能力核验，未知搜索量与 KD 留空。竞品词里发现的新叫法可再作种子。

## 2. 由 Codex 找外贸 B2B 独立站同行

1. **按采购商的搜法找。** 用“产品或别名 × manufacturer / supplier / OEM / wholesale × 供应商所在国或当地叫法”分轮公开检索。例如 `custom liquor bottle manufacturer`、`glass bottle supplier in Vietnam`。接着用新发现的产品名和企业补查。记录每轮查询、已查市场和新合格域名。主要维度已覆盖、连续两轮无新增时可收尾，列出覆盖缺口。
2. **用行业词反查。** 有 Ahrefs 时，将相关词表放回 Keywords Explorer，选买家搜索的目标国家，打开 Traffic share → By domains，导出域名。词多时按报告允许的规模分批。只有 Semrush 时，可输入一家已核验的同行域名，从 Organic Rankings → Competitors 补找共享排名词的网站。[Ahrefs 按词表找域名](https://ahrefs.com/academy/how-to-use-ahrefs/keywords-explorer/traffic-share-domains) · [Semrush 自然搜索竞品](https://www.semrush.com/kb/496-organic-rankings-competitors-report)
3. **核验后出名单。** 合并两路结果、客户提名和已有排名表里的域名，按根域去重，确属不同经营主体的另列。点开官网产品、OEM、批发或询价页，记录公司与域名、角色、产品、国家、发现来源和证据 URL。保留有企业采购证据的同行，纯零售、平台和目录另列，身份存疑标待核。有 Ahrefs 可用 Batch Analysis 按同一目标国家和域名范围比较自然流量，结合指定行业词流量和产品相关性排优先级。保留完整合格名单。[Ahrefs 批量比较](https://help.ahrefs.com/en/articles/79496-how-to-use-batch-analysis-to-analyze-many-urls-domains-at-once)

## 3. 每个同行的前 30 名排名词

按名单逐站导出。Semrush 用 **Organic Rankings → Positions**，Ahrefs 用 **Site Explorer → Organic keywords**。输入竞品域名，选目标国家，设 **Position 1–30**，导出当前权限下该范围的全部记录。每行至少有 **Keyword、Position、排名页面的完整 URL**，有 Volume、KD、Traffic 等列就一起保留。词可以不含种子。若已有更广的排名表，保留整表，由 Codex 筛出前 30 名视图。缺 URL 时只补相应站点的明细。[Semrush Positions](https://www.semrush.com/kb/494-organic-rankings-positions-report) · [Ahrefs Organic keywords](https://ahrefs.com/academy/how-to-use-ahrefs/site-explorer/organic-keywords)

## 4. 每个同行有排名、有流量的页面

在同一域名范围、国家和数据日期，打开 **Semrush SEO → Top Pages**，选择 Organic 来源（部分界面为 Organic Rankings → Pages），或 **Ahrefs Site Explorer → Top pages**。按整页估算自然流量降序，导出全部可得页面。保留**具体页面 URL、整页自然流量**，以及可得排名词数、首要关键词和其他原列。已有商品报价资料也随原件接收。单独保存页面表，之后用 URL 与逐词排名表关联。某页只有流量时照收，缺的逐词明细标为待补。[Semrush Top Pages](https://www.semrush.com/kb/1614-top-pages) · [Ahrefs Top pages](https://ahrefs.com/academy/how-to-use-ahrefs/site-explorer/top-pages)

## 放进一个文件夹，再生成工作台

复用客户已有文件夹和文件名。新项目可以照下面放，同名导出按域名、国家或日期分存。客户只需交整个文件夹。

```text
玻璃酒瓶-网站架构资料/
  产品目录.pdf
  公司介绍.md
  核心词与拓词/
  B2B 竞品名单.csv
  竞品排名词/          每站的 Positions 或 Organic keywords 导出
  竞品 Top 页面/       每站的页面流量导出
```

客户自行操作时，只给所缺报告的入口、筛选、必留列和保存位置。例如美国市场的排名词表已经齐全，只缺页面表，就请客户在 Ahrefs 的 Site Explorer 输入名单中缺表的域名，选 United States → Top pages → Export，把原件放进“竞品 Top 页面”。

**用户授权代办时**，Codex 用 Computer Use 打开用户指定的 Semrush 或 Ahrefs 网页，复用已授权的登录状态，或按网页流程登录。需要本人输入密码或二次验证时，让用户在网页完成，随后继续筛选、导出并核对下载文件。按本次授权和账号现有额度处理各站。浏览器不可用时给手工导出步骤，报告限行时登记可见总行数、实收行数和未覆盖范围，利用已有样本继续分析。

收到文件后，Codex 打开原表核对表头、记录数、域名、市场、日期和筛选范围，按站确认两份报告的覆盖，继续合表、去重和页面规划。逐词排名和整页流量各自保留原行。只有工厂资料时先交种子、已核验的同行和具体补数步骤。工作台的需求和指标据实际收到的资料生成。
