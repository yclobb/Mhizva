# Task for DeepSeek: write a training worksheet for "Mhizva"
# 给 DeepSeek 的任务：为 "Mhizva" 编写训练数据

(Paste everything below into DeepSeek. At the end, replace BATCH with a line from the batch plan.)
（把下面全部内容粘贴给 DeepSeek。最后把 BATCH 换成批次计划里的一行。）

---

## English

You are writing training data for Mhizva, a small AI assistant that runs on a small business owner's laptop and answers questions about the business's own documents (invoices, bills, quotes, contracts, spreadsheets) and general questions.

Before Mhizva answers, it fills in a **form** that says what the message is asking. Your job: write realistic messages a small business owner might type, and the correct form for each.

### Output format
Output **only** JSON Lines: one JSON object per line, no commentary, no numbering, no code fences. Every object has exactly these keys:

```
{"source": "deepseek", "lang": "en", "q": "<message>", "history": [], "form": {"intent": "...", "direction": "...", "group_by": "...", "filters": {...}, "subject": "..."}}
```

- `lang`: `"en"` or `"zh"` (the language of the message).
- `q`: the message exactly as a person would type it. Include typos, slang, missing punctuation, very short messages ("unpaid??"), long rambling ones, Australian wording (e.g. "tradie", "ute", "GST", "BAS").
- `history`: usually `[]`. For follow-up messages that only make sense after an earlier exchange, include the earlier turns: `[{"role":"user","content":"..."},{"role":"assistant","content":"..."}]`.

### Form fields
**intent**, exactly one of:
- `chat`: small talk, greetings, thanks, jokes, insults, opinions about the conversation
- `about_me`: questions about the assistant itself (what it is, which model, who made it, what it can do, its limits)
- `general`: world or general-knowledge questions, not about this business's records
- `advice`: asking what to do about a decision (money, legal, hiring, investing)
- `lookup`: one fact from the business's documents (a date, a name, a price, a term)
- `total`: add up or average money across records (including "breakdown per X")
- `count`: how many records match
- `list`: list or show the records that match
- `rank`: top or bottom records/groups (who owes the most, best customers, biggest expenses)
- `compare`: compare two amounts or groups (money in vs out, this month vs last month, A vs B)
- `chart`: a chart or graph of the business's numbers
- `trend`: whether numbers are rising, falling or changing over time
- `stats`: statistics of a series (averages, volatility, returns, drawdown, moving averages, seasonality, e.g. stock prices, sales)

**direction**, exactly one of: `in` (money owed to the business: its invoices, sales, customers), `out` (money the business pays: bills, suppliers, expenses, wages), `net` (both together: profit, cash flow, balance), `none` (not about money).

**group_by**: `""`, or what a total/ranking/chart is split by: `customer`, `supplier`, `month`, `quarter`, `week`, `category`, `product`, `staff`, `project`, etc.

**filters**: only what the message actually limits it to. Allowed keys:
- `period`: `today`, `yesterday`, `this_week`, `last_week`, `this_month`, `last_month`, `this_quarter`, `last_quarter`, `this_year`, `last_year`, `ytd`, or a specific `"2026-Q2"` / `"2026-04"`
- `date_field`: `issued`, `due` or `paid` (only when the message says which date, e.g. "due this week", "paid yesterday")
- `status`: `unpaid`, `paid`, `overdue`, `partly_paid`, `written_off`
- `days_overdue`: e.g. `">30"`
- `amount`: e.g. `">1000"`, `"<50"`
- `category`: free text (e.g. `"software"`, `"fuel"`)
- `party`: a named customer or supplier (e.g. `"Timber Co"`)

**subject**: 1-4 words saying what the message is about, or `""`.

### Spec v2.1 additions (agreed 2026-10-18)
- `form.agg` (optional, default `"sum"`): `"sum"`, `"avg"`, `"min"`, `"max"`. Averages are intent `total` with `agg: "avg"`.
- `filters.period` may be a 2-item list for compare: `["this_month", "last_month"]`.
- `filters.ref`: a document number the message names, e.g. `"INV-1043"`, `"B-2201"`.
- `form.money_field` (optional, default `"total"`): `"total"`, `"subtotal"`, `"tax"`. "How much GST did we charge" → intent `total`, direction `in`, `money_field: "tax"`.
- `filters.category` suggested values: fuel, materials, software, rent, wages, insurance, utilities, travel, marketing, equipment, subcontractors, other. Free text is still allowed.
- Quotes count as `direction: "in"` (money the business expects to receive).

### Consistency rules (very important)
1. A "breakdown per X" is `total` with `group_by: X`, never `list`.
2. Time is a filter, not a grouping: "this week" → `period`. But "monthly breakdown" → `group_by: "month"`.
3. "Overdue" → `status: "overdue"`; add `days_overdue` only if a number of days is given.
4. chat / about_me / general / advice, and lookups that are not about money, use `direction: "none"`.
5. Every key is always present. Use `""` for empty strings and `{}` for no filters.
6. Use only made-up names, companies and amounts. No real people's data.
7. Do not repeat the same sentence pattern with only a name changed. Vary wording, length and tone.

