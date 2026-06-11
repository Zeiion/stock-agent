"""Prompt construction for the AI decision brain.

The user payload is deterministic and machine-built from the normalized Quote +
indicator snapshot + position + the rule that triggered, so the same context
feeds the Anthropic API, claude -p, and codex exec identically.
"""
from __future__ import annotations

import json
from typing import Any, Optional

# Selectable analysis strategies (lenses). The chosen lens is injected into the
# prompt so decisions reflect a consistent style, and is recorded per-decision so
# accuracy can be tracked per strategy.
ANALYSIS_STRATEGIES: dict[str, dict] = {
    # —— 风格策略 ——
    "balanced":   {"label": "均衡",     "group": "风格", "lens": "综合技术面、基本面与消息面，给出均衡、客观的判断。"},
    "value":      {"label": "价值投资", "group": "风格", "lens": "以价值投资视角为主：护城河、估值水平、长期现金流与安全边际；淡化短期波动，horizon 倾向 position（中长线）。"},
    "momentum":   {"label": "动量趋势", "group": "风格", "lens": "以趋势/动量为主：均线多头排列、创新高、放量突破、相对强度；顺势而为，horizon 倾向 swing。"},
    "swing":      {"label": "波段技术", "group": "风格", "lens": "以波段技术分析为主：支撑/阻力、超买超卖、MACD/KDJ/RSI 背离、箱体结构；聚焦 1–4 周的波段机会。"},
    "short_term": {"label": "短线",     "group": "风格", "lens": "以短线交易视角：日内/数日动能、量价配合、盘口与消息催化；horizon 倾向 intraday，强调严格止损。"},
    "contrarian": {"label": "逆向",     "group": "风格", "lens": "以逆向/均值回归视角：超跌反弹、情绪极端、错杀机会；在恐慌中找机会，但提示左侧抄底风险。"},
    # —— 大师人格（模仿其公开投资哲学的分析视角，非本人观点） ——
    "buffett":    {"label": "巴菲特",   "group": "大师", "lens": "扮演沃伦·巴菲特的分析视角：只买能理解的优质生意，看重持久护城河、高ROE、充沛自由现金流与诚实管理层；在合理价格买伟大公司，模糊的正确胜过精确的错误；几乎不做短线，horizon 倾向 position；对高估值、高杠杆、看不懂的故事直接回避。"},
    "munger":     {"label": "芒格",     "group": "大师", "lens": "扮演查理·芒格的分析视角：多元思维模型+逆向思考（先想怎么会亏），极度强调商业质量与理性，宁缺毋滥；用『这门生意十年后会更强吗』检验；厌恶平庸机会与过度交易，绝大多数时候的正确动作是 HOLD/不动。"},
    "graham":     {"label": "格雷厄姆", "group": "大师", "lens": "扮演本杰明·格雷厄姆的深度价值视角：安全边际是一切，重视低PB、低PE、股息与资产负债表稳健；把市场先生的报价当作可利用的情绪而非指引；对成长故事保持怀疑，只对显著低估给 BUY。"},
    "lynch":      {"label": "彼得·林奇", "group": "大师", "lens": "扮演彼得·林奇的 GARP 视角：在合理估值下找成长（关注 PEG≈PE/盈利增速），偏好简单易懂、日常可感知的业务；区分缓慢增长/坚定成长/快速成长/困境反转等类型并据此定策略；增速配不上估值就回避。"},
    "wood":       {"label": "木头姐",   "group": "大师", "lens": "扮演凯西·伍德(Cathie Wood)的颠覆性创新视角：聚焦指数级技术（AI/基因/自动驾驶/区块链）带来的5年期巨大市场空间，容忍高估值与高波动，敢于在恐慌抛售中加仓创新龙头；同时必须明确提示该风格的回撤风险。"},
    "burry":      {"label": "迈克尔·伯里", "group": "大师", "lens": "扮演迈克尔·伯里的深度逆向/风险揭示视角：寻找市场共识中的裂缝与泡沫迹象，重视硬数据与现金流而非叙事；对拥挤交易、被动资金扭曲、估值泡沫高度警惕；更倾向给出 SELL/REDUCE 或揭示下行风险的判断。"},
    "livermore":  {"label": "利弗莫尔", "group": "大师", "lens": "扮演杰西·利弗莫尔的趋势投机视角：只在关键点(突破/破位)出手，顺大势而为，让利润奔跑、果断砍掉亏损；重视量价行为与市场整体方向；明确给出关键价位（pivotal point）作为 entry/stop。"},
    "dalio":      {"label": "达利欧",   "group": "大师", "lens": "扮演瑞·达利欧的宏观/风险平价视角：从经济机器与周期（增长/通胀/流动性）出发判断资产环境，强调分散与风险预算；个股判断需放入宏观象限与相关性背景中，并提示组合层面的敞口建议。"},
    "fisher":     {"label": "费雪",     "group": "大师", "lens": "扮演菲利普·费雪的成长股研究视角：用『闲聊法』式的产业洞察评估管理层品质、研发实力与长期成长跑道；愿为卓越成长支付溢价，几乎永不卖出真正的成长股；关注营收增速与利润率趋势。"},
    "taleb":      {"label": "塔勒布",   "group": "大师", "lens": "扮演纳西姆·塔勒布的尾部风险/反脆弱视角：首要任务是识别脆弱性与隐藏的尾部风险（杠杆、拥挤、波动率压抑），偏好不对称收益结构（亏损有限、收益开放）；对高波动高估值标的强调仓位必须小，杠铃式配置思维。"},
    "druckenmiller": {"label": "德鲁肯米勒", "group": "大师", "lens": "扮演斯坦利·德鲁肯米勒的宏观+集中进攻视角：判断流动性与盈利周期的大方向，确认后敢于重仓出击（『当你确信时要下重注』），错了立刻认错离场；关注资金面、利率环境与领涨板块的轮动位置。"},
    "damodaran":  {"label": "达摩达兰", "group": "大师", "lens": "扮演阿斯沃斯·达摩达兰的『故事+数字』估值视角：把公司叙事翻译成增长率/利润率/再投资假设，估算内在价值区间并与现价比较；对叙事与数字脱节的标的明确指出；结论围绕『当前价格隐含了什么预期』展开。"},
}


