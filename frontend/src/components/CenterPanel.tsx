import {
  Candle,
  Indicators,
  Quote,
  Rule,
  changeClass,
  currencySymbol,
  fmtNum,
  fmtPct,
  fmtVol,
} from "../lib/api";
import IndicatorStrip from "./IndicatorStrip";
import FundamentalsCard from "./FundamentalsCard";
import PriceChart, { PriceLine } from "./PriceChart";

interface Props {
  symbol: string | null;
  quote: Quote | null;
  candles: Candle[];
  indicators: Indicators | null;
  rules: Rule[];
  days: number;
  onChangeDays: (d: number) => void;
  interval: string;
  onChangeInterval: (iv: string, days?: number) => void;
  loading: boolean;
}

// interval options: each sets the yfinance interval + an appropriate day window
const INTERVALS: { key: string; label: string; days: number; intraday: boolean }[] = [
  { key: "1m", label: "分时", days: 2, intraday: true },
  { key: "5m", label: "5分", days: 20, intraday: true },
  { key: "15m", label: "15分", days: 40, intraday: true },
  { key: "60m", label: "60分", days: 60, intraday: true },
  { key: "1d", label: "日K", days: 180, intraday: false },
  { key: "1wk", label: "周K", days: 730, intraday: false },
  { key: "1mo", label: "月K", days: 1825, intraday: false },
];

const RULE_LINE: Record<string, { color: string; label: string }> = {
  price_above: { color: "#4c8dff", label: "突破" },
  price_below: { color: "#ffb02e", label: "跌破" },
  stop_loss: { color: "#ff5470", label: "止损" },
  take_profit: { color: "#2bd47a", label: "止盈" },
};

function rulesToLines(rules: Rule[]): PriceLine[] {
  const out: PriceLine[] = [];
  for (const r of rules) {
    if (!r.active) continue;
    const spec = RULE_LINE[r.type];
    const price = (r.params as Record<string, number>)?.price;
    if (spec && typeof price === "number") {
      out.push({ price, color: spec.color, title: spec.label });
    }
  }
  return out;
}

const TIMEFRAMES: { label: string; days: number }[] = [
  { label: "1M", days: 30 },
  { label: "3M", days: 90 },
  { label: "6M", days: 180 },
  { label: "1Y", days: 365 },
  { label: "2Y", days: 730 },
];

const LEGEND = [
  { label: "MA5", color: "#ffb02e" },
  { label: "MA20", color: "#4c8dff" },
  { label: "MA60", color: "#c46bff" },
];

export default function CenterPanel({
  symbol,
  quote,
  candles,
  indicators,
  rules,
  days,
  onChangeDays,
  interval,
  onChangeInterval,
  loading,
}: Props) {
  if (!symbol) {
    return (
      <div className="col center">
        <div className="empty" style={{ marginTop: 80 }}>
          从左侧自选股中选择一支标的查看行情与分析。
        </div>
      </div>
    );
  }

  const code = symbol.includes(":") ? symbol.split(":")[1] : symbol;
  const market = symbol.includes(":") ? symbol.split(":")[0] : "";
  const cls = changeClass(quote?.change_pct);
  const cur = currencySymbol(market);

  return (
    <div className="col center">
      <div className="sym-header">
        <span className="sh-sym">{code}</span>
        <span className="mtag">{market}</span>
        <span className="sh-name" title={quote?.long_name || quote?.name || ""}>
          {quote?.long_name || quote?.name || ""}
        </span>
        <span className={"sh-price mono " + cls}>
          {quote ? cur + fmtNum(quote.last) : "—"}
        </span>
        <span className={"sh-chg mono " + cls}>
          {quote
            ? `${quote.change != null && quote.change > 0 ? "+" : ""}${fmtNum(
                quote.change
              )} (${fmtPct(quote.change_pct)})`
            : "—"}
        </span>
        {quote?.delayed && <span className="delayed-pill">延迟</span>}

        <div className="sh-meta">
          <span>
            开 <b className="mono">{fmtNum(quote?.open)}</b>
          </span>
          <span>
            高 <b className="mono">{fmtNum(quote?.high)}</b>
          </span>
          <span>
            低 <b className="mono">{fmtNum(quote?.low)}</b>
          </span>
          <span>
            昨收 <b className="mono">{fmtNum(quote?.prev_close)}</b>
          </span>
          <span>
            量 <b className="mono">{fmtVol(quote?.volume)}</b>
          </span>
        </div>
      </div>

      <div className="chart-toolbar">
        <div className="seg">
          {INTERVALS.map((iv) => (
            <button
              key={iv.key}
              className={interval === iv.key ? "active" : ""}
              onClick={() => onChangeInterval(iv.key, iv.days)}
            >
              {iv.label}
            </button>
          ))}
        </div>
        {interval === "1d" && (
          <div className="seg">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf.days}
                className={days === tf.days ? "active" : ""}
                onClick={() => onChangeDays(tf.days)}
              >
                {tf.label}
              </button>
            ))}
          </div>
        )}
        <div className="legend">
          {LEGEND.map((l) => (
            <span key={l.label} className="legend-item">
              <span className="legend-swatch" style={{ background: l.color }} />
              {l.label}
            </span>
          ))}
        </div>
      </div>

      <div className="chart-wrap">
        {candles.length === 0 ? (
          <div className="empty" style={{ height: 420, paddingTop: 160 }}>
            {loading ? "加载 K 线…" : "无历史数据"}
          </div>
        ) : (
          <PriceChart
            candles={candles}
            priceLines={rulesToLines(rules)}
            intraday={INTERVALS.find((i) => i.key === interval)?.intraday ?? false}
          />
        )}
      </div>

      <IndicatorStrip ind={indicators} />
      <FundamentalsCard symbol={symbol} />
    </div>
  );
}