### Examples
{"source": "deepseek", "lang": "en", "q": "who still owes me money from last month?", "history": [], "form": {"intent": "list", "direction": "in", "group_by": "", "filters": {"status": "unpaid", "period": "last_month"}, "subject": "unpaid customers"}}
{"source": "deepseek", "lang": "en", "q": "breakdown of what each client owes", "history": [], "form": {"intent": "total", "direction": "in", "group_by": "customer", "filters": {"status": "unpaid"}, "subject": "balances owed"}}
{"source": "deepseek", "lang": "en", "q": "bills due this wk over 500", "history": [], "form": {"intent": "list", "direction": "out", "group_by": "", "filters": {"period": "this_week", "date_field": "due", "amount": ">500"}, "subject": "upcoming bills"}}
{"source": "deepseek", "lang": "en", "q": "did we make more than we spent in Q2", "history": [], "form": {"intent": "compare", "direction": "net", "group_by": "", "filters": {"period": "2026-Q2"}, "subject": "profit"}}
{"source": "deepseek", "lang": "en", "q": "when does the ute rego expire", "history": [], "form": {"intent": "lookup", "direction": "none", "group_by": "", "filters": {}, "subject": "ute registration"}}
{"source": "deepseek", "lang": "en", "q": "lol ur useless", "history": [], "form": {"intent": "chat", "direction": "none", "group_by": "", "filters": {}, "subject": ""}}
{"source": "deepseek", "lang": "en", "q": "what model r u running on", "history": [], "form": {"intent": "about_me", "direction": "none", "group_by": "", "filters": {}, "subject": "model"}}
{"source": "deepseek", "lang": "en", "q": "and what about last month?", "history": [{"role": "user", "content": "how much did we spend on fuel this month"}, {"role": "assistant", "content": "Total: $1,240 across 9 receipts."}], "form": {"intent": "total", "direction": "out", "group_by": "", "filters": {"period": "last_month", "category": "fuel"}, "subject": "fuel spend"}}

### This batch
Write **100** examples for: BATCH

---

## 中文

你要为 Mhizva 编写训练数据。Mhizva 是一个运行在小企业主笔记本电脑上的小型 AI 助手，负责回答关于企业自身文件（发票、账单、报价、合同、电子表格）的问题，以及一般性问题。

Mhizva 在回答之前，会先填写一张**表单**，说明这条消息在问什么。你的任务：写出小企业主可能真实输入的消息，并为每条消息写出正确的表单。

### 输出格式
**只**输出 JSON Lines：每行一个 JSON 对象，不要任何说明、编号或代码块。每个对象恰好包含以下键：

```
{"source": "deepseek", "lang": "zh", "q": "<消息>", "history": [], "form": {"intent": "...", "direction": "...", "group_by": "...", "filters": {...}, "subject": "..."}}
```

- `lang`：`"en"` 或 `"zh"`（消息所用的语言）。
- `q`：消息原文，就像真人会打的那样：包括错别字、口语、缺少标点、很短的消息（"没付的？？"）、啰嗦的长消息。
- `history`：通常是 `[]`。对于只有在之前对话之后才有意义的追问，写上之前的对话轮次。

### 表单字段
**intent**（只能选一个，值保持英文）：
- `chat`：闲聊、问候、感谢、玩笑、骂人、对对话的评价
- `about_me`：关于助手本身的问题（它是什么、用什么模型、谁做的、能做什么、有什么限制）
- `general`：常识或世界知识问题，与本企业记录无关
- `advice`：询问某个决定该怎么做（钱、法律、招人、投资）
- `lookup`：从企业文件中查一个事实（日期、名字、价格、条款）
- `total`：对记录中的金额求和或求平均（包括"按 X 分类汇总"）
- `count`：有多少条记录符合
- `list`：列出符合条件的记录
- `rank`：最高或最低的记录/分组（谁欠得最多、最好的客户、最大的支出）
- `compare`：比较两个金额或分组（收入与支出、本月与上月、A 与 B）
- `chart`：业务数字的图表
- `trend`：数字是在上升、下降还是变化
- `stats`：一组数字的统计（平均值、波动率、回报、回撤、移动平均、季节性，例如股价、销售额）

**direction**（只能选一个）：`in`（别人欠企业的钱：发票、销售、客户），`out`（企业要付的钱：账单、供应商、费用、工资），`net`（两者合并：利润、现金流、余额），`none`（与钱无关）。

**group_by**：`""`，或者汇总/排名/图表按什么分组：`customer`、`supplier`、`month`、`quarter`、`week`、`category`、`product`、`staff`、`project` 等（值用英文）。

