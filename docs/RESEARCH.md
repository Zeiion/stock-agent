# 高 Star 开源仓库调研 · 取精华去糟粕 · 融入 Stock Agent

> 调研日期：2026-06（star 数为当时 GitHub 页面实测值，仅作量级参考）。
> 目标：调研 ≥10 个高 star 仓库，**提炼可落地的精华**、**明确不抄的糟粕**，并把精华
> **真正融入** 本项目（不是写 PPT，而是落到代码 + 接口 + 测试）。

本项目（Stock Agent）已经是一个相当成熟的「个人盯盘 + 多智能体 AI 决策 + 轻量量化」系统，
因此调研的关键不是「抄一个新框架」，而是**逐仓库对照现有能力，只提炼当前真正缺失的那一部分**。

---

## 一、调研对象（14 个，按主题分三组）

| # | 仓库 | ★(量级) | 一句话定位 |
|---|------|--------|-----------|
| 1 | **TauricResearch/TradingAgents** | ~85k | 模拟交易公司组织架构的多智能体 LLM 交易框架 |
| 2 | **virattt/ai-hedge-fund** | ~60k | 以「传奇投资人」人格为 agent 的 AI 对冲基金（教学向） |
| 3 | AI4Finance-Foundation/FinGPT | ~20k | 金融领域开源 LLM（LoRA 微调 + 指令数据集） |
| 4 | AI4Finance-Foundation/FinRobot | ~7k | 基于 AutoGen 的金融分析 agent 平台（Perception→Brain→Action） |
| 5 | **microsoft/qlib** | ~44k | AI 量化投研平台（因子库 / 模型 / 回测 / IC 分析） |
| 6 | **freqtrade/freqtrade** | ~51k | 开源交易机器人（回测 / hyperopt / 保护机制 / 远程控制） |
| 7 | mementum/backtrader | ~22k | 纯 Python 事件驱动回测框架（Strategy/Analyzer/Sizer 插件） |
| 8 | vnpy/vnpy (VeighNa) | ~41k | 事件引擎量化交易平台（中国市场为主） |
| 9 | QuantConnect/Lean | ~20k | 专业级算法交易引擎（Alpha→Portfolio→Risk→Execution） |
| 10 | **OpenBB-finance/OpenBB** | ~69k | 统一 ~100 数据源的开源投研平台（provider 抽象 + Pydantic 标准模型） |
| 11 | JerBouma/FinanceToolkit | ~5k | 透明、可溯源的 150+ 财务指标计算引擎（5 大类比率） |
| 12 | ranaroussi/yfinance | ~24k | Yahoo Finance 数据下载器（本项目已用） |
| 13 | akfamily/akshare | ~20k | 中国/港/宏观数据聚合库（本项目已用） |
| 14 | wilsonfreitas/awesome-quant | ~27k | 量化库索引（当「选型菜单」用） |

---

## 二、逐仓库：精华 / 糟粕 / 与本系统的差距

### 1. TradingAgents —— 多空辩论 + 风险评议 + 反思记忆
- **精华**
  - **四类分析师**（基本面/情绪/新闻/技术）各司其职 —— 本系统已有等价的三分析师。
  - **多头 vs 空头研究员『结构化辩论』**：在交给决策者之前，先让多空双方就同一份证据
    来回对辩，平衡上行/下行。✅ **本系统缺失，已采纳**。
  - **Trader → Risk team → Portfolio Manager 的门控链**：决策不是一次 LLM 调用，而是
    经过风险团队（激进/中性/保守）评议后再批准。✅ **风险评议思想已采纳**（见集成②）。
  - **反思记忆闭环**：决策落库 → 事后取真实收益 → 生成一段反思 → 注入后续上下文。
    —— 本系统 `daemon._build_ctx` 已做等价实现（把历史决策 + `realized_return` 注入 prompt），
    并有 `reflection.py` 战绩回测。**已具备，无需重复**。
