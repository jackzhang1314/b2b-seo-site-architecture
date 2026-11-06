# 可运行的最小计划示例

给Codex理解结构与定位报错使用。用户仍然提供原始业务资料和表格，不需要制作JSON。本例只有1页、1模板和2条词，故意没有实访、整页流量与产品参数，不能当成完整网站方案。

- [minimal-plan.json](../examples/minimal-plan.json)：完整0.4.0结构，0.4.1工具直接读取。
- [keywords.csv](../examples/keywords.csv)：与计划ID一致的合成词表。

在自己选定的新输出目录执行（把`SKILL_DIR`替换为实际Skill目录）：

```sh
python3 SKILL_DIR/scripts/architecture.py --plan SKILL_DIR/examples/minimal-plan.json --keywords SKILL_DIR/examples/keywords.csv --out /绝对路径/新的示例成果目录
```

预期：结构检查通过，1核心页/1模板/2关键词；`research_complete=false`，`analysis_delivery.status=not_attached`。这表示结构可运行，不表示已研究或已交齐完整分析。真实项目完成分析后按[输出规范](output-contract.md)传入`--analysis-dir`。

## 从哪里修改

| 位置 | 示例关联 | 实际项目怎样填写 |
| --- | --- | --- |
| `sources` | `S_DEMO.kind=simulation` | 原始证据按真实性登记，不把MOCK改为user_provided来绕过检查 |
| `pages[P_HOME]` | `template_id=T_HOME` | 本页类型、目标、真实内容差异与资料缺口 |
| `templates[T_HOME].design_spec.modules[hero]` | `field_ids`引用`headline`和全站`site_contact` | 按模块顺序填写内容要求、桌面/手机布局、交互及证据 |
| `templates[T_HOME].design_spec.fields[headline]` | 页面自己的标题字段 | 类型、格式、必填、来源与缺失处理；不是已实现的后台字段 |
| `site_design.fields[site_contact]` | 可被各模板模块复用 | 客户共用内容；不能和模板字段重名 |
| `pages[P_HOME].design_brief.module_notes` | `module_id=hero` | 此页面对同一模块的具体内容要求 |
| `decisions` | `kw_demo_main→page`；`kw_demo_section→section` | 每词唯一主去向，章节/筛选词不自动变成整页主词 |

`primary_keyword_id`非空时，必须对应`decisions`中同页且`disposition=page`的词。没有独立页面主词可以留空；不要为了通过检查把正确的section/filter决定改成page。

## 常见报错怎样处理

- `Invalid source_id 'seed.01'`：只允许ASCII字母、数字、下划线与连字符，例如`seed_01`。点号和空格不是“重复ID”。
- `Duplicate source_id 'seed_01'`：同一清单已有这个编号，为另一个来源使用不同编号。
- `invalid evidence_class ... allowed: ...`：按真实来源选择列出的值。MOCK在词库、排名、页面流量的`evidence_class`及计划来源的`kind`统一为`simulation`；项目`mode`也为`simulation`。
- `pages[P_HOME].primary_keyword_id ...`：按报错中的页面ID和词ID回查上面的规则；保留原本正确的业务判断。
- `Unknown module field reference`：检查模块`field_ids`是否指向同模板或`site_design.fields`里的字段ID；报错给出模板和模块ID。
- `keyword hash mismatch`：附带的分析不是当前`--keywords`的运行结果。选择同一完整词库对应的本轮目录；子集分析另留研究记录，不混装成全库结果。
- `multiple rankings/build runs`：传入了包含历史版本的总目录。只挑当前需要交付的各个工具输出目录组成这一轮的analysis目录，保留旧目录。

模板摘要`modules/fields`的名称与详细定义顺序一致；中文翻译用单独`*_zh`字段。更多属性、应用层级和研究字段以[完整输出契约](output-contract.md)为准。
