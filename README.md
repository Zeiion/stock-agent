# Stock Agent · 个人盯盘 + 多智能体 AI 决策 + 轻量量化

> 个人用的 **美股 / A 股 (CN) / 港股 (HK)** 实时盯盘工具，集成 **AI 决策建议**、**多智能体深度分析**、**消息面聚合**、**模拟组合分析**、**AI 战绩回测**、**选股筛选** 与**轻量量化（指标 + 回测 + 模拟盘）**，配套**多渠道推送**。
> 由 **Claude Code & Codex CLI** 驱动 —— 直接复用你本机已登录的 CLI，**无需 API key 也能跑**。
>
> ⚠️ **signal + paper only**：本工具只产出**信号**与**模拟盘（paper）**记录，**不会**自动下真实订单。
> ⚠️ **本工具仅供学习与研究，不构成任何投资建议。** 详见文末[免责声明](#免责声明)。

---

## 项目简介

Stock Agent 是一个跑在本机的个人量化盯盘助手，目标是把「盯盘 → 触发提醒 → AI 给判断 → 人工确认 → 记一笔模拟单 → 事后复盘战绩」这条链路自动化，覆盖美股、A 股、港股三个市场。

核心理念：

- **本地优先、零门槛起步**：默认数据走 `yfinance`（全市场，延迟报价）+ `akshare`（A 股近实时、免 key），AI 走本机 `claude` / `codex` CLI，推送走 macOS 桌面通知。**不配任何 key 即可开箱运行。**
- **可渐进增强**：配上 `ANTHROPIC_API_KEY` / `FINNHUB_API_KEY` / Bark / Telegram / 飞书 等，解锁更快的 AI、美股实时数据与手机推送。
- **AI 决策三档可切换**：Anthropic API / `claude -p` CLI / `codex exec` CLI，运行时一键切换。
- **从单点判断走向多智能体**：除了「快速分析」给出单一决策，还提供「深度分析」—— 技术面 / 基本面·估值 / 消息面·情绪 三位专家分析师并行研判，再由首席决策官做多空辩论式综合。
- **闭环可复盘**：决策、模拟单、净值快照全部落库；事后用真实价格回测 AI 方向准确率与买入信号 alpha。
- **安全第一**：默认 `signal` 模式（只出信号），模拟盘需人工确认，带持仓上限与 kill-switch。

---

## 功能总览

| 功能 | 模块 | 主要 Endpoint | 前端标签/入口 |
| --- | --- | --- | --- |
| 实时盯盘 / 行情 | `datahub.py` `daemon.py` | `GET /api/quotes` `GET /api/quote/{symbol}` `GET /api/history/{symbol}` | 中央行情面板 |
| 自选股名称搜索 | `search.py` | `GET /api/search` | 自选股添加框（中文名/英文名/代码下拉） |
| 技术指标快照 | `indicators.py` | `GET /api/indicators/{symbol}` | 中央指标区 |
| 盯盘规则引擎 | `rules.py` | `GET/POST /api/rules` | 「规则」标签 |
| 提醒/警报 | `rules.py` `bus.py` | `GET /api/alerts` `GET /api/stream` (SSE) | 「提醒」标签 |
| AI 快速决策 | `ai/brain.py` | `POST /api/analyze/{symbol}` | 「AI 决策」面板「快速分析」 |
| **多智能体深度分析** | `deepanalysis.py` | `POST /api/deep-analyze/{symbol}` | 「AI 决策」面板「🔬 深度分析」按钮 |
| 决策历史 | `db.py` | `GET /api/decisions` | 「AI 决策」面板 |
| **新闻/消息面** | `news.py` | `GET /api/news/{symbol}` | 「新闻」标签（同时接入 AI 上下文） |
| 模拟盘下单/审批 | `paper.py` | `GET/POST /api/orders`、`/api/positions` | 「持仓/订单」标签 |
| **模拟组合分析** | `analytics.py` | `GET /api/portfolio`、`/api/portfolio/history`、`/api/realized` | 「组合」标签 |
| **AI 决策战绩** | `reflection.py` | `GET /api/track-record` | 「战绩」标签 |
| **选股筛选器** | `screener.py` | `POST /api/screen`、`GET /api/screen/fields` | 「选股」标签 |
| 回测 | `backtest.py` | `POST /api/backtest` | 「回测」标签 |
| 设置（运行时切档） | `config.py` `main.py` | `GET/POST /api/settings` | 顶栏设置 |
| 推送测试 | `daemon.py`（notifier） | `POST /api/test-notify` | 顶栏设置 |

---

## 架构图

```
                          ┌──────────────────────────────────────────────┐
                          │                  FastAPI app                   │
                          │   REST  /api/*      +      SSE  /api/stream     │
                          │        （并托管已构建的 React 前端）            │
                          └───────────────┬────────────────────────────────┘
                                          │ lifespan: start/stop
                                          ▼
   ┌──────────────────────────────  Daemon (后台轮询循环)  ───────────────────────────────┐
   │                                                                                        │
   │  poll_once()  ── 盘中 POLL_INTERVAL_S / 盘后 POLL_INTERVAL_OFFHOURS_S 节流              │
   │      │            每轮收尾 snapshot_nav() 写一笔净值历史 (analytics.py)                 │
   │      ▼                                                                                 │
   │  ┌────────────┐   per-market 适配器 + 兜底链                                            │
   │  │  DataHub   │── US: finnhub(有key实时) → yfinance(准实时)                              │
   │  │            │── CN: akshare(近实时,免key) → yfinance(延迟)                            │
   │  │            │── HK: akshare(~15min) → yfinance(~15min)                                │
   │  └─────┬──────┘   统一归一化为 Quote / OHLCV(DataFrame)                                 │
   │        │                                                                               │
   │        ▼                                                                               │
   │  ┌────────────┐   ┌─────────────┐   ┌──────────────────────────────┐                  │
   │  │ Indicators │──▶│ Rules Engine │──▶│  AIBrain（三档可切换）        │                  │
   │  │ (pandas)   │   │  触发 Alert  │   │  anthropic / claude / codex   │                  │
   │  └────────────┘   └──────┬──────┘   │  → 结构化 Decision            │                  │
   │        │                 │          └──────┬──────────────┬────────┘                  │
   │        │                 │                 │              │                            │
   │        │                 │                 │     ┌────────▼──────────┐                 │
   │        │                 │                 │     │  DeepAnalysis      │  三分析师并行    │
   │        │                 │                 │     │  技术/基本/消息    │  → CIO 综合      │
   │        │                 │                 │     └───────────────────┘                 │
   │        ▼                 ▼                 ▼                                            │
   │  ┌──────────┐     ┌────────────┐    ┌────────────────┐    ┌──────────┐                 │
   │  │  News    │     │  Notifier  │    │  Paper Trading  │    │ Reflection│                 │
   │  │ 聚合头条 │     │ (多渠道)   │    │ 人工确认 + 上限 │    │ 战绩回测  │                 │
   │  └──────────┘     └─────┬──────┘    └────────┬───────┘    └──────────┘                 │
   └─────────────────────────┼──────────────────────────┼─────────────────────────────────┘
                             │                          │
                             ▼                          ▼
              桌面/Bark/ntfy/TG/飞书/钉钉/企业微信/邮件         SQLite (db.py 持久化)
                                                                + Event Bus → SSE → React UI
```

要点：

- **Daemon**：唯一的后台循环，按市场开闭盘节流轮询；启动时从历史回补指标缓冲；每轮收尾写一笔净值快照供组合分析使用。
- **DataHub**：把 `MARKET:CODE` 规范符号转成各数据源格式，按市场选适配器并自动兜底，带短 TTL 历史缓存。
- **Adapters**：`yfinance` / `akshare` / `finnhub`，均归一化为 `Quote` 与 OHLCV `DataFrame`。
- **Indicators**：纯 pandas/numpy 计算 MA/RSI/MACD/KDJ/BOLL/ATR/量比等快照。
- **Rules Engine**：根据快照判断规则是否触发，产出 `Alert`。
- **AIBrain**：三档 provider，输出符合 schema 的结构化 `Decision`；全部失败时回退到确定性的「机械判断」，绝不阻塞盯盘。
- **DeepAnalysis**：多智能体编排，三位专家分析师并行 + 首席决策官综合（详见 [多智能体深度分析](#多智能体深度分析)）。
- **News**：多源聚合头条，既供「新闻」标签展示，也注入 AI / 深度分析的消息面上下文。
- **Notifier**：按严重级别路由到不同渠道，失败自动切下一个。
- **Paper Trading**：模拟下单，默认需人工确认，受持仓上限约束。
- **Analytics / Reflection**：纯计算的组合盯市与 AI 战绩回测，无网络调用。
- **FastAPI + SSE**：REST 接口 + 事件流；同进程托管前端构建产物。
- **React 前端**：Vite + lightweight-charts，实时看盘、规则、决策、新闻、组合、战绩、选股、模拟盘。

---

## 功能特性

- 美股 / A 股 / 港股统一盯盘，规范符号 `MARKET:CODE`（如 `US:AAPL`、`HK:00700`、`CN:600519`）。
- 自选股 watchlist + **跨市场名称搜索**（中文名 / 英文名 / 代码），盘中/盘后自适应轮询节流。
- 技术指标快照：MA(5/10/20/60)、RSI14、MACD、KDJ(J)、BOLL、ATR、量比/量能等（纯 pandas/numpy）。
- 盯盘规则引擎，11 种规则类型（见[盯盘规则](#盯盘规则)）+ 冷却时间 + 严重级别。
- AI 决策三档（Anthropic API / claude CLI / codex CLI），运行时一键切换 + 可选 ensemble 双 AI 交叉验证。
- **多智能体深度分析**：技术面 / 基本面·估值 / 消息面·情绪 三分析师并行 → 多空辩论式综合决策。
- **新闻/消息面聚合**：yfinance + akshare 头条合并去重，接入 AI 上下文。
- 结构化决策：action / conviction / horizon / 入场区间 / 止损 / 止盈 / 关键风险 / 数据新鲜度。
- **模拟组合分析**：净值曲线、浮动盈亏、已实现盈亏、胜率、按市场敞口。
- **AI 决策战绩**：用决策之后的真实价格回测方向准确率与买入信号 alpha，形成反馈闭环。
- **选股筛选器**：在自选股 / 热门 universe 上按 RSI/KDJ/MACD/涨跌幅 等指标筛选，预置快捷条件。
- 多渠道推送：桌面 / Bark / ntfy / Telegram / 飞书 / 钉钉 / 企业微信 / 邮件，按严重级别路由。
- 轻量量化：内置回测（如 `ma_cross`）+ 模拟盘（paper）下单、人工确认、持仓盈亏。
- **做 T 当日高低点预测**：Camarilla 枢轴 / ATR 投影 / 历史日内极值分位 / 波动率（Yang-Zhang/EWMA，可选 GARCH）多方法集成预估今日高低点，给高抛低吸挂单建议（感知 A 股 T+1 与涨跌停），可选 AI 经验分析。
- SSE 实时事件流，前端无刷新更新报价 / 警报 / 决策 / 订单。
- 安全护栏：默认 `signal` 模式、人工确认、持仓上限、kill-switch。
- 开箱即用：无需任何 API key 即可启动；支持 Docker / docker-compose 部署。

---

## 目录结构

```
stock-agent/
├── README.md
├── requirements.txt              # Python 依赖（部分可选但推荐；含测试依赖）
├── .env.example                  # 全部环境变量示例，可 cp 成 .env
├── Dockerfile                    # 后端 + 已构建前端 的容器镜像
├── docker-compose.yml            # 一键起容器（容器内用 anthropic API 档）
├── data/                         # 默认 SQLite 落库目录 (stockagent.db)
├── scripts/
│   ├── run.sh                    # 一键：建venv + 装依赖 + 构建前端 + 启动后端
│   ├── dev.sh                    # 开发：后端 --reload + 前端 vite dev 并行
│   └── com.stockagent.plist      # macOS launchd 自启模板（占位路径需手动改）
├── backend/
│   └── app/
│       ├── __main__.py           # python -m app 入口
│       ├── main.py               # FastAPI: REST + SSE + 托管前端
│       ├── config.py             # Settings + 单例 settings（所有 env 在此）
│       ├── daemon.py             # 后台轮询循环 daemon（analyze / deep_analyze 编排）
│       ├── datahub.py            # DataHub + 市场开闭盘判断
│       ├── models.py             # Quote/Candle/Rule/Alert/Decision/Position/PaperOrder
│       ├── symbols.py            # 规范符号解析与各数据源映射
│       ├── indicators.py         # compute_indicators 指标快照
│       ├── rules.py              # 规则引擎
│       ├── paper.py              # 模拟盘
│       ├── backtest.py           # 回测
│       ├── news.py               # 新闻/消息面聚合 (yfinance + akshare)
│       ├── deepanalysis.py       # 多智能体深度分析编排
│       ├── analytics.py          # 模拟组合盯市 + 净值快照
│       ├── reflection.py         # AI 决策战绩 / 反馈闭环
│       ├── screener.py           # 选股筛选器
│       ├── search.py             # 跨市场名称搜索
│       ├── bus.py                # 事件总线 (SSE)
│       ├── db.py                 # SQLite 持久化
│       ├── adapters/             # yfinance / akshare / finnhub 适配器
│       └── ai/                   # brain + anthropic_api / claude_cli / codex_cli
│           ├── brain.py          # AIBrain（三档调度 + ensemble + run_schema + 机械兜底）
│           ├── schema.py         # DECISION_SCHEMA
│           └── prompts.py        # SYSTEM_PROMPT / build_decision_prompt
└── frontend/                     # React + Vite + lightweight-charts
    ├── package.json              # scripts: dev / build / preview
    ├── src/
    │   └── components/           # Watchlist / AIPanel / NewsPanel / PortfolioPanel /
    │                             # TrackRecordPanel / ScreenerPanel / RightPanel ...
    └── dist/                     # pnpm build 产物（后端会自动托管）
```

---

## 快速开始

### 1. 安装 Python 依赖

```bash
cd stock-agent
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> 需要 **Python 3.10+**。`akshare`、`anthropic` 为可选但强烈推荐（A 股近实时数据 / 中文名搜索 / A 股头条 / Anthropic API 直连）。

### 2.（可选）配置环境变量

```bash
cp .env.example .env
# 按需填入 ANTHROPIC_API_KEY / FINNHUB_API_KEY / Bark / Telegram 等
```

不配也能跑：默认数据走 yfinance + akshare，AI 走本机 `claude` / `codex` CLI，推送走 macOS 桌面通知。

### 3. 构建前端（可选，但建议）

```bash
cd frontend
pnpm install
pnpm build            # 产物落到 frontend/dist，后端会自动托管
```

### 4. 启动后端

```bash
cd backend
python -m app
# 或
uvicorn app.main:app --host 127.0.0.1 --port 8848
```

打开浏览器访问 **http://127.0.0.1:8848** 即可。

> 也可以直接一键脚本：`./scripts/run.sh`（建 venv + 装依赖 + 构建前端 + 启动后端）。

### 开发模式（热重载）

后端开 `--reload`，前端开 Vite dev server（默认 8888，已配 CORS）：

```bash
# 终端 A
cd backend && uvicorn app.main:app --reload --port 8848
# 终端 B
cd frontend && pnpm dev          # http://127.0.0.1:8888
```

或一键：`./scripts/dev.sh`（并行起后端 reload + 前端 dev，Ctrl-C 会一起退出）。

---

## 部署（Docker / docker-compose）

容器化部署适合把后端长期跑在 NAS / 服务器上。镜像内含已构建的前端，单容器即可对外提供 UI + API。

```bash
# 构建并后台启动
docker compose up -d --build
# 访问 http://<host>:8848
```

或手动：

```bash
docker build -t stock-agent .
docker run -d -p 8848:8848 \
  -e AI_PROVIDER=anthropic -e ANTHROPIC_API_KEY=sk-ant-... \
  -v "$PWD/data:/app/data" \
  --name stock-agent stock-agent
```

> ⚠️ **AI provider 注意**：`claude` / `codex` 这两档**复用宿主机已登录的 CLI**，是 shell 会话级登录，**无法在容器里运行**。
> 在容器内请使用 `AI_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`（Anthropic Messages API 档）。
> 若要用 CLI 档（免 key），请在宿主机直接 `python -m app` 运行，而非容器内。
> 记得把 `data/`（SQLite 落库）挂载为持久化卷，否则容器重建会丢历史（决策、模拟单、净值）。

---

## 数据源说明

| 市场 | 首选数据源 | 兜底 | 说明 |
| --- | --- | --- | --- |
| **US 美股** | Finnhub（配 `FINNHUB_API_KEY` 时，实时） | yfinance（准实时） | 不配 key 时直接用 yfinance |
| **CN A 股** | akshare（**近实时、免 key**） | yfinance（延迟） | 由 `PREFER_AKSHARE_FOR_CN` 控制（默认开） |
| **HK 港股** | akshare（约 ~15 分钟延迟） | yfinance（约 ~15 分钟延迟） | 免费源均为延迟报价 |

- 适配器统一归一化为 `Quote`（含 `delayed` 标记）和 OHLCV `DataFrame`，上层逻辑与数据源解耦。
- DataHub 按市场维护「首选 → 兜底」链，单源失败自动切换；历史数据带短 TTL 缓存。
- **新闻头条**：`yfinance` 全市场头条 + `akshare`（A 股 `stock_news_em`，可选）合并去重；任一源失败均优雅降级。
- **名称搜索**：内置热门股（中英双语、离线即时）+ `akshare`（A 股/港股中文名）+ `yfinance.Search`（美股/英文名），并行 + 限时兜底。
- **港股 / A 股实时**：`futu`（富途）/ `longport`（长桥）可在后续接入为更高优先级的适配器，得到真正的实时行情。

---

## AI 大脑

三档可切换的 provider，均输出符合 `DECISION_SCHEMA` 的结构化 `Decision`：

| provider | 说明 | 是否需要 key |
| --- | --- | --- |
| `anthropic` | Anthropic Messages API 直连 | 需要 `ANTHROPIC_API_KEY` |
| `claude` | 本机 `claude -p` headless CLI | **不需要**（用 CLI 已登录的账号） |
| `codex` | 本机 `codex exec` headless CLI | **不需要**（用 CLI 已登录的账号） |
| `auto` | 有 `ANTHROPIC_API_KEY` 走 anthropic，否则走 claude，并依次兜底 | — |

切换方式：

- **运行时**：`POST /api/settings` body `{"ai_provider": "claude"}`（也可在前端切换；同时可切 `ai_ensemble`）。
- **启动默认**：环境变量 `AI_PROVIDER`（`auto` / `anthropic` / `claude` / `codex`）。

特性与兜底：

- `claude` / `codex` 直接复用你本机**已登录的 CLI**，无需在本项目配置任何 API key。
- 这两个 CLI 在本环境是 shell 别名，会通过登录 shell（`zsh -lc`）拉起，由 `AI_LOGIN_SHELL` 控制（默认开）。**注意：容器内无法使用这两档**（见[部署](#部署docker--docker-compose)）。
- 模型由 `CLAUDE_MODEL` / `CODEX_MODEL` 指定，超时由 `AI_TIMEOUT_S` 控制。
- `AI_ENSEMBLE=true` 时，对高信念（conviction ≥ 4）的决策会用另一档 AI 做二次交叉验证；意见不一致时自动下调信念并记入关键风险。
- AIBrain 还暴露 `run_schema(prompt, system, schema, provider)` 通用入口，供深度分析的各个智能体复用同一套 provider 选择与兜底逻辑。
- **全部失败也不阻塞**：AIBrain 会回退到确定性的「机械判断」（基于 RSI / 均线 / KDJ 的规则兜底），保证盯盘循环永不卡死。

---

## 多智能体深度分析

「快速分析」（`POST /api/analyze/{symbol}`）让单个模型给出一条决策；**深度分析**（`POST /api/deep-analyze/{symbol}`，前端「AI 决策」面板的「🔬 深度分析」按钮）则编排一组专家智能体协作研判：

1. **三位专家分析师并行**（每位只拿到与自己维度相关的上下文切片）：
   - **技术面**：基于价格、技术指标（MA/RSI/MACD/BOLL/KDJ 等）与最近 K 线判断趋势、动量、支撑/阻力。
   - **基本面·估值**：在「无详细财报/估值数据」的约束下做高层次的估值与仓位合理性推断（会主动声明数据有限、保持低信念）。
   - **消息面·情绪**：基于聚合到的新闻标题判断市场情绪偏向（利好/利空/中性）与潜在影响（无新闻则中性）。
   每位分析师输出 `{dimension, stance(bullish/bearish/neutral), score(1-5), summary, key_points}`（小结与要点为简体中文）。
2. **首席决策官（CIO）综合**：拿到三方意见 + 价格/指标快照，做一次**多空辩论式综合**——对比一致与冲突、说明取舍与加权，最终产出标准 `Decision`（action / conviction / horizon / 入场区间 / 止损止盈 / 详尽中文 rationale / 关键风险 / 数据新鲜度）。

返回结构：`{symbol, analysts: [三方意见], decision: Decision, ts}`。

**永不抛错**：任一分析师失败会被替换为中性占位意见；综合失败回退到 `brain.decide`，再不行回退到机械判断。所有模型调用都走 `AIBrain.run_schema`，复用同一套 provider 选择与跨档兜底。

---

## 新闻 / 消息面

`GET /api/news/{symbol}`（前端「新闻」标签）聚合多源头条：

- **yfinance**：全市场（US/HK/CN），兼容新旧两种 news 数据结构。
- **akshare `stock_news_em`**：仅 A 股，可选；源不可达时静默降级。

合并后按归一化标题去重（保留信息更全的条目）、按时间倒序、截断到 `limit`，统一为 `{title, publisher, ts, link, summary}`。这份新闻**同时注入 AI 决策与深度分析的消息面上下文**（daemon 构建 ctx 时 `with_news=True`），让 AI 能结合消息面给判断。

---

## 模拟组合分析

`GET /api/portfolio`（前端「组合」标签）把模拟持仓按 `latest` 报价**盯市**，纯计算、无网络调用：

- **净值与盈亏**：`nav`（盯市持仓 + 已落袋已实现盈亏）、`holdings_value`、`cost_basis`、`unrealized` / `unrealized_pct`。
- **逐仓明细**：每个持仓的 qty / 均价 / 现价 / 市值 / 浮动盈亏（绝对值 + 百分比）。
- **市场敞口**：按 US/HK/CN 汇总市值与占比（`exposure` / `exposure_pct`）。
- **已实现汇总**：`realized` 含已实现盈亏与**胜率**等。

配套接口：

- `GET /api/portfolio/history`：净值历史点（daemon 每轮收尾 `snapshot_nav` 写一笔），用于画**净值曲线**。
- `GET /api/realized`：已实现交易（平仓）流水。

行情缺失时单腿回退到均价（该腿零盈亏），坏行不会拖垮整个报表。

---

## AI 决策战绩 / 反馈闭环

`GET /api/track-record`（前端「战绩」标签）用**决策之后的真实价格**回测历史 AI 决策，给「AI 到底有没有用」一个直观的方向性体检：

- 对每条历史决策，比较「决策时报价」与「当前最新价」的涨跌幅，判断方向是否走对：
  - BUY/ADD：涨幅 > +0.5% 记对；
  - SELL/REDUCE：跌幅 < −0.5% 记对；
  - HOLD：|涨跌幅| ≤ 3% 记对。
- 聚合输出：总体 `accuracy`（方向准确率）、`avg_move`、**`buy_signal_alpha`**（买入信号平均事后涨幅）、`by_action`（分动作准确率/均值）、`recent`（最近 30 条评分明细）。
- 评分时把实现涨幅回写到决策行（`update_decision_realized`），完成闭环。

> 这是刻意做轻的「价格-only」代理指标（无基准、无持有期匹配），仅用于直觉判断，不构成绩效归因。

---

## 选股筛选器

`POST /api/screen`（前端「选股」标签）在一个 universe 上按指标筛选：

- **universe**：`watchlist`（自选股）/ `popular`（热门·全部）/ `popular_us`（热门·美股）/ `popular_hk`（热门·港股）/ `popular_cn`（热门·A股），或显式 `symbols` 列表。
- **filters**：一组 `{field, op, value}` 条件，**AND** 关系。可筛字段（`GET /api/screen/fields` 返回全集）涵盖报价类（`last` / `change_pct` / `open` / `high` / `low` / `volume`）与指标类（`rsi14` / `j` / `k` / `d` / `macd` / `macd_signal` / `macd_hist` / `ma5` / `ma10` / `ma20` / `ma60` / `boll_upper` / `boll_lower` / `atr14` / `vol` / `vol_ma20` / `close`）；运算符 `> < >= <= == !=`。
- **预置快捷条件**：RSI 超卖 <30 / RSI 超买 >70 / KDJ-J 超卖 <20 / MACD 翻多 >0 / 大涨 >5% / 大跌 <-5%。
- 并发拉取（信号量限流），按当日涨跌幅绝对值排序，返回 `{universe, scanned, matched, matches}`，命中项带 `tags` 等便于一键加入自选股。

---

## 自选股名称搜索

`GET /api/search?q=...`（自选股添加框的下拉）支持**中文名 / 英文名 / 代码**跨市场搜索，分层降级、永不卡住输入框：

1. **直接代码**：query 能解析成符号时直接给出「直接添加」选项（仅对明确的 `MARKET:CODE` 或纯数字代码）。
2. **内置热门**：中英双语、离线即时。
3. **akshare 名称表**：A 股全量代码/名称；含 CJK 时附带港股中文名搜索（本机环境）。
4. **yfinance Search**：美股 / 英文名。

结果按规范符号合并去重（直接/热门优先），实时源并行且限时 3s 兜底——慢源/不可达源也不会阻塞，热门/直接结果照样即时返回。

---

## API 一览

所有接口前缀 `/api`，由 `backend/app/main.py` 定义：

| Method | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 + daemon 状态 |
| GET | `/api/stream` | **SSE** 实时事件流（status/quote/alert/decision/order/nav…） |
| GET | `/api/watchlist` | 自选股列表 |
| POST | `/api/watchlist` | 添加自选股（`{symbol, name?}`） |
| DELETE | `/api/watchlist/{symbol}` | 删除自选股 |
| GET | `/api/search` | 跨市场名称/代码搜索（`?q=&limit=`） |
| GET | `/api/quotes` | 全部最新报价（内存缓存） |
| GET | `/api/quote/{symbol}` | 单标的实时报价 |
| GET | `/api/history/{symbol}` | OHLCV 历史（`?days=&interval=`） |
| GET | `/api/indicators/{symbol}` | 技术指标快照 |
| GET | `/api/rules` | 规则列表（`?symbol=`） |
| POST | `/api/rules` | 新增规则 |
| PATCH | `/api/rules/{rule_id}` | 启停规则（`{active}`） |
| DELETE | `/api/rules/{rule_id}` | 删除规则 |
| POST | `/api/analyze/{symbol}` | AI 快速分析（`{provider?}`） |
| POST | `/api/deep-analyze/{symbol}` | **多智能体深度分析**（`{provider?, strategy?, debate?}`；`debate=true` 启用多空辩论 + 风险调整综合） |
| GET | `/api/personas` | **投资大师清单**（可选人格 + 默认 Panel） |
| POST | `/api/persona-panel/{symbol}` | **投资大师 Panel 投票**（多位大师并行 → 加权投票共识；`{personas?, provider?}`） |
| GET/POST | `/api/day-t/{symbol}` | **做T 当日高低点预测 + 高抛低吸挂单建议**（多方法集成；`{ai?, provider?, use_garch?}`） |
| GET | `/api/decisions` | 决策历史（`?symbol=&limit=`） |
| GET | `/api/news/{symbol}` | **新闻/消息面头条**（`?limit=`） |
| GET | `/api/portfolio` | **模拟组合盯市快照** |
| GET | `/api/portfolio/history` | **净值历史**（`?limit=`） |
| GET | `/api/realized` | **已实现交易流水**（`?limit=`） |
| GET | `/api/track-record` | **AI 决策战绩** |
| GET | `/api/screen/fields` | **可筛字段 + 运算符** |
| POST | `/api/screen` | **选股筛选**（`{universe, symbols?, filters, limit}`） |
| GET | `/api/alerts` | 警报历史（`?limit=`） |
| GET | `/api/positions` | 模拟持仓（含现价/盈亏） |
| GET | `/api/orders` | 模拟订单（`?status=`） |
| POST | `/api/orders` | 下模拟单（`{symbol, side, qty, limit_price?}`） |
| POST | `/api/orders/{order_id}/approve` | 批准挂起的模拟单 |
| POST | `/api/orders/{order_id}/reject` | 驳回挂起的模拟单 |
| POST | `/api/backtest` | 回测（`{symbol, days?, strategy?, params?}`；stats 含 Sharpe/Sortino/Calmar/最大回撤/胜率） |
| POST | `/api/backtest/optimize` | 网格寻优（`{symbol, strategy?, grid?, metric?}`；`metric` 可选 total_return/sharpe/sortino/calmar/win_rate） |
| POST | `/api/backtest/walk-forward` | **样本外验证**（`{symbol, strategy?, folds?, train_ratio?, metric?}`；输出 IS vs OOS 与 overfit_gap） |
| GET | `/api/settings` | 运行时设置（AI provider / ensemble / 交易模式 / 渠道…） |
| POST | `/api/settings` | 修改运行时设置（含 kill-switch 切回 signal） |
| POST | `/api/test-notify` | 发一条测试推送 |

> 静态前端：根路径 `/` 与 SPA 兜底 `/{path}` 由后端托管 `frontend/dist`（构建产物存在时）。

---

## 通知渠道

按 **严重级别** 路由（`info` / `normal` / `critical`），失败自动切下一个可用渠道：

- **critical**：桌面（若开）+ Bark（critical 级、alarm 声）+ `[telegram, feishu, dingtalk, wecom]` 中**第一个可用**的冗余渠道。
- **normal**：桌面（若开）+ `[bark, ntfy, telegram, feishu, dingtalk, wecom]` 中第一个可用渠道。
- **info**：邮件（若配置），否则桌面。

配置方法（写进 `.env`）：

| 渠道 | 环境变量 | 示例 / 说明 |
| --- | --- | --- |
| 桌面（macOS 通知中心） | `NOTIFY_DESKTOP` | `true`（默认开） |
| Bark | `BARK_URL` | `https://api.day.app/<你的key>` |
| ntfy | `NTFY_URL` | `https://ntfy.sh/<你的topic>` |
| Telegram | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | 两者都需填 |
| 飞书 | `FEISHU_WEBHOOK` | 群机器人 webhook |
| 钉钉 | `DINGTALK_WEBHOOK` | 群机器人 webhook |
| 企业微信 | `WECOM_WEBHOOK` | 群机器人 webhook |
| 邮件 | `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_TO` | `SMTP_HOST` 与 `SMTP_TO` 都填才启用 |

测试推送：`POST /api/test-notify`（会向当前已配置的渠道发一条测试消息）。

---

## 盯盘规则

规则引擎支持以下 11 种类型（`type` + `params`，附带 `severity` 与 `cooldown_s` 冷却）：

| type | 含义 | 主要 params |
| --- | --- | --- |
| `price_above` | 价格突破上限 | `price` |
| `price_below` | 价格跌破下限 | `price` |
| `pct_move` | 相对昨收的当日涨跌幅绝对值达到阈值 | `pct` |
| `rsi_above` | RSI14 高于阈值（超买） | `value` |
| `rsi_below` | RSI14 低于阈值（超卖） | `value` |
| `ma_cross` | 快/慢均线金叉/死叉 | `fast`, `slow` |
| `macd_cross` | MACD 线穿越信号线 | —（用指标快照） |
| `kdj_cross` | KDJ 的 J 上穿 80（顶部预警）/ 下穿 20（底部预警） | —（用指标快照） |
| `volume_spike` | 成交量相对 N 日均量放大达倍数 | `mult` |
| `stop_loss` | 持仓止损：价格 ≤ 设定价 | `price` |
| `take_profit` | 持仓止盈：价格 ≥ 设定价 | `price` |

- 触发后产出 `Alert`（含触发时的报价 + 指标快照），经 Notifier 推送。
- `AUTO_AI_ON_CRITICAL=true` 时，`critical` 级警报会自动触发一次 AI 分析。

---

## 交易与风险

- **默认 signal-only**：`TRADING_MODE=signal` —— 只产出信号，**不创建任何模拟单或真实单**。
- **paper 模拟盘**：`TRADING_MODE=paper` 时，可下模拟单，记录持仓与盈亏，**永不触达真实券商**。
- **人工确认**：`REQUIRE_HUMAN_APPROVAL=true`（默认）—— 模拟单进入 `pending`，需经 `POST /api/orders/{id}/approve` 人工批准后才成交。
- **持仓上限**：`MAX_POSITION_VALUE` 限制单个持仓市值上限。
- **A 股合规提示**：A 股 **T+1**（当日买入次日才可卖）、**涨跌停限制**、以及更严格的合规要求 —— 本工具不绕过这些规则，相关信号仅供参考。
- **Kill-switch**：随时把 `trading_mode` 切回 `signal`（环境变量或 `POST /api/settings {"trading_mode":"signal"}`）即可立刻停止一切下单行为。

> 再次强调：本工具 **signal + paper only**，不接真实下单通道。

---

## 测试

后端测试用 pytest：

```bash
cd backend
python -m pytest
```

> 测试依赖 `pytest` / `pytest-asyncio` 已加入 `requirements.txt`（异步用例需要 `pytest-asyncio`）。

---

## 路线图

> 📚 **开源调研**：本项目对照 14 个高 star 仓库（TradingAgents / ai-hedge-fund / qlib /
> freqtrade / OpenBB / FinanceToolkit …）做了「取精华去糟粕」并落地了三项集成（投资大师
> Panel 投票、深度分析多空辩论、回测样本外验证 + 目标可选 + Sortino/Calmar）。
> 详见 [`docs/RESEARCH.md`](docs/RESEARCH.md)（含逐仓库精华/糟粕分析与后续路线）。

- [ ] 复合因子打分 + IC/Rank IC（qlib）、Altman Z / Piotroski 财务评分（FinanceToolkit）。
- [ ] 预交易风控熔断 / 冷却 / 过滤链（freqtrade / vnpy）。
- [ ] `futu`（富途）/ `longport`（长桥）实时行情适配器（港股 / A 股真实时）。
- [ ] IB（Interactive Brokers）/ Alpaca paper trading 接入（仍以模拟盘为先）。
- [ ] 深度分析引入更多专家维度（资金流 / 板块联动 / 宏观）。
- [ ] `qlib` 量化研究与因子/策略库集成。
- [ ] Tauri 桌面端打包，做成原生 App。

---

## 免责声明

本工具仅供个人**学习与研究**使用，所有输出（包括 AI 决策、多智能体深度分析、信号、指标、新闻聚合、回测、模拟盘结果、组合分析与战绩统计）**不构成任何投资建议**，亦不构成买卖任何证券的要约或招揽。市场有风险，据此操作的一切后果由使用者自行承担。开发者不对任何直接或间接损失负责。请遵守你所在司法辖区及相关交易所的法律法规与合规要求。