- **糟粕（不抄）**：十几个 agent + 多轮辩论的 token/延迟成本极高；LangGraph + SQLite
  checkpoint 的重编排框架；模拟执行无验证 alpha。→ 我们只取**辩论/风险评议的「模式」**，
  并用 `debate=True` 开关默认关闭，避免拖慢日常盯盘。

### 2. ai-hedge-fund —— 传奇投资人「人格即信号源」
- **精华**
  - **~13 位传奇投资人 persona-as-agent**（巴菲特/芒格/格雷厄姆/伍德/伯里/林奇/费雪/
    德鲁肯米勒/达摩达兰/塔勒布…），每人一套系统人格，对同一标的并行给信号。
  - **Risk Manager → Portfolio Manager 分层**：人格出「信号」，风险层加「约束」，组合层做
    「聚合裁决」。
  - 本系统**已内置 12 位大师 lens**（`ai/prompts.py`），但只作为 `brain.decide` 的**单一
    视角选择器**——**缺少「并行多人格 → 加权投票共识」**。✅ **已采纳**（集成①）。
- **糟粕（不抄）**：明确「不下真单、教学向」、人格输出未经回测验证、依赖付费
  Financial Datasets API、Poetry 全家桶。→ 我们复用**已有的大师 lens**（单一事实来源），
  共识里**显式暴露异见 `dissenters`**，对抗「拟人化过度自信」。

### 3. FinGPT —— 便宜微调 + 指令数据集
- **精华**：LoRA/QLoRA「便宜适配而非预训练」哲学；6 套金融指令数据集（情绪/关系抽取/
  标题/QA/NER）；FinGPT-Forecaster「结构化 prompt → 方向 + 理由」的范式。
- **糟粕（不抄）**：需要 GPU + 训练管线；基座模型偏老（Llama2 时代）；是「模型/数据集」
  项目，没有可借的编排/记忆/决策管线。→ 我们调用前沿 API/CLI，**无须其训练栈**；范式上
  与本系统「结构化 schema 决策」已一致。**不集成。**

### 4. FinRobot —— Perception→Brain→Action + 研报生成
- **精华**：清晰的单 agent 认知环（感知→思考(金融 CoT)→行动）；Director/Registry/Adaptor
  的动态 agent 路由；端到端 HTML/PDF **股票研报生成**。
- **糟粕（不抄）**：AutoGen + 注册/适配多层编排对个人工具过重；偏向托管 SaaS；多个付费数据源；
  社区体量小、文档薄。→ 认知环模式本系统已具备；**研报生成**可作为未来增强（见路线图），本轮不做。

### 5. qlib —— 因子 / IC 分析 / 滚动重训
- **精华**
  - 表达式**因子库**（Alpha158/360）—— 把因子作为一等对象。
  - **IC / Rank IC / ICIR** 信号质量评估（对**预测力**打分，而非只看已实现收益）。
  - **分位数多空组合**分析（按因子分 N 档，看 top−bottom 价差）。
  - **滚动/在线重训**（walk-forward 思想）。✅ **样本外验证思想已采纳**（集成③）。
- **糟粕（不抄）**：25+ 深度学习模型动物园、RL、GPU 训练、离线/在线数据服务器 + Docker、
  MLflow `qrun` 编排。→ 太重。本轮取**「样本外验证」**这一最关键、最轻的点；IC/因子库列入路线图。

### 6. freqtrade —— hyperopt 目标可选 + 保护机制
- **精华**
  - **hyperopt**：scikit-optimize 贝叶斯搜索，且**目标函数可选**（Sharpe/Sortino/Calmar/
    回撤…）—— 直击本系统「optimize 只按 total_return 排序」的弱点。✅ **目标可选已采纳**（集成③）。
  - ROI 阶梯 + trailing-stop 退出模型；**Protections** 风控熔断（连损暂停/回撤暂停/冷却）；
    可组合的 pairlist 过滤链；Telegram/WebUI 远程控制；dry-run 与实盘同代码路径。
- **糟粕（不抄）**：ccxt 交易所对接、合约/杠杆、FreqAI 自适应 ML、完整 bot 生命周期 DB。
  → 我们取**「目标可选优化 + 样本外」**；保护机制/过滤链列入路线图。

