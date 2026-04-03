---
name: startup-tracker
description: |
  Monitor startups for weekly signals — funding rounds, product launches, partnerships, leadership hires,
  and other notable developments — across news, websites, and social media.
  监控初创公司动态——融资轮次、产品发布、合作伙伴、高管招聘等新闻和社交媒体信号。
  Designed for VC investors, analysts, or researchers tracking early-stage companies.

  **ACTIVELY use this skill whenever the user mentions:**
  "run startup tracker", "check startup updates", "monitor companies", "追踪公司动态",
  "/startup-tracker", "startup weekly", "监控公司", "startup监控", "startup weekly",
  generating startup monitoring reports, "追踪新创公司", "监控新创", or anything similar.

  **Do NOT wait to be asked twice.** If the user's request involves tracking, monitoring, or reporting on
  multiple companies' recent activity, this skill should be used immediately.
---

# Startup Tracker — 完整版执行指南

## 架构概览

```
┌─────────────────────────────────────────────────────┐
│                    Agent (你)                        │
│  编排全部 4 个数据源 + 去重 + 报告生成                │
├───────┬──────────┬──────────┬───────────────────────┤
│ ①新闻  │ ②网站    │ ③Twitter  │ ④LinkedIn            │
│ Tavily │ Crawl4AI │ apify-   │ apify-               │
│ CLI    │ (Python) │ ultimate │ ultimate             │
│        │          │ -scraper │ -scraper             │
│        │          │ skill    │ skill                │
└───────┴──────────┴──────────┴───────────────────────┘
         ↓              ↓          ↓          ↓
┌─────────────────────────────────────────────────────┐
│           state/new_items.json (统一输出)             │
└─────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────┐
│          最终Markdown报告 (输出给用户)                 │
└─────────────────────────────────────────────────────┘
```

**关键设计决策**：
- `tracker.py` 仅负责 ①Tavily 新闻搜索 + ②Crawl4AI 网站监控
- ③Twitter 和 ④LinkedIn 由 Agent 直接调用 `apify-ultimate-scraper` skill 完成
- Agent 负责将 4 个数据源的输出**合并、去重、分类**，写入 `state/new_items.json`，然后生成报告

---

## 技术栈与环境

| 组件 | 用途 | 安装方式 | 必须/可选 |
|------|------|---------|----------|
| `tvly` CLI (Tavily) | 新闻/融资搜索 | `pip install tavily-python` + API key | 必须 |
| `crawl4ai` | 网站内容变更检测 | `pip install crawl4ai`（首次运行可能需 1-2 分钟下载模型） | 可选 |
| `firecrawl` | 网站内容爬取（替代 Crawl4AI） | 仅需 API key，无需安装：https://www.firecrawl.dev/pricing | 可选 |

**网站监控选型建议**：
- Crawl4AI：免费、本地运行、无需网络请求，但首次安装较慢（需下载 ~100MB 模型文件）
- Firecrawl：云端运行、安装即走、**免费额度 500 credits 为一次性**（用完即止），无订阅则不会刷新；如需继续使用需购买 Credits
- 建议：短期测试可用 Firecrawl 快速上手，长期监控优先 Crawl4AI

**网站监控说明**：
- 默认仅监控公司 `website` 字段指定的首页URL。但首页内容变动较少，**强烈建议在 `monitor_urls` 中同时加入博客页（`/blog`）和新闻页（`/news`）URL**，否则会遗漏大部分产品公告和融资新闻
- `monitor_urls` 需要用户手动提供，因为 AI 自动查找博客/博客页 URL 的准确率不稳定，且不同公司使用不同路径结构（如 `/blog`、`/news`、`/updates` 等）

| `@apify/mcpc` CLI | 运行 Apify Actor | `npm install -g @apify/mcpc` | 必须（社交媒体） |
| `apify-ultimate-scraper` | Apify CLI wrapper skill | 已安装在 `~/.claude/skills/apify-ultimate-scraper/` | 必须（社交媒体） |
| `python3` + `requests` + `dotenv` | tracker.py 运行时 | 已配置 | 必须 |

**API Keys 加载优先级**（从高到低）：
1. 命令行参数（`--tavily-key`, `--apify-key`）
2. `.env` 环境变量（`Track_skill/.env` 中的 `TAVILY_API_KEY`, `APIFY_TOKEN`）
3. `config.json` → `api_keys` 字段

