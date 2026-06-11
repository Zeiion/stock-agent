import { useEffect, useState } from "react";
import { MarketResp, api, changeClass, fmtNum } from "../lib/api";

/** Thin global-market strip under the top bar: indices + fear & greed. */
export default function MarketStrip() {
  const [mkt, setMkt] = useState<MarketResp | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.market().then((m) => alive && setMkt(m)).catch(() => {});
    load();
    const t = window.setInterval(load, 60_000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  if (!mkt || mkt.indices.length === 0) return null;
  const fg = mkt.fear_greed;

  return (
    <div className="market-strip">
      {mkt.indices.map((ix) => (
        <span key={ix.ticker} className="ms-item" title={ix.ticker}>
          <span className="ms-name">{ix.name}</span>
          <span className={"mono ms-val " + changeClass(ix.change_pct)}>
            {fmtNum(ix.last)}
          </span>
          <span className={"mono ms-chg " + changeClass(ix.change_pct)}>
            {ix.change_pct > 0 ? "+" : ""}
            {fmtNum(ix.change_pct)}%
          </span>
        </span>
      ))}
      {fg?.score != null && (
        <span className="ms-item ms-fg" title="CNN Fear & Greed Index">
          <span className="ms-name">恐惧贪婪</span>
          <span
            className="mono ms-val"
            style={{
              color:
                fg.score < 25 ? "#ff5470" : fg.score < 45 ? "#ffb02e"
                : fg.score > 75 ? "#2bd47a" : "var(--text)",
            }}
          >
            {fg.score}
          </span>
          <span className="ms-chg dim">{fg.rating}</span>
        </span>
      )}
    </div>
  );
}