### 7. backtrader —— 策略/分析器/Sizer 插件化
- **精华**：统一 `Strategy` 基类 + 生命周期钩子；**Analyzer 插件**（Sharpe/Sortino/SQN/
  DrawDown 各自独立挂载）；**Sizer** 仓位抽象；更真实的撮合（限价/止损/滑点/手续费）。
- **糟粕（不抄）**：实盘网关；晦涩的 metaclass「lines」数据架构；自带 Matplotlib；事件循环
  对单标的网格比向量化慢；项目自 ~2021 基本停更。→ 取**「指标即可插拔分析器」**思想
  （本轮以新增 Sortino/Calmar 体现）；策略插件接口列入路线图。

### 8. vnpy —— 事件引擎 + 一策略两引擎 + 预交易风控
- **精华**：中心 `EventEngine`（事件队列 + 类型化处理器）解耦行情/信号/订单/UI；
  CTA「同一策略类跑回测与实盘」；回测内置（遗传）参数优化；**预交易风控**（下单频率/
  最大持仓/活动单上限/成交计数）；可换 DB 抽象。
- **糟粕（不抄）**：30+ 券商/交易所 C++/.dll 网关；Qt 桌面 GUI；高性能 K 线渲染；多 app
  桌面平台。→ 形态不对（我们是 FastAPI Web）。预交易风控/事件引擎列入路线图。

### 9. Lean —— Alpha→Portfolio→Risk→Execution + Insight
- **精华**：最可移植的是 **4 段框架** —— 信号生成 / 组合构建 / 风险管理 / 下单执行 各自可换；
  `Insight` 对象携带方向+幅度+置信度+权重+到期；Universe Selection 作为独立阶段；
  组合构建模型（等权/insight 加权/均值方差/BL）。
- **糟粕（不抄）**：C#/.NET 引擎本体（94% C#）、多资产订阅复杂度、实盘执行、Docker/CLI 云混合。
  → 只借**架构思想**：本系统 `Decision` 已类似 `Insight`；4 段流水线作为长期重构方向。

### 10. OpenBB —— provider 抽象 + Pydantic 标准模型
- **精华**：单一标准 endpoint + 可换 provider 注册表（把本系统 yfinance/akshare/finnhub
  兜底升级为「标准模型 + provider 注册」范式）；每个 endpoint 返回**类型化、跨源一致**的
  Pydantic 模型；免 key 源（SEC/FRED/EconDB/CBOE/finviz）；面向 LLM 的 MCP server 模式。
- **糟粕（不抄）**：~100 provider 的巨大依赖面 + AGPLv3；企业 Workspace/桌面层；大量付费源。
  → 取**「标准模型 + 多源兜底」范式**（本系统已具雏形）；统一 Pydantic 化列入路线图。

### 11. FinanceToolkit —— 5 大类财务比率库
- **精华**（对「丰富基本面」最对口）：**估值 / 盈利能力 / 流动性 / 偿债 / 营运效率** 五类
  共 150+ 比率，公式可溯源；`collect_*_ratios()` 批量 + 单指标 getter；TTM/增长助手；
  **Altman Z-Score / Piotroski / DuPont / WACC / DCF** 等高阶模型；财报标准化映射。
- **糟粕（不抄）**：核心依赖 FMP 付费 key（免费 250 req/天、仅美股 5 年）；含期权/固收/技术
  指标等越界模块；货币换算有坑。→ 取**公式**，喂本系统自有的 yfinance/akshare 财报数据；
  Z-Score/Piotroski 评分列入路线图（高价值、可落地）。

### 12–13. yfinance / akshare —— 已是本系统数据底座
- **精华（增量）**：yfinance 的 `Search/EquityQuery/Screener`、holders/期权、`curl_cffi`
  浏览器伪装抗限流、`download()` 批量、WebSocket 流；akshare 的 CN/港股 + **宏观/北向南向
  资金** endpoint 广度、snake_case 命名约定。
