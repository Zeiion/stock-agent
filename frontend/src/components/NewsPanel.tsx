import { useEffect, useState } from "react";
import { NewsItem, Sentiment, SocialResp, api, fmtDateTime } from "../lib/api";

const SENT: Record<string, { label: string; cls: string }> = {
  bullish: { label: "偏多", cls: "bull" },
  bearish: { label: "偏空", cls: "bear" },
  neutral: { label: "中性", cls: "neut" },
};

export default function NewsPanel({ symbol }: { symbol: string | null }) {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [agg, setAgg] = useState<Sentiment | null>(null);
  const [social, setSocial] = useState<SocialResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!symbol) {
      setNews([]);
      setAgg(null);
      setSocial(null);
      return;
    }
    let alive = true;
    setLoading(true);
    setErr(null);
    api
      .news(symbol, 15)
      .then((r) => {
        if (alive) {
          setNews(r.news);
          setAgg(r.sentiment);
        }
      })
      .catch((e) => alive && setErr(String(e)))
      .finally(() => alive && setLoading(false));
    api.social(symbol).then((s) => alive && setSocial(s)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [symbol]);

  if (!symbol) return <div className="empty">选择一只股票查看相关新闻。</div>;

  return (
    <div>
      <div className="panel-title">
        新闻 · {symbol.includes(":") ? symbol.split(":")[1] : symbol}
        {agg && agg.n ? (
          <span
            className={"sent-badge " + (SENT[agg.label]?.cls ?? "neut")}
            style={{ marginLeft: "auto" }}
            title={`多 ${agg.bull} / 空 ${agg.bear} / 中 ${agg.neutral ?? 0}`}
          >
            情绪 {SENT[agg.label]?.label ?? agg.label}
          </span>
        ) : null}
      </div>
      {loading && <div className="thinking"><span className="spinner" />加载新闻…</div>}
      {err && <div className="error-banner">{err}</div>}
      {!loading && !err && news.length === 0 && (
        <div className="empty">暂无相关新闻。</div>
      )}

      {social && social.posts.length > 0 && (
        <>
          <div className="sub-title">
            社交信号 · X大V / Reddit
            {social.aggregate?.n ? (
              <span className={"sent-badge " + (SENT[social.aggregate.label]?.cls ?? "neut")}
                    style={{ marginLeft: 8 }}>
                {SENT[social.aggregate.label]?.label ?? "中性"}
              </span>
            ) : null}
            {social.kol_handles.length === 0 && (
              <span className="dim small" style={{ marginLeft: "auto" }}>
                设置→x_kol_handles 配置大V
              </span>
            )}
          </div>
          <div className="news-list" style={{ marginBottom: 10 }}>
            {social.posts.slice(0, 8).map((p, i) => (
              <a key={i} className="news-item" href={p.link || undefined}
                 target="_blank" rel="noreferrer">
                <div className="news-title">
                  {p.sentiment && p.sentiment.label !== "neutral" && (
                    <span className={"sent-dot " + (SENT[p.sentiment.label]?.cls ?? "neut")} />
                  )}
                  {p.title}
                </div>
                <div className="news-meta">
                  <span>{p.source}{p.author && p.source.indexOf(p.author) < 0 ? " · " + p.author : ""}</span>
                  <span>{p.ts ? fmtDateTime(p.ts) : ""}</span>
                </div>
              </a>
            ))}
          </div>
          <div className="sub-title">新闻</div>
        </>
      )}
      <div className="news-list">
        {news.map((n, i) => (
          <a
            key={i}
            className="news-item"
            href={n.link || undefined}
            target="_blank"
            rel="noreferrer"
          >
            <div className="news-title">
              {n.sentiment && n.sentiment.label !== "neutral" && (
                <span className={"sent-dot " + (SENT[n.sentiment.label]?.cls ?? "neut")} />
              )}
              {n.title}
            </div>
            {n.summary && <div className="news-summary">{n.summary}</div>}
            <div className="news-meta">
              <span>{n.publisher || "—"}</span>
              <span>{n.ts ? fmtDateTime(n.ts) : ""}</span>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
