import { useEffect, useState } from "react";
import { Fundamentals, api, fmtNum } from "../lib/api";

function fmtCap(v?: number | null): string {
  if (v == null) return "—";
  if (v >= 1e12) return (v / 1e12).toFixed(2) + "万亿";
  if (v >= 1e8) return (v / 1e8).toFixed(1) + "亿";
  return (v / 1e6).toFixed(0) + "M";
}

const REC: Record<string, string> = {
  strong_buy: "强烈买入", buy: "买入", hold: "持有",
  sell: "卖出", strong_sell: "强烈卖出", underperform: "跑输", none: "—",
};

export default function FundamentalsCard({ symbol }: { symbol: string | null }) {
  const [f, setF] = useState<Fundamentals | null>(null);

  useEffect(() => {
    setF(null);
    if (!symbol) return;
    let alive = true;
    api.fundamentals(symbol)
      .then((r) => alive && setF(r.fundamentals))
      .catch(() => {});
    return () => { alive = false; };
  }, [symbol]);

  if (!symbol || !f || Object.values(f).every((v) => v == null || v === "")) {
    return null;
  }

  const cells: { k: string; v: string; cls?: string }[] = [
    { k: "总市值", v: fmtCap(f.market_cap) },
    { k: "PE(TTM)", v: f.pe != null ? fmtNum(f.pe) : "—" },
    { k: "预期PE", v: f.forward_pe != null ? fmtNum(f.forward_pe) : "—" },
    { k: "PB", v: f.pb != null ? fmtNum(f.pb) : "—" },
    { k: "ROE", v: f.roe != null ? fmtNum(f.roe) + "%" : "—" },
    { k: "净利率", v: f.profit_margin != null ? fmtNum(f.profit_margin) + "%" : "—" },
    { k: "营收增速", v: f.revenue_growth != null ? fmtNum(f.revenue_growth) + "%" : "—",
      cls: f.revenue_growth != null ? (f.revenue_growth >= 0 ? "up" : "down") : "" },
    { k: "股息率", v: f.dividend_yield != null ? fmtNum(f.dividend_yield) + "%" : "—" },
    { k: "Beta", v: f.beta != null ? fmtNum(f.beta) : "—" },
    { k: "52周位置", v: f.pos_52w != null ? fmtNum(f.pos_52w) + "%" : "—" },
    { k: "目标价", v: f.target_price != null ? fmtNum(f.target_price) : "—" },
    { k: "机构评级", v: REC[f.recommendation ?? "none"] ?? f.recommendation ?? "—" },
  ];

  return (
    <div className="fund-card">
      <div className="fund-head">
        基本面
        {f.sector && <span className="dim small">{f.sector}{f.industry ? " · " + f.industry : ""}</span>}
      </div>
      <div className="fund-grid">
        {cells.map((c) => (
          <div key={c.k} className="fund-cell">
            <div className="k">{c.k}</div>
            <div className={"v mono " + (c.cls ?? "")}>{c.v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
