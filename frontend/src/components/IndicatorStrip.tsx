import { Indicators, changeClass, fmtNum } from "../lib/api";

interface Props {
  ind: Indicators | null;
}

function tagClass(t: string): string {
  const s = t.toLowerCase();
  if (s.includes("bull") || s.includes("oversold") || s.includes("macd>signal"))
    return "tag bull";
  if (s.includes("bear") || s.includes("overbought") || s.includes("macd<signal"))
    return "tag bear";
  return "tag";
}

export default function IndicatorStrip({ ind }: Props) {
  if (!ind || ind.close == null) {
    return <div className="empty">指标数据加载中…</div>;
  }

  const rsi = ind.rsi14 ?? null;
  const rsiCls = rsi == null ? "" : rsi >= 70 ? "down" : rsi <= 30 ? "up" : "";
  const macdCls = (ind.macd_hist ?? 0) >= 0 ? "up" : "down";
  const jCls =
    ind.j == null ? "" : ind.j >= 80 ? "down" : ind.j <= 20 ? "up" : "";

  return (
    <>
      <div className="indicator-strip">
        <div className="ind-card">
          <div className="ic-label">RSI 14</div>
          <div className={"ic-value mono " + rsiCls}>{fmtNum(rsi, 1)}</div>
          <div className="ic-sub">
            {rsi == null ? "" : rsi >= 70 ? "超买" : rsi <= 30 ? "超卖" : "中性"}
          </div>
        </div>

        <div className="ind-card">
          <div className="ic-label">MACD</div>
          <div className={"ic-value mono " + macdCls}>{fmtNum(ind.macd, 3)}</div>
          <div className="ic-sub mono">
            sig {fmtNum(ind.macd_signal, 3)} · hist {fmtNum(ind.macd_hist, 3)}
          </div>
        </div>

        <div className="ind-card">
          <div className="ic-label">KDJ</div>
          <div className={"ic-value mono " + jCls}>J {fmtNum(ind.j, 1)}</div>
          <div className="ic-sub mono">
            K {fmtNum(ind.k, 1)} · D {fmtNum(ind.d, 1)}
          </div>
        </div>

        <div className="ind-card">
          <div className="ic-label">MA 5 / 20 / 60</div>
          <div className="ic-value mono" style={{ fontSize: 13 }}>
            <span className={changeClass((ind.ma5 ?? 0) - (ind.ma20 ?? 0))}>
              {fmtNum(ind.ma5)}
            </span>{" "}
            / {fmtNum(ind.ma20)} / {fmtNum(ind.ma60)}
          </div>
        </div>

        <div className="ind-card">
          <div className="ic-label">BOLL</div>
          <div className="ic-value mono" style={{ fontSize: 13 }}>
            {fmtNum(ind.boll_upper)}
          </div>
          <div className="ic-sub mono">
            mid {fmtNum(ind.boll_mid)} · low {fmtNum(ind.boll_lower)}
          </div>
        </div>

        <div className="ind-card">
          <div className="ic-label">ATR 14</div>
          <div className="ic-value mono">{fmtNum(ind.atr14)}</div>
          <div className="ic-sub">{ind.bars ?? 0} 根 K 线</div>
        </div>
      </div>

      {ind.tags && ind.tags.length > 0 && (
        <div className="tag-row">
          {ind.tags.map((t, i) => (
            <span key={i} className={tagClass(t)}>
              {t}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