---

## 配置文件详解

### config.json 结构

```json
{
  "companies": [
    {
      "name": "公司显示名",
      "website": "官网首页URL（完整https://，例如 https://openai.com）",
      "monitor_urls": ["官网首页URL", "博客页URL如https://xxx.com/blog", "新闻页URL（可选）"],
      "x_handle": "Twitter/X 账号用户名（@ 后面的部分，如 https://x.com/OpenAI 则填 OpenAI）",
      "linkedin_url": "LinkedIn公司页面完整URL",
      "priority": "high | medium | low",
      "exclude_keywords": ["需过滤的关键词列表"]
    }
  ],
  "data_sources": {
    "news": "tavily",
    "website": "crawl4ai",
    "twitter": true,
    "linkedin": true
  },
  "website_monitor": {
    "engine": "crawl4ai",
    "max_content_chars": 2000,
    "use_article_signature": true
  },
  "tavily": {
    "search_days_back": 7,
    "max_results_per_company": 15,
    "search_query": "funding OR raised OR acquired OR acquisition OR merger OR IPO OR partnership OR launch OR expansion OR investment OR product OR hiring or series",
    "major_keywords": ["funding","raises","raised","million","acquired","acquisition","IPO","series A","series B","series C","seed round","partnership","launch","shut down","layoff","appointed CEO","appointed CTO"]
  },
  "apify": {
    "twitter_actor_id": "parseforge/x-com-scraper",
    "linkedin_actor_id": "supreme_coder/linkedin-post",
    "max_tweets_per_run": 15,
    "max_linkedin_posts_per_run": 15,
    "poll_interval_sec": 5,
    "max_poll_sec": 120
  },
  "api_keys": {
    "tavily": "tvly-...",
    "apify": "apify_api_...",
    "firecrawl": ""
  }
}
```

### 配置路径

- **配置文件**：`<skill-directory>/config.json`
- **状态目录**：`<skill-directory>/state/`
- **输出文件**：`<skill-directory>/state/new_items.json`
- **脚本路径**：`<skill-directory>/tracker.py`

**Crawl4AI 首次运行说明**：
首次运行 Crawl4AI 只会建立网站的基线 hash，不会产生变更告警。从第二次运行开始才会真正检测网站内容变更。

---

## 第一次使用（交互式配置引导）

> ⏱️ **初次配置说明**：首次配置需要手动提供公司信息和 API Key，Agent 会引导你逐步完成。
> 配置文件一旦建立，后续运行将自动加载配置，无需重复设置。

当 `config.json` 不存在时，Agent 用亲切温和的语气按以下步骤引导：

### Step 0：说明为何需要手动提供链接

用亲切自然的语气告知用户：
- AI 领域同名公司极多（例如叫 "Nova AI" 的可能有十几家），自动搜索容易张冠李戴
- AI 自动查找博客/新闻页面的准确率不稳定，可能遗漏重要更新
- 请用户亲自查验并提供链接，可以确保监控的是他们真正想关注的公司

> 语示例："因为 AI 领域同名公司很多，为了保证监控的准确性，需要你提供一下官网和博客页链接哦～"

### 交互语气指南

- **全程保持亲切温和的语气**，像在和朋友聊天，而不是机械地汇报
- 配置引导时用"请"/"麻烦"/"我来帮你"等表达，避免生硬的指令
- 当结果为 0 条时，用轻松的语气解释原因，而非冰冷的错误提示
- 展示报告时开头可以用"这周帮你跑了下监控，情况如下～"这类自然对话式的开场

### Step 1：收集公司名称
向用户询问要监控的公司列表（名称、官网、Twitter handle、LinkedIn URL）。

### Step 2：搜索并确认公司信息
使用 `tavily-search` skill 或 `WebSearch` 搜索每个公司的官网、Twitter、LinkedIn 链接，展示给用户确认。

### Step 3：配置 API Keys
引导用户配置：
- **Tavily API Key**（必需）：获取地址 https://tavily.com
- **Apify Token**（社交媒体监控必需）：获取地址 https://console.apify.com/account/integrations

写入 `Track_skill/.env` 文件或 `config.json`。