- **糟粕（不抄）**：均为非官方/ToS 脆弱、schema 易变、无内建缓存/限流 —— 必须**归一化 +
  缓存 + 兜底**（本系统已做）。→ 增量项（宏观资金流、holders）列入路线图。

### 14. awesome-quant —— 选型菜单
- **精华**：指标/风险库 **empyrical / quantstats / pyfolio**（现成 Sharpe/Sortino/MaxDD/
  VaR）；交易日历库 `exchange_calendars`（多市场 session）；情绪/另类数据分类。
- **糟粕（不抄）**：只是链接列表、无代码；重型回测/定价引擎（zipline/QuantLib C++）过度；
  不少条目停更。→ 当**菜单**用：本轮按 empyrical/quantstats 思路补 **Sortino/Calmar**。

---

## 三、差距矩阵：本系统已有 vs 本轮采纳

| 能力 | 现状 | 来源（精华） | 本轮 |
|------|------|------------|------|
| 多 provider AI + 兜底 + ensemble | ✅ 已有 | — | — |
| 三分析师深度分析 + CIO 综合 | ✅ 已有 | TradingAgents | — |
| 12 位大师人格（单一 lens） | ✅ 已有 | ai-hedge-fund | — |
| 决策记忆/反思注入 prompt | ✅ 已有 | TradingAgents | — |
| 基本面/社交情绪/新闻聚合 | ✅ 已有 | OpenBB/akshare | — |
| 回测 5 策略 + 网格优化 | ✅ 已有（全样本、只按收益） | freqtrade/qlib | 升级↓ |
| **大师并行 Panel + 加权投票共识** | ❌ 缺失 | **ai-hedge-fund** | ✅ **集成①** |
| **多空辩论 + 风险调整综合** | ❌ 缺失 | **TradingAgents** | ✅ **集成②** |
| **样本外 walk-forward 验证** | ❌ 缺失 | **qlib/freqtrade** | ✅ **集成③** |
| **优化目标可选（Sharpe/Sortino/Calmar…）** | ❌ 缺失 | **freqtrade** | ✅ **集成③** |
| **Sortino / Calmar 风险指标** | ❌ 缺失 | **empyrical/quantstats** | ✅ **集成③** |
| 因子库 / IC 分析 / 分位多空 | ❌ 缺失 | qlib | 路线图 |
| Altman Z / Piotroski / 财务比率库 | 部分 | FinanceToolkit | 路线图 |
| 预交易风控熔断 / 过滤链 | ❌ 缺失 | freqtrade/vnpy | 路线图 |
| Alpha→Portfolio→Risk→Exec 流水线 | ❌ 缺失 | Lean | 路线图 |
| Pydantic 标准模型 + provider 注册 | 雏形 | OpenBB | 路线图 |
| 研报生成 / 宏观资金流 | ❌ 缺失 | FinRobot/akshare | 路线图 |

---

## 四、本轮已落地的三项集成（含代码与测试）

### 集成① 投资大师 Panel（ai-hedge-fund 精华）
- 新增 `backend/app/personas.py`：复用 `ai/prompts.py` 里的 12 位大师 lens（单一事实来源），
  通过 `brain.run_schema` **并行**跑一组人格，每人输出 `{signal, action, confidence, reasoning,
  key_points}`，再做**信心加权投票**得到共识（含 `score`、`counts`、`participation`、
  **显式 `dissenters` 异见**）。永不抛错：失败人格降级为中性弃权。
- 端点：`GET /api/personas`（人格清单 + 默认 Panel）、`POST /api/persona-panel/{symbol}`。
- 前端：AIPanel 新增「🎭 大师投票」按钮 + 共识条 + 人格卡片。
- 测试：`tests/test_personas.py`（聚合/弃权/解析/全流程 mock）。
- **去糟粕**：不下真单、不做仓位；共识**暴露异见**而非掩盖，抵御拟人化过度自信。

### 集成② 多空辩论 + 风险调整综合（TradingAgents 精华）
- `backend/app/deepanalysis.py` 新增 `debate` 开关：三分析师之后，**多头研究员 / 空头研究员**
  基于同一证据并行对辩（各含 thesis/arguments/rebuttal/confidence），再由**肩负风险管理职责的
  CIO** 做风险调整后的最终 `Decision`（势均力敌时主动下调 conviction、收紧止损）。
