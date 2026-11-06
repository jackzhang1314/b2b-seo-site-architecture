# 英文命名、应用层级与产品属性

## 网站内容用英文，中文服务审阅

英文站的`name`和`name_en`一致，`name_zh`单独保存中文；例如`Mini Excavators / 小型挖掘机`、`Applications / 应用方案`。导航用`label / label_zh`，CTA用`cta / cta_zh`，页面章节用同序`sections / sections_zh`，模板/模块用`name / name_zh`，字段和产品属性用`label / label_zh`。设计简报的首屏核心信息用`primary_message / primary_message_zh`。

HTML先显示英文、再显示辅助中文；CSV和JSON分别保留两种语言，英文网站后续只取英文访客文案。页面用途、研究取舍、布局要求和素材缺口可以用中文解释，不能把这些内部说明当作最终英文正文。所有英文内容依据实际产品、采购任务与企业事实组织，不照字面拼接中文，也不补造规格、证书、价格或产能。

## 应用方案有集合层级

```text
Home（首页）
├─ Mini Excavators（小型挖掘机）
│  ├─ 1 Ton Mini Excavators（1吨级小型挖掘机）
│  ├─ 2 Ton Mini Excavators（2吨级小型挖掘机）
│  └─ 3 Ton Mini Excavators（3吨级小型挖掘机）
└─ Applications（应用方案）
   ├─ Landscaping（园林与绿化）
   └─ Trenching（管沟工程）
```

`application_hub`是具有独立URL与内容的集合页；`application`是场景详情页。集合页解释覆盖范围、展示应用入口、提供跨场景选型线索；详情页解释现场约束、适用选择、证据和询价资料。可以分别使用模板，不能仅为让树好看创建没有内容的空页。

多个应用详情的直接上级应为真实集合页；主导航中的应用详情也要从可点击的集合页入口进入。单个独立应用若确无集合必要，可记录`standalone_reason`，但不能用这个例外把多个应用全部放到首页下。URL路径、页面树、导航层级分别建模；增加父页不擅改已有URL，真实改版的URL调整另核授权与旧页表现。

## 一份属性定义，关联两个使用位置

关键词的高频词、数字和共现修饰词提示买家关注的属性；竞品类目筛选、规格表、对比表、FAQ和应用页帮助核对其采购含义。研究后记录为`product_attributes`，不要从词频直接推断本企业参数值。

| 属性例子 | 类目页可能用途 | 产品详情页应填什么 |
| --- | --- | --- |
| Weight Class（吨位等级） | 系列筛选或经判断后的独立类目 | 目录归属；与实测工作重量分开 |
| Operating Weight（工作重量） | 数据齐全且有有用差异时做范围筛选 | 数值、kg、配置和测试口径 |
| Overall Width（整机宽度） | 按通行空间选型；核实后再做范围筛选 | mm；固定/伸缩底盘条件及必要的最小/最大宽度 |
| Maximum Digging Depth（最大挖掘深度） | 核实产品值与采购用途后再筛选 | mm；工作装置和测量条件 |
| Rated Engine Power（发动机额定功率） | 有实际采购价值时才成为筛选 | kW；发动机型号、转速和功率标准 |

这些是机械行业解释示例，不能照搬为每个行业的必填字段。CNC、电子、材料、包装等产品应从本行业证据归纳。只有用户资料能证明本站某型号的真实值；竞品可以提示字段和比较方式。

0.4.0属性记录包含：

- `id, label, label_zh, definition, value_type, unit, measurement_note`：稳定ID、英文/中文、含义、类型（number/choice/boolean/text）、单位（无单位为空）、口径。
- `keyword_ids, study_ids, evidence_ids, source_status`：来源与状态（available/partial/missing/simulation）。未访页面不能作研究证据；available须有本站资料。模拟值仅在simulation计划中使用。
- `filter_status, filter_control, filter_page_ids, filter_options, filter_reason, filter_missing_policy`：proposed/deferred/not_applicable；range/select/multi_select/toggle/none；适用类目；选项`value,label,label_zh`；采用理由和缺值处理。未知选项为空，不填0或猜范围；缺值时保持deferred。proposed表示规划中有依据可设计，不代表筛选功能已实现。
- `detail_bindings`：每项`template_id, field_id`，关联产品/附件模板中的具体类型字段，不只笼统指向“规格参数表”。字段中继续记录必填、单位/格式、来源、缺失处理和展示条件。

同一属性可既是独立类目维度，又用于其他类目的筛选和详情参数。避免在“1 Ton”类目下再放只有“1 Ton”一个选项的无效筛选。数值范围、配置间比较、单位换算及适用认证需另核事实；缺失或不适用状态不能与真实0混同。