### Step 4：保存 config.json 并运行首次监控
按以下"运行监控"章节的流程执行。

---

## 运行监控 — 完整执行流程

当用户要求运行 startup tracker 时，Agent 必须按以下顺序逐步执行：

### Phase 0：前置检查

```
1. 确认 config.json 存在
2. 确认 company 列表非空
3. 确认 API Keys（Tavily 用于新闻，Apify 用于社交媒体）
4. 确认 Node.js 已安装（apify skill 依赖）
5. 确认 @apify/mcpc 已全局安装
```

如果任何检查失败，告知用户并引导修复。

### Phase 1：Tavily 新闻搜索 + Crawl4AI 网站监控

执行 `tracker.py`，它会自动完成：
- 对每个公司使用带有 `company name` 的精确搜索词调用 Tavily API
- 如果有 Crawl4AI，抓取每个公司官网的内容变更

```bash
cd <skill-directory>
python tracker.py
```

执行结果会自动保存到 `state/new_items.json`。

### Phase 2：Twitter/X 监控（通过 apify-ultimate-scraper skill）

> **Twitter/X 模块说明**：该模块可以使用，但由于 X（原 Twitter）的反爬机制非常强，目前依赖的单个 Apify Actor 有时会出现信息过时、过滤有误或结果不完整的情况。后续会推出一系列经过逐一验证和筛选的 Actor 池，通过多 Actor 互补交叉来提升稳定性和数据完整性，而非简单堆叠。如遇到数据异常请稍微等待更新。

**重要：不要调用 tracker.py 中的 Apify 函数（已禁用），必须通过 Apify actor API 调用。**

#### 推荐 Actor：`parseforge/x-com-scraper`（已实测通过 ✅）

描述："Extract up to 100 tweets from any X.com (Twitter) user. No login required."

对 `config.json` 中每个配置了 `x_handle` 的公司，使用 `run_actor.js` 脚本直接调用：

```bash
export APIFY_TOKEN=<token>
node --env-file=.env <apify-skill-dir>/reference/scripts/run_actor.js \
  --actor "parseforge/x-com-scraper" \
  --input '{"usernames": ["<x_handle>"], "maxItems": 15}' \
  --timeout 120
```

**关键参数**（经实测验证）：
- `usernames` — **数组**，不包含 @，如 `["OpenAI"]`
- `maxItems` — 最多抓取条数

**备用 Actor（如主 Actor 不可用）**：
- `logical_scrapers/x-twitter-user-profile-tweets-scraper` — 参数: `{"username": ["<x_handle>"], "maxTweets": 15}`