- 透传：`daemon.deep_analyze(..., debate=)` → `POST /api/deep-analyze {debate:true}`；返回新增
  `researchers`。前端：深度分析旁「多空辩论」勾选，结果区渲染多空研究员卡片。
- 测试：`tests/test_deepanalysis.py`（默认不辩论 / 辩论全流程 / 单边研究员失败仍完成）。
- **去糟粕**：默认 `debate=False`，不抄十几 agent 的高成本编排，只取「对抗式辩论 + 风险门控」模式。

### 集成③ 回测样本外验证 + 目标可选 + 风险指标（qlib / freqtrade / empyrical 精华）
- `backend/app/backtest.py`：
  - `walk_forward()`：锚定式样本外验证——前 `train_ratio` 训练、其余切 `folds` 段，
    每折在「之前的数据」上按目标优化，再在**未见过的**测试段评估，输出 IS vs OOS 与
    `overfit_gap`（正值=过拟合）。
  - `optimize(metric=...)`：目标可选（`total_return/sharpe/sortino/calmar/win_rate`）。
  - `_compute_stats` 新增 **Sortino**（只罚下行波动）与 **Calmar**（年化/最大回撤）。
- 端点：`POST /api/backtest/walk-forward`；`POST /api/backtest/optimize` 增加 `metric`。
- 测试：`tests/test_backtest.py` 扩充（stats 键、目标排序、回退、walk-forward 形状与短序列降级）。
- **去糟粕**：不引入 DL 模型动物园 / hyperopt 依赖 / 数据服务器，只取「样本外 + 目标可选」最关键点。

> 全量后端测试：`cd backend && python -m pytest` —— 通过（含新增用例）。前端 `pnpm build` 通过。

---

## 五、路线图（已识别、本轮未做的精华，按性价比排序）
1. **Sortino/Calmar → 选股与战绩**：把风险指标接入 screener 排序与 track-record。
2. **复合因子打分 + IC/Rank IC**（qlib）：screener 从「单指标 AND」升级为「多因子排序 + 预测力评估」。
3. **Altman Z-Score / Piotroski F-Score**（FinanceToolkit）：用现有 yfinance/akshare 财报数据，零新增付费源。
4. **预交易风控熔断 / 冷却 / 过滤链**（freqtrade/vnpy）：增强 paper 交易安全护栏。
5. **Pydantic 标准模型 + provider 注册表**（OpenBB）：统一适配器返回结构，便于扩源。
6. **宏观/北向南向资金流**（akshare）、**holders/期权**（yfinance）：丰富消息面与基本面。
7. **股票研报生成**（FinRobot）：把深度分析 + 大师 Panel 汇总为可导出 HTML/PDF 研报。
8. **Alpha→Portfolio→Risk→Execution 流水线 + Insight 抽象**（Lean）：长期架构演进方向。

---

## 六、做 T 当日高低点预测（开源算法调研 + 集成）

> 需求：用成熟预测算法（历史日 K 规律总结 + 时序/波动率预测 + 经验分析）预估**当日高点/低点**，
> 给可挂单的高抛低吸建议。下面是开源算法调研（已用 Web 核实公式与库现状）+ 落地方案。