def strategy_lens(key: str) -> str:
    return (ANALYSIS_STRATEGIES.get(key) or ANALYSIS_STRATEGIES["balanced"])["lens"]


SYSTEM_PROMPT = (
    "You are a disciplined, sell-side-grade equity analyst embedded in a PERSONAL "
    "stock-monitoring tool. You output a SUGGESTION only — you never place or "
    "execute trades; a human decides and acts.\n\n"
    "Rules of engagement:\n"
    "- Respect each market's conventions: A-shares (CN) have T+1 settlement and "
    "daily price limits (±10% main board, ±5% ST, ±20% STAR/ChiNext); HK trades in "
    "board lots; US is T+2.\n"
    "- Base your view on the provided price, indicators (MA/RSI/MACD/BOLL/KDJ), "
    "recent OHLCV, position, and any news. Do not invent data you were not given.\n"
    "- If the data is delayed or stale, set data_freshness_ok=false and LOWER your "
    "conviction accordingly.\n"
    "- conviction is 1 (weak) to 5 (strong). Be honest; most situations are 2-3.\n"
    "- Keep rationale concise and concrete, citing the actual numbers.\n"
    "- LANGUAGE: write `rationale` and every item in `key_risks` in Simplified "
    "Chinese (简体中文). Keep the enum fields (action / horizon) as their defined "
    "English values; numbers stay numeric.\n"
    "- Return ONLY the JSON object matching the schema. No prose outside it."
)