**Actor 稳定性保障**：
- Actor 提交到 Apify 云端执行，返回 `runId` + `datasetId`
- `run_actor.js` 内置 polling 逻辑（每 5 秒查一次状态，超时 600 秒默认）
- Actor 失败后（状态为 FAILED/ABORTED/TIMED-OUT），立即检查 [Apify Console](https://console.apify.com/actors/runs/<runId>) 的错误详情
- 如果特定 Actor 持续失败，切换到备用 Actor 并重试

**输出解析**：
```
结果字段映射：
- id / url / twitterUrl → unique ID + URL
- fullText / text → 内容
- createdAt → published_date（ISO 格式）
- retweetCount, likeCount → 判断互动量
- isRetweet, isQuote → 判断类型
```

### Phase 3：LinkedIn 监控（通过 apify-ultimate-scraper skill）

> **LinkedIn 模块说明**：该模块可以使用，但由于 LinkedIn 的反爬机制非常强，目前依赖的单个 Apify Actor（supreme_coder/linkedin-post）有时会出现信息过时、过滤有误或结果不完整的情况。后续会推出一系列经过逐一验证和筛选的 Actor 池，通过多 Actor 互补交叉来提升稳定性和数据完整性，而非简单堆叠。如遇到数据异常请稍微等待更新。

#### 推荐 Actor：`supreme_coder/linkedin-post`（已验证可用）

对 `config.json` 中每个配置了 `linkedin_url` 的公司，使用 `run_actor.js`：

```bash
export APIFY_TOKEN=<token>
node --env-file=.env <apify-skill-dir>/reference/scripts/run_actor.js \
  --actor "supreme_coder/linkedin-post" \
  --input '{"urls": ["<linkedin_url>"], "limit": 15}' \
  --timeout 120
```

**正确输入参数**（经实测验证）：
- 字段名 `urls`（字符串数组），不是 `startUrls`

**输出解析**：
```
结果字段映射：
- urn → unique ID
- url → URL
- text → 内容
- timeSincePosted → 相对时间（如 "3w"），需转换为 YYYY-MM-DD
- postedAtISO → ISO 格式的绝对时间（如果存在）
- authorName, numLikes, numShares, numComments → 互动数据
```
调用 `tracker.py` 中的 `relative_time_to_date()` 将 "3w"、"2mo" 等转换为 YYYY-MM-DD

### Phase 4：统一去重与合并

将 Phase 1-3 的所有结果合并为统一的 JSON 数组，写入 `state/new_items.json`。

**Apify 结果解析说明**：
- Twitter/LinkedIn Actor 通过 `run_actor.js` 返回 stdout，格式为 `status` 行 + JSON 结果
- Agent 需要手动解析 stdout 输出：找到最后一行 JSON 数组（以 `[` 开头的行）
- 如果 stdout 中包含多行 JSON，取最后一条完整的 JSON 数组进行解析

**输出格式** (`state/new_items.json`)：
```json
{
  "items": [
    {
      "company": "公司名",
      "source": "tavily | crawl4ai | apify_twitter | apify_linkedin",
      "title": "动态标题",
      "url": "链接URL",
      "snippet": "内容摘要（前300字符）",
      "published_date": "YYYY-MM-DD",
      "importance": "MAJOR | NORMAL"
    }
  ],
  "run_timestamp": "ISO-8601时间戳",
  "summary": {
    "total": 总数,
    "by_company": {"公司A": N, "公司B": M},
    "by_source": {"tavily": N, "crawl4ai": M, "apify_twitter": X, "apify_linkedin": Y},
    "by_importance": {"MAJOR": N, "NORMAL": M}
  }
}
```

### Phase 5：生成报告并保存

读取 `state/new_items.json`，按以下"报告格式"章节生成 Markdown 报告。

**报告必须同时输出两件事**：
1. 将完整报告**保存为 Markdown 文件**到 `<skill-directory>/reports/` 目录下，文件名格式为 `report_YYYY-MM-DD.md`（如 `report_2026-04-03.md`）
2. **在对话中向用户展示报告摘要**（MAJOR 动态逐条展示，NORMAL 动态整合为摘要），内容充实丰富但不用像存档文件那样每条都附带链接和完整字段，保持适中篇幅即可。语气亲切自然，像是在跟朋友分享一周的发现，而非机械汇报。展示完后注明存档文件的完整路径供后续查阅。

---

## Agent 判断指南 — 社交媒体噪音过滤

公司社交媒体（Twitter/LinkedIn）通常噪音极高，必须过滤后呈现。**Agent 在合并 Phase 2-3 结果时需按以下规则判断每条内容：**

### 直接过滤（不呈现给用户）的帖子类型：
- 常规招聘帖（"We're hiring!" 无具体职位/人数信息）
- 产品推广/广告（"Try our amazing new feature!" 无重大产品发布）
- 节日祝福/日常社交（"Happy Halloween from our team!"）
- 纯转发（isRetweet=true 且无额外评论）
- "Great article!" 类转帖
- 会议出席预告（"See you at conference X"）

### 需要呈现的帖子类型：
- **核心人事变动**：CEO/CTO/VP 级别入职或离职
- **重大产品发布**：正式发布（GA）、重大功能更新（非"minor tweak"）
- **融资公告**：金额可查的融资轮次
- **战略合作**：与知名公司/机构的合作
- **数据里程碑**：用户数、收入、融资额等关键指标
- **媒体采访**：创始人接受知名媒体采访谈论公司方向

### NORMAL 级但需要汇总的帖子类型：
- 博客文章链接
- 技术分享/演讲
- 一般性招聘帖（含具体职位和 JD 的）
- 官网更新通知
- 产品迭代进展

**Agent 在生成报告时，应将上述 NORMAL 级内容整合为"本周信号摘要"（1-2 段自然语言），而非逐条罗列。**

---

## 首次配置 — 全流程指南

当用户首次运行 startup-tracker（config.json 不存在时），Agent 按以下步骤完成全部配置：

### Step 1：获取公司名称
询问用户要监控的公司列表。至少需要：**公司名**和**官网**。
可选：Twitter handle、LinkedIn URL

### Step 2：验证与补全公司信息
对每家公司，使用 `tavily-search` skill 或 `WebSearch` 验证：
- 官网是否正确可访问
- Twitter/X handle 是否正确
- LinkedIn 公司页面是否正确

### Step 3：引导 API 配置
**Tavily API（必需）**：
- 注册地址：https://tavily.com
- 免费额度：1,000 次搜索/月
- 配置方式：将 API Key 写入 `<skill-directory>/.env` 或引导用户创建文件

**Apify Token（仅社交媒体需要）**：
- 注册地址：https://console.apify.com/account/integrations
- 免费额度：$5 额度/月
- **网络限制说明**：中国大陆用户访问 `api.apify.com` 可能被网络拦截。如验证失败，需使用代理或 VPN。
- 配置方式：将 Token 写入 `.env`

**网站监控二选一**：
- Firecrawl：注册 https://www.firecrawl.dev，**免费额度 500 credits 为一次性**（注册即送，用完不会自动刷新），超出需购买 Credits
- Crawl4AI：`pip install crawl4ai`，完全免费，无使用次数限制

### Step 4：确认并生成 config.json
将公司信息整合为 config.json 格式，展示给用户确认后保存。

### Step 5：运行首次监控
执行 `python tracker.py`，展示结果，并解释：
- Crawl4AI 首次运行只建立网站基线 hash，不产生变更告警，从第二次运行才开始检测
- "Example AI" 这类测试公司无新闻属正常（无实际业务的公司不会有媒体报道）
- 0 条动态可能意味着：公司太早期、监控窗口（7 天）太短、公司处于静默期

---

## 去重规则

在合并各数据源结果时，Agent 必须执行以下去重：

1. **URL 去重**：相同 URL 的内容只保留一次（以 URL 为唯一键）
2. **内容去重**：同一天、同一公司的相似内容（标题相似度 > 80%）合并为一条
3. **跨源优先级**：若同一动态出现在多个数据源，优先保留信息量最大的那条（MAJOR > NORMAL），并在标题后标注来源标签 `[Tavily + Twitter]`

---

## 重要性分类

Agent 合并结果时必须根据内容判断 `importance`：

**MAJOR（标记为 🔴）**：
- 融资/投资轮次（关键词：funding, raised, series, million, acquired, IPO）
- 重大产品发布/launch
- 战略合作伙伴公告
- 核心高管招聘/离职

**NORMAL（标记为 ⚪）**：
- 日常产品更新
- 博客/新闻稿更新
- 社交媒体常规帖子
- 团队扩张公告

---

## 报告格式与服务目标

### 报告定位

**目标受众**：VC 投资人、竞品分析师、行业研究员
**核心价值**：让读者 3 分钟内了解 N 家被监控公司过去一周的关键动态，并辅助判断是否需要进一步尽调
**生成原则**：
- 信息密度优先：每条动态不超过 3 行摘要
- 行动导向：MAJOR 动态后附加"值得关注点"（如融资规模、竞争对手影响）
- 小新闻不遗漏：NORMAL 级别的内容也要呈现，但整合为"本周信号摘要"而非逐条罗列

```markdown
# Startup Tracker — 信号报告

**报告日期**: YYYY-MM-DD
**监控公司**: N 家
**数据源**: Tavily 新闻 | Crawl4AI 网站监控 | Apify Twitter | Apify LinkedIn
**时间范围**: 过去 7 天

---

## 总览

| 公司 | 重要动态 | 常规动态 | 关键信号 |
|------|---------|---------|---------|
| 公司A | 🔴 1条 | ⚪ 3条 | 正在招聘 CTO |
| 公司B | 🔴 0条 | ⚪ 1条 | 本周安静 |

---

## [公司A名称]
- **官网**: https://...
- **Twitter**: @handle
- **LinkedIn**: linkedin.com/company/...

### 🔴 重要动态

#### 1. [Tavily] 融资标题
- **发布日期**: YYYY-MM-DD
- **来源**: TechCrunch
- **链接**: [查看详情](URL)
- **摘要**: 2-3 句总结事件核心信息
- **值得关注**: 为什么这件事重要（1 行）

### ⚪ 本周信号摘要

> 将本周所有 NORMAL 级别动态 + 社交媒体帖子**整合为 1-2 段自然语言摘要**，
> 而非逐条罗列。例如：
>
> "本周 Example AI 在 Twitter 上发布了 3 条帖子，主要是产品介绍和招聘广告。
> 官网博客更新了一篇技术文章，介绍了他们的 LLM 微调方案。LinkedIn 上
> 新增了 VP of Engineering 职位。整体来看公司处于技术建设期，产品尚未
> 正式发布。"

---

## 无动态公司

以下公司在过去 7 天内无可见信号：
- 公司B（可能仍为早期阶段，或社交媒体未活跃）
```

**报告生成硬性规则**：
1. **MAJOR 优先**：必须逐条展示，每条附加"值得关注"（1 行解释为什么重要）
2. **NORMAL 整合**：同一公司的 NORMAL 动态必须整合为一段自然语言"信号摘要"，禁止逐条罗列
3. **社交媒体噪音过滤**：Twitter/LinkedIn 的常规帖子（如产品广告、节日祝福、"We're hiring"常规帖）不单独呈现，仅作为信号摘要的背景信息提及。除非内容包含重大事件（核心人事变动、重大产品发布、融资），才提入 MAJOR 或单独提及
4. **无动态也要列**：归入"无动态公司"区域
5. **数据源标签**：[Tavily]、[Website]、[Twitter]、[LinkedIn]
6. 如果某个数据源因配置问题被跳过，在报告开头注明"⚠️ 数据源跳过说明"
7. 如果所有公司都无动态，报告必须包含："本周无可见信号——可能原因：监控公司为早期阶段/监控时间窗口（7 天）较短/公司处于静默期"

---

## 管理配置（对话式命令）

| 用户指令 | Agent 操作 |
|---------|-----------|
| "添加公司" | 询问公司名称 → 搜索信息 → 确认 → 更新 config.json |
| "删除公司" | 列出当前公司 → 用户选择 → 更新 config.json |
| "修改公司信息" | 列出公司 → 用户选择 → 询问新信息 → 更新 config.json |
| "查看当前配置" | 读取并格式化展示 config.json |
| "更新 API Key" | 询问哪个 key → 验证 → 保存至 .env |
| "切换数据源" | 询问启用/禁用哪些数据源 → 更新 config.json |
| "查看状态" | 运行 `python tracker.py --validate` 并展示结果 |
| "重置历史" | 清空 `state/` 目录下的状态文件 |

---

## 故障排除

| 问题 | 解决方案 |
|------|---------|
| "Tavily API Key 无效" | 引导用户重新获取有效 key，写入 .env |
| "Apify 运行失败" | 检查 `APIFY_TOKEN` 环境变量；确认 `mcpc` 已全局安装 (`npm ls -g \| grep mcpc`) |
| "Apify Actor 超时" | Actor 可能在排队，检查 Apify Console 的运行状态链接；尝试增加 `--timeout` 参数 |
| "某家公司返回 0 结果" | 早期公司可能没有媒体报道，属正常行为；可检查网站监控源是否有变更 |
| "Crawl4AI 未安装" | 运行 `pip install crawl4ai`，或告知用户可跳过（仅网站监控不可用） |
| "tracker.py 执行报错" | 使用 `python tracker.py --validate` 诊断配置；检查 Python 版本 ≥ 3.8 |
| "SSL 网络连接失败" | 中国大陆访问 `api.apify.com` 可能受限，使用代理或检查网络设置 |

---

## 设计原则

1. **零手动配置**：所有设置通过对话完成，Agent 自动搜索公司信息
2. **混合数据源架构**：Python 脚本处理新闻/网站，Agent skill 处理社交媒体
3. **渐进式启用**：先运行基础功能（新闻+网站），可选功能（Twitter/LinkedIn）后续按需启用
4. **即时反馈**：配置后立即运行并展示结果
5. **透明可控**：每个配置步骤都展示给用户确认
6. **状态持久化**：通过 `state/` 目录下的 JSON 文件追踪已处理项目，避免重复报告

---

## 定时运行

```
Agent: 设置每周自动运行
```

**注意**：定时运行前请确保 `config.json` 已完整配置且 API Keys 有效。