### 调研：成熟 / 开源的算法（精华 / 糟粕）
| 类别 | 算法 | 精华（为何用） | 糟粕 / 注意 |
|------|------|--------------|------------|
| 枢轴点 | 经典(Floor) / 斐波那契 / **Camarilla** | 零拟合、可解释的支撑阻力；**Camarilla H3/L3** 就是日内反转(做T)带，L3 买/H3 卖、H4/L4 作突破止损 | 纯机械、无波动率regime意识；1.1/x 系数是经验值 |
| 波动率(OHLC) | Parkinson / Garman-Klass / Rogers-Satchell / **Yang-Zhang** | 用 OHLC 把历史波幅高效估成 σ；**Yang-Zhang** 含隔夜跳空+漂移，最稳 | 多假设无跳空/无漂移，A股跳空+涨跌停下有偏；是 realized 非 forecast |
| 波幅投影 | **ATR(14)** (Wilder) | 把昨日真实波幅直接投影成今日价带 anchor±k·ATR；本系统已有 `atr()` | k 取值经验性 |
| 时序波动率 | **GARCH**(`arch` v8, 维护中) / **EWMA**(λ=0.94, 零依赖) | GARCH 给**次日** σ 预测（捕捉波动聚集）；EWMA 一行递归、无依赖兜底 | GARCH 是编译依赖、需 100+ 样本、只预测幅度非方向；单股日频常仅略胜 EWMA |
| 经验分位 | (high-prevclose)/prevclose、(low-prevclose)/prevclose 的历史分位 | **直接回答「高低点通常落在哪」**（历史日 K 规律总结）；纯 numpy、天然不对称 | 平稳性假设；窗口小则噪声大 |
| 时序(价格) | ARIMA / Holt-Winters / Prophet | —— | 日收近随机游走，预测**价位**几乎不胜 naive；Prophet 重依赖、对日内高低无输出 → **不采用** |
| 分位回归 | LightGBM `objective=quantile` | 可按特征给校准的高低分位区间 | ML 依赖、易过拟合单股、需时序CV 防泄漏 → 留作可选升级层 |

**库现状**（2026-06 核实）：`arch` v8 活跃（GARCH 标准）；`TA-Lib` v0.6.8 已带预编译 wheel；
`pandas-ta` 计划 2026-07 归档（用 `pandas-ta-classic` 继任）→ **枢轴/ATR 自己用 numpy 实现更稳**。

### 集成：`backend/app/intraday.py`（纯 pandas/numpy，零依赖，优雅降级）
分层集成（推荐架构落地）：
1. **Tier 0（永远可用，零依赖）**：自实现 Camarilla(H3/L3/H4/L4) + 经典/斐波那契枢轴、
   Wilder ATR 投影、历史日内极值分位。
2. **Tier 1（仅 numpy）**：Yang-Zhang / Parkinson / GK / RS + **EWMA σ** 把价带按当前 regime 缩放。
3. **Tier 2（可选安装）**：`arch` 的 **GARCH 次日 σ**，缺失/失败自动回退 EWMA。

**集成共识**：取各方法高点中位数 / 低点中位数为预测高/低点（用 E[极差]≈1.6σ 把 σ 换成价带），
跨方法离散度→置信度。**A 股按涨跌停（默认 ±10% 主板口径）夹取**预测区间。

**做 T 挂单建议** `day_t_plan`：持仓者→近预测高点**高抛**部分底仓、近预测低点**低吸买回**降成本
（建议量按板块手数取整）；空仓者→低吸/高抛区间参考。**全程感知 A 股 T+1 与涨跌停**，振幅低于
性价比阈值(1.2%)则建议观望；给出突破止损位(H4/L4)。

**可选 AI 经验分析** `ai_commentary`（复用 `brain.run_schema`）：把统计预测+枢轴+波动率+近 K 线+
持仓喂给「做 T 资深交易员」，产出经验分析叙述、是否值得做 T、以及**夹取在预测带内**的挂单价。

- 端点：`GET/POST /api/day-t/{symbol}`（`{ai?, provider?, use_garch?}`）。
- 前端：右侧「做 T」标签——预测高/低点、低吸/高抛挂单价、各方法明细、T+1/涨跌停提示、可勾选 AI 经验分析。
- 测试：`tests/test_intraday.py`（枢轴精确值、Parkinson 闭式解、各估计器、集成形状、CN 涨跌停夹取、
  做 T 计划手数取整 / 振幅过窄观望 / 空仓提示）。
- **去糟粕**：不上 Prophet/ARIMA（对高低点无效）、不强依赖 ML/GARCH（可选且兜底）、枢轴自实现避开
  pandas-ta 归档风险；signal-only，不自动下单。
