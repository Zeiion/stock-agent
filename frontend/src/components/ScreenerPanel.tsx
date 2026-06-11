import { useEffect, useState } from "react";
import { ScreenMatch, api, changeClass, fmtNum } from "../lib/api";

interface Filter {
  field: string;
  op: string;
  value: number;
}

const UNIVERSES: { key: string; label: string }[] = [
  { key: "watchlist", label: "自选股" },
  { key: "popular", label: "热门(全部)" },
  { key: "popular_us", label: "热门·美股" },
  { key: "popular_hk", label: "热门·港股" },
  { key: "popular_cn", label: "热门·A股" },
];

const PRESETS: { label: string; filters: Filter[] }[] = [
  { label: "RSI 超卖 <30", filters: [{ field: "rsi14", op: "<", value: 30 }] },
  { label: "RSI 超买 >70", filters: [{ field: "rsi14", op: ">", value: 70 }] },
  { label: "KDJ-J 超卖 <20", filters: [{ field: "j", op: "<", value: 20 }] },
  { label: "MACD 翻多 >0", filters: [{ field: "macd_hist", op: ">", value: 0 }] },
  { label: "大涨 >5%", filters: [{ field: "change_pct", op: ">", value: 5 }] },
  { label: "大跌 <-5%", filters: [{ field: "change_pct", op: "<", value: -5 }] },
];

const MKT: Record<string, string> = { US: "美", HK: "港", CN: "A" };

export default function ScreenerPanel({
  onAdd,
}: {
  onAdd: (raw: string) => Promise<void>;
}) {
  const [fields, setFields] = useState<string[]>([]);
  const [ops, setOps] = useState<string[]>([">", "<", ">=", "<=", "==", "!="]);
  const [universe, setUniverse] = useState("popular");
  const [filters, setFilters] = useState<Filter[]>([
    { field: "rsi14", op: "<", value: 30 },
  ]);
  const [res, setRes] = useState<ScreenMatch[] | null>(null);
  const [scanned, setScanned] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api
      .screenFields()
      .then((d) => {
        setFields(d.fields);
        setOps(d.ops);
      })
      .catch(() => {});
  }, []);

  const run = async (fs?: Filter[]) => {
    const use = fs ?? filters;
    if (fs) setFilters(fs);
    setLoading(true);
    try {
      const r = await api.screen({ universe, filters: use });
      setRes(r.matches);
      setScanned(r.scanned);
    } catch {
      setRes([]);
    } finally {
      setLoading(false);
    }
  };

  const setF = (i: number, patch: Partial<Filter>) =>
    setFilters((prev) => prev.map((f, j) => (j === i ? { ...f, ...patch } : f)));

  return (
    <div>
      <div className="panel-title">选股筛选器</div>

      <div className="scr-row">
        <select value={universe} onChange={(e) => setUniverse(e.target.value)}>
          {UNIVERSES.map((u) => (
            <option key={u.key} value={u.key}>
              {u.label}
            </option>
          ))}
        </select>
        <button className="primary" onClick={() => run()} disabled={loading}>
          {loading ? "扫描中…" : "扫描"}
        </button>
      </div>

      <div className="scr-presets">
        {PRESETS.map((p) => (
          <button key={p.label} className="chip" onClick={() => run(p.filters)}>
            {p.label}
          </button>
        ))}
      </div>

      <div className="scr-filters">
        {filters.map((f, i) => (
          <div key={i} className="scr-filter">
            <select value={f.field} onChange={(e) => setF(i, { field: e.target.value })}>
              {fields.map((fl) => (
                <option key={fl} value={fl}>
                  {fl}
                </option>
              ))}
            </select>
            <select value={f.op} onChange={(e) => setF(i, { op: e.target.value })}>
              {ops.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
            <input
              type="number"
              value={f.value}
              onChange={(e) => setF(i, { value: Number(e.target.value) })}
            />
            <button
              className="tiny"
              onClick={() => setFilters((p) => p.filter((_, j) => j !== i))}
            >
              ×
            </button>
          </div>
        ))}
        <button
          className="tiny"
          onClick={() =>
            setFilters((p) => [...p, { field: fields[0] || "rsi14", op: "<", value: 0 }])
          }
        >
          + 条件
        </button>
      </div>

      {res !== null && (
        <>
          <div className="sub-title">
            命中 {res.length} / 扫描 {scanned}
          </div>
          {res.length === 0 ? (
            <div className="empty">没有符合条件的标的。</div>
          ) : (
            <table className="mini-table">
              <thead>
                <tr>
                  <th>标的</th>
                  <th className="r">现价</th>
                  <th className="r">涨跌</th>
                  <th className="r">RSI</th>
                  <th className="r">J</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {res.map((m) => (
                  <tr key={m.symbol}>
                    <td>
                      <span className={"mtag m-" + m.market}>{MKT[m.market] ?? m.market}</span>
                      <span className="mono"> {m.symbol.split(":")[1]}</span>
                      <div className="scr-name">{m.name}</div>
                    </td>
                    <td className="r mono">{fmtNum(m.last)}</td>
                    <td className={"r mono " + changeClass(m.change_pct)}>
                      {fmtNum(m.change_pct)}%
                    </td>
                    <td className="r mono">{m.rsi14 != null ? fmtNum(m.rsi14) : "—"}</td>
                    <td className="r mono">{m.j != null ? fmtNum(m.j) : "—"}</td>
                    <td className="r">
                      <button className="tiny" onClick={() => onAdd(m.symbol)} title="加入自选">
                        +
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}
