import { useEffect, useRef } from "react";
import {
  AreaSeriesPartialOptions,
  createChart,
  IChartApi,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

interface Props {
  data: { ts: number; equity: number }[];
}

export default function EquityChart({ data }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!wrapRef.current) return;
    const chart = createChart(wrapRef.current, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: "#9aa6b8",
        fontFamily: "ui-monospace, Menlo, Consolas, monospace",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: "rgba(35,42,54,0.4)" },
        horzLines: { color: "rgba(35,42,54,0.4)" },
      },
      rightPriceScale: { borderColor: "#232a36" },
      timeScale: { borderColor: "#232a36", timeVisible: false },
      handleScroll: false,
      handleScale: false,
    });
    const opts: AreaSeriesPartialOptions = {
      lineColor: "#4c8dff",
      topColor: "rgba(76,141,255,0.35)",
      bottomColor: "rgba(76,141,255,0.02)",
      lineWidth: 2,
      priceLineVisible: false,
    };
    const series = chart.addAreaSeries(opts);
    const seen = new Set<number>();
    const clean = data
      .filter((d) => {
        const t = Math.floor(d.ts);
        if (seen.has(t)) return false;
        seen.add(t);
        return true;
      })
      .sort((a, b) => a.ts - b.ts);
    series.setData(
      clean.map((d) => ({
        time: Math.floor(d.ts) as UTCTimestamp as Time,
        value: d.equity,
      }))
    );
    chart.timeScale().fitContent();
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [data]);

  return <div style={{ width: "100%", height: "100%" }} ref={wrapRef} />;
}