**filters**：只写消息里真正限定的条件。允许的键（值用英文，category 和 party 可用中文原文）：
- `period`：`today`、`yesterday`、`this_week`、`last_week`、`this_month`、`last_month`、`this_quarter`、`last_quarter`、`this_year`、`last_year`、`ytd`，或具体的 `"2026-Q2"` / `"2026-04"`
- `date_field`：`issued`、`due` 或 `paid`（只有在消息说明是哪个日期时才写，例如"这周到期"、"昨天付的"）
- `status`：`unpaid`、`paid`、`overdue`、`partly_paid`、`written_off`
- `days_overdue`：例如 `">30"`
- `amount`：例如 `">1000"`、`"<50"`
- `category`：自由文本（例如 `"软件"`、`"油费"`）
- `party`：具体的客户或供应商名字（例如 `"木材公司"`）

**subject**：用 1-4 个词说明消息的主题，或 `""`。

### 规范 v2.1 补充（2026-10-18 确认）
- `form.agg`（可选，默认 `"sum"`）：`"sum"`、`"avg"`、`"min"`、`"max"`。求平均是 intent `total` 加 `agg: "avg"`。
- `filters.period` 在比较时可以是两个元素的列表：`["this_month", "last_month"]`。
- `filters.ref`：消息中提到的单据编号，例如 `"INV-1043"`、`"B-2201"`。
- `form.money_field`（可选，默认 `"total"`）：`"total"`、`"subtotal"`、`"tax"`。"我们收了多少消费税（GST）" → intent `total`，direction `in`，`money_field: "tax"`。
- `filters.category` 建议值：fuel、materials、software、rent、wages、insurance、utilities、travel、marketing、equipment、subcontractors、other。仍然允许自由文本。
- 报价单算 `direction: "in"`（企业预计会收到的钱）。

### 一致性规则（非常重要）
1. "按 X 汇总/细分"是 `total` 加 `group_by: X`，不是 `list`。
2. 时间是筛选条件，不是分组："这周" → `period`。但"按月细分" → `group_by: "month"`。
3. "逾期" → `status: "overdue"`；只有给出天数时才加 `days_overdue`。
4. chat / about_me / general / advice，以及与钱无关的 lookup，使用 `direction: "none"`。
5. 每个键都必须出现。空字符串用 `""`，没有筛选条件用 `{}`。
6. 只用虚构的名字、公司和金额。不要使用真实个人信息。
7. 不要只换名字重复同一个句式。措辞、长度和语气要多样。

### 示例
{"source": "deepseek", "lang": "zh", "q": "上个月还有谁没给钱", "history": [], "form": {"intent": "list", "direction": "in", "group_by": "", "filters": {"status": "unpaid", "period": "last_month"}, "subject": "未付款客户"}}
{"source": "deepseek", "lang": "zh", "q": "每个客户欠多少，分开列一下", "history": [], "form": {"intent": "total", "direction": "in", "group_by": "customer", "filters": {"status": "unpaid"}, "subject": "客户欠款"}}
{"source": "deepseek", "lang": "zh", "q": "这周要付的账单超过500的有哪些", "history": [], "form": {"intent": "list", "direction": "out", "group_by": "", "filters": {"period": "this_week", "date_field": "due", "amount": ">500"}, "subject": "待付账单"}}
{"source": "deepseek", "lang": "zh", "q": "第二季度是赚了还是亏了", "history": [], "form": {"intent": "compare", "direction": "net", "group_by": "", "filters": {"period": "2026-Q2"}, "subject": "利润"}}
{"source": "deepseek", "lang": "zh", "q": "你是用什么模型做的", "history": [], "form": {"intent": "about_me", "direction": "none", "group_by": "", "filters": {}, "subject": "模型"}}
{"source": "deepseek", "lang": "zh", "q": "哈哈你好笨", "history": [], "form": {"intent": "chat", "direction": "none", "group_by": "", "filters": {}, "subject": ""}}

### 本批次
为以下内容写 **100** 条示例：BATCH

---

## Batch plan / 批次计划 (replace BATCH with one line per run / 每次运行替换一行)

1. English: money owed TO the business (intent total/list/count/rank), many filter combinations
2. English: money the business PAYS (bills, suppliers, expenses, wages), many filter combinations
3. English: compare and net questions (profit, cash flow, this vs last period, A vs B)
4. English: rank questions (top/bottom customers, suppliers, products, expenses)
5. English: lookups of single facts from documents (dates, names, terms, prices, renewal dates)
6. English: charts, trends and stats (sales, costs, stock prices, seasonality)
7. English: chat and about_me (greetings, thanks, jokes, insults, "who made you", "what can you do")
8. English: general knowledge and advice (world facts, tax concepts explained, "should I...")
9. English: messy messages across ALL intents (typos, slang, no punctuation, 1-3 word messages, rambling)
10. English: follow-ups that need `history` across all intents
11. 中文：与钱有关的问题（in/out/net，total/list/count/rank/compare，各种筛选条件）
12. 中文：查找、图表、趋势、统计
13. 中文：闲聊、about_me、常识、建议
14. 中文：口语化、错别字、很短的消息，涵盖所有意图
15. English + 中文 mixed: Australian small business wording (GST, BAS, tradies, invoices from suppliers, super, payroll), all intents