def build_decision_prompt(ctx: dict[str, Any]) -> str:
    """`ctx` is assembled by the AI brain and contains:
        quote: dict (normalized Quote.to_dict)
        indicators: dict (compute_indicators snapshot)
        recent: list[dict] (last ~20 OHLCV candles)
        position: dict | None
        trigger: dict | None  (the alert/rule that prompted this, if any)
        news: list[str]
    """
    quote = ctx.get("quote", {})
    ind = ctx.get("indicators", {})
    recent = ctx.get("recent", [])
    position = ctx.get("position")
    trigger = ctx.get("trigger")
    news = ctx.get("news", [])

    lines: list[str] = []
    # Prefer the descriptive long name so the model knows what a cryptic ticker
    # actually is (e.g. HK:07709 = a 2x-leveraged SK Hynix product, not a stock).
    disp_name = quote.get("long_name") or quote.get("name", "")
    lines.append(f"Analyze {quote.get('symbol', ctx.get('symbol', '?'))} "
                 f"({disp_name}) on the {quote.get('market','?')} market "
                 f"and return a trading SUGGESTION as JSON.")
    strat = ctx.get("strategy", "balanced")
    lines.append(f"分析策略视角：{strategy_lens(strat)} 请严格按此策略风格给出判断。")
    lines.append("")
    lines.append("== Current quote ==")
    lines.append(json.dumps({
        "symbol": quote.get("symbol"),
        "last": quote.get("last"),
        "prev_close": quote.get("prev_close"),
        "change_pct": quote.get("change_pct"),
        "open": quote.get("open"), "high": quote.get("high"), "low": quote.get("low"),
        "volume": quote.get("volume"),
        "currency": quote.get("currency"),
        "delayed": quote.get("delayed"),
        "source": quote.get("source"),
    }, ensure_ascii=False))

    if ind:
        lines.append("")
        lines.append("== Indicator snapshot ==")
        lines.append(json.dumps(ind, ensure_ascii=False))

    fund = ctx.get("fundamentals")
    if fund:
        from ..fundamentals import summarize_for_ai
        s = summarize_for_ai(fund)
        if s:
            lines.append("")
            lines.append("== 基本面快照 ==")
            lines.append(s)

    social = ctx.get("social")
    if social:
        from ..social import summarize_for_ai as _soc_sum
        s = _soc_sum(social)
        if s:
            lines.append("")
            lines.append("== 社交情绪快照 (X大V/Reddit/恐惧贪婪/股吧) ==")
            lines.append(s)

    if recent:
        lines.append("")
        lines.append("== Recent candles (oldest first) ==")
        compact = [
            {"t": c.get("ts"), "o": c.get("open"), "h": c.get("high"),
             "l": c.get("low"), "c": c.get("close"), "v": c.get("volume")}
            for c in recent[-20:]
        ]
        lines.append(json.dumps(compact, ensure_ascii=False))

    lines.append("")
    lines.append("== Position ==")
    lines.append(json.dumps(position or {"qty": 0}, ensure_ascii=False))

    history = ctx.get("history")
    if history:
        lines.append("")
        lines.append("== 本工具对该股的历史判断及事后表现 (反思参考, 勿盲从) ==")
        import time as _t
        for h in history[:5]:
            ago = ""
            if h.get("ts"):
                days = max(0, int((_t.time() - h["ts"]) / 86400))
                ago = f"{days}天前"
            rr = h.get("realized_return_pct")
            rr_s = f" 此后{'+' if rr and rr > 0 else ''}{rr}%" if rr is not None else ""
            lines.append(f"- {ago} {h.get('action')}(信念{h.get('conviction')},"
                         f"策略{h.get('strategy')}){rr_s}")

    if trigger:
        lines.append("")
        lines.append("== What triggered this analysis ==")
        lines.append(json.dumps(trigger, ensure_ascii=False))

    if news:
        lines.append("")
        lines.append("== Recent headlines ==")
        for h in news[:8]:
            lines.append(f"- {h}")

    lines.append("")
    lines.append("用简体中文填写 rationale 和 key_risks。"
                 "Return ONLY the JSON object for the decision schema.")
    return "\n".join(lines)
