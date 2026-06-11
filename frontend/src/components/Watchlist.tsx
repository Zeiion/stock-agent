import { useEffect, useRef, useState } from "react";
import {
  Quote,
  SearchHit,
  WatchRow,
  api,
  changeClass,
  currencySymbol,
  fmtNum,
  fmtPct,
} from "../lib/api";

interface Props {
  rows: WatchRow[];
  quotes: Record<string, Quote>;
  active: string | null;
  onSelect: (symbol: string) => void;
  onAdd: (raw: string) => Promise<void>;
  onDelete: (symbol: string) => void;
}

const MARKET_LABEL: Record<string, string> = { US: "美", HK: "港", CN: "A" };

export default function Watchlist({
  rows,
  quotes,
  active,
  onSelect,
  onAdd,
  onDelete,
}: Props) {
  const [input, setInput] = useState("");
  const [mkt, setMkt] = useState<"ALL" | "US" | "HK" | "CN">("ALL");
  const [searchMkt, setSearchMkt] = useState<"ALL" | "US" | "HK" | "CN">("ALL");
  const [busy, setBusy] = useState(false);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [hi, setHi] = useState(0); // highlighted index
  const boxRef = useRef<HTMLDivElement>(null);
  const seq = useRef(0);

  // debounced search as the user types
  useEffect(() => {
    const q = input.trim();
    if (!q) {
      setHits([]);
      setOpen(false);
      return;
    }
    setSearching(true);
    const mySeq = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const { results } = await api.search(q);
        if (mySeq !== seq.current) return; // stale
        setHits(results);
        setOpen(true);
        setHi(0);
      } catch {
        if (mySeq === seq.current) setHits([]);
      } finally {
        if (mySeq === seq.current) setSearching(false);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [input]);

  // close dropdown on outside click
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const add = async (raw: string) => {
    const v = raw.trim();
    if (!v || busy) return;
    setBusy(true);
    try {
      await onAdd(v);
      setInput("");
      setHits([]);
      setOpen(false);
    } finally {
      setBusy(false);
    }
  };

  const view = searchMkt === "ALL" ? hits : hits.filter((h) => h.market === searchMkt);

  // The + button / Enter should add the resolved search hit (the highlighted one,
  // else the top match) rather than the raw typed text — otherwise typing a name
  // like "比亚迪" would post the literal string and mint a broken symbol.
  const addFromBox = () => {
    if (view.length) add(view[hi]?.symbol ?? view[0].symbol);
    else add(input);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (open && view.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setHi((i) => Math.min(i + 1, view.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setHi((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        add(view[hi]?.symbol ?? input);
        return;
      }
      if (e.key === "Escape") {
        setOpen(false);
        return;
      }
    } else if (e.key === "Enter") {
      addFromBox();
    }
  };

  return (
    <div className="col sidebar">
      <div className="sidebar-head">
        <div className="title">自选股 · Watchlist</div>
        <div className="add-row" ref={boxRef}>
          <select
            className="mkt-presel"
            value={searchMkt}
            onChange={(e) => setSearchMkt(e.target.value as typeof searchMkt)}
            title="按市场筛选搜索结果"
          >
            <option value="ALL">全部</option>
            <option value="US">美股</option>
            <option value="HK">港股</option>
            <option value="CN">A股</option>
          </select>
          <div className="search-wrap">
            <input
              value={input}
              placeholder="搜索：苹果 / 腾讯 / 茅台 / AAPL / 600519"
              onChange={(e) => setInput(e.target.value)}
              onFocus={() => view.length && setOpen(true)}
              onKeyDown={onKey}
            />
            {open && (view.length > 0 || searching) && (
              <div className="search-pop">
                {searching && view.length === 0 && (
                  <div className="search-empty">搜索中…</div>
                )}
                {view.map((h, i) => (
                  <div
                    key={h.symbol}
                    className={"search-hit " + (i === hi ? "hi" : "")}
                    onMouseEnter={() => setHi(i)}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      add(h.symbol);
                    }}
                  >
                    <span className={"mtag m-" + h.market}>
                      {MARKET_LABEL[h.market] ?? h.market}
                    </span>
                    <span className="sh-code mono">{codeOf(h.symbol)}</span>
                    <span className="sh-name">{h.name}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          <button className="primary" onClick={addFromBox} disabled={busy}>
            {busy ? "…" : "+"}
          </button>
        </div>
      </div>

      {rows.length > 0 && (
        <div className="mkt-filter">
          {(["ALL", "US", "HK", "CN"] as const).map((m) => {
            const n = m === "ALL" ? rows.length : rows.filter((r) => r.market === m).length;
            return (
              <button
                key={m}
                className={"mkt-chip " + (mkt === m ? "active " : "") + ("m-" + m)}
                onClick={() => setMkt(m)}
              >
                {m === "ALL" ? "全部" : m === "US" ? "美" : m === "HK" ? "港" : "A"}
                <span className="mkt-n">{n}</span>
              </button>
            );
          })}
        </div>
      )}

      {rows.length === 0 && (
        <div className="empty">暂无自选股，搜索名称或代码添加一个。</div>
      )}

      {rows
        .filter((row) => mkt === "ALL" || row.market === mkt)
        .map((row) => {
        const q = quotes[row.symbol];
        const cls = changeClass(q?.change_pct);
        const name = q?.name || row.name;
        return (
          <div
            key={row.symbol}
            className={"watch-row " + (active === row.symbol ? "active" : "")}
            onClick={() => onSelect(row.symbol)}
          >
            <div className="wl-main">
              <div className="wl-sym">
                {codeOf(row.symbol)}
                <span className={"mtag m-" + row.market}>
                  {MARKET_LABEL[row.market] ?? row.market}
                </span>
              </div>
              <div className="wl-name">{name || " "}</div>
            </div>
            <div className="wl-price">
              <div className={"wl-last mono " + cls}>
                {q ? currencySymbol(row.market) + fmtNum(q.last) : "—"}
              </div>
              <div className={"wl-chg mono " + cls}>
                {q ? fmtPct(q.change_pct) : "—"}
              </div>
            </div>
            <button
              className="wl-del"
              title="移除"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(row.symbol);
              }}
            >
              ×
            </button>
          </div>
        );
      })}
    </div>
  );
}

function codeOf(symbol: string): string {
  const i = symbol.indexOf(":");
  return i >= 0 ? symbol.slice(i + 1) : symbol;
}
