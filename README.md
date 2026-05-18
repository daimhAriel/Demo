# eVTOL 情报监控系统

自动爬取全球 eVTOL（电动垂直起降飞行器）企业官网及垂类媒体新闻，经 LLM 智能分类、公司归属、标题清洗后，通过仪表盘可视化展示情报全景。

---

## 总体架构

```
用户访问 → Streamlit 面板 (8501)
                │
          PipelineOrchestrator (调度引擎)
           ┌─────┼─────┐
        爬虫引擎  LLM  通知
           │           ├─ 飞书群机器人
     ┌─────┴─────┐     └─ 邮件 SMTP
   静态(BS4)  动态(Playwright)
           │
       SQLite 数据库 ← 存储所有情报和配置
```

---

## 模块说明

### `core/` — 引擎核心

| 文件 | 职责 |
|---|---|
| **`crawler.py`** | 爬虫引擎。静态模式用 `BeautifulSoup` 解析 HTML，动态模式用 `Playwright` 渲染 JS 页面。支持分页（URL参数翻页 / 点击翻页）、详情页跟进、锚点去重 |
| **`llm.py`** | LLM 处理层。调用 DeepSeek API（兼容 OpenAI SDK），对情报进行摘要生成、分类、标题清洗、公司归属。非 JSON 返回时自动跳过 |
| **`pipeline.py`** | 调度编排引擎。串联"爬取 → LLM 处理 → 入库 → 推送通知"全流程。公司归属采用分层策略：公司站点直映射，媒体站点关键词多匹配 + LLM 兜底 |
| **`classifier.py`** | 关键词分类器（方案A）。当站点无 `topic` 配置时，用关键词矩阵对情报进行快速分类，作为 LLM 不可用时的降级方案 |
| **`database.py`** | 数据库层。SQLite 增删改查、指纹去重（SHA256）、时间感知统计、趋势分组查询 |
| **`scheduler.py`** | APScheduler 定时调度器。嵌入 Streamlit 后台线程，支持 cron 表达式配置，自动记录最近执行结果 |
| **`notifier.py`** | 推送通知引擎。新情报入库时自动推送，支持飞书群机器人（消息卡片）和邮件 SMTP |

### `app/` — Streamlit 管理面板

| 文件 | 职责 |
|---|---|
| **`app.py`** | 主面板。4 个页面：仪表盘（KPI + 趋势图 + 分类分布 + 公司声量）、站点管理（增删改 + 选择器探测）、情报浏览（三轴筛选 + 编辑 + 饼图）、系统设置（LLM / 调度 / 公司列表 / 推送通知） |
| `components/preview.py` | 站点解析预览组件 |
| `components/selector_prober.py` | CSS 选择器自动探测工具 |

### 根目录文件

| 文件 | 职责 |
|---|---|
| **`cli.py`** | 命令行工具。`python cli.py run` 手动爬取，`python cli.py reprocess` LLM 回填，`python cli.py list` 查看情报等 |
| **`models.py`** | 数据模型：SiteConfig（站点配置）、RawPage（原始页面）、IntelItem（情报条目） |
| **`config.py`** | 环境变量配置（API Key 等，面板系统设置优先级更高） |
| **`Dockerfile`** | Docker 镜像构建 |
| **`docker-compose.yml`** | 一键部署编排 |
| **`requirements.txt`** | Python 依赖 |

---

## 部署后需要手动配置

### 1. LLM API Key（必须）
```
系统设置 → LLM 配置 → 填入 DeepSeek API Key
```
推荐模型：`deepseek-chat`，Base URL：`https://api.deepseek.com/v1`

没有配置时，爬虫仍可爬取，但不会生成摘要、分类和公司归属。

### 2. 定时调度（可选）
```
系统设置 → 定时调度 → 启用 + 选择频率
```
默认 `0 0 * * 0`（周一 00:00），可在面板直观选择。

### 3. 推送通知（可选）
```
系统设置 → 推送通知 → 配置飞书 Webhook 或邮件 SMTP
```
新情报入库时自动推送。

### 4. 公司列表（可选）
```
系统设置 → 公司管理 → 增删改公司 + 关键词 + 站点映射
```
影响情报浏览的公司筛选和图表中的公司声量统计。

---

## 输出结果

### 数据库（data/intel.db）

每条情报包含以下字段：

| 字段 | 说明 | 来源 |
|---|---|---|
| `raw_title` | 原始标题 | 页面抓取 |
| `clean_title` | LLM 清洗后的标题 | LLM 处理 |
| `summary` | 中文摘要（一句话） | LLM 处理 |
| `category` | 分类标签 | LLM / 关键词 |
| `company` | 所属公司（ID或CSV列表） | 关键词匹配 / LLM |
| `event_time` | 事件时间 | 页面提取 / LLM |
| `source_url` | 原文链接 | — |
| `site_id` | 来源站点 ID | — |
| `pushed` | 是否已推送 | — |

### 分类体系（6类）

| 分类 | 说明 |
|---|---|
| 产品与工程 | 技术参数、原型机、适航取证进展 |
| 商业化情况 | 订单、航线、合作、运营 |
| 战略目标 | 公司战略、融资计划、业务布局 |
| 股权与资本 | 融资、上市、投资 |
| 适航情况 | 适航认证、监管批准 |
| 产能规划 | 工厂建设、产能扩张 |

### 公司归属（可配置，默认10家）

Joby Aviation、Archer Aviation、Eve Air Mobility、Wisk Aero、BETA Technologies、峰飞航空、时的科技、沃飞长空、零重力飞机、Volant Aerotech 等，支持用户增删改。

---

## 快速部署

```bash
# 1. 克隆项目
git clone <仓库地址>
cd intel-monitor

# 2. 启动
docker compose up -d

# 3. 打开 http://服务器IP:8501
# 4. 系统设置 → 填入 DeepSeek API Key
```

---

## CLI 命令

| 命令 | 用途 |
|---|---|
| `python cli.py run` | 手动爬取全部站点 |
| `python cli.py run -s <site_id>` | 爬取指定站点 |
| `python cli.py reprocess` | LLM 回填已有情报 |
| `python cli.py reprocess -f` | 强制全部重做 |
| `python cli.py list` | 列出所有站点 |
| `python cli.py stats` | 情报统计 |
| `python cli.py last -n 10` | 最近 10 条情报 |
