import { useEffect, useRef } from "react";
import {
  createChart,
  CrosshairMode,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  LineStyle,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { Candle } from "../lib/api";

export interface PriceLine {
  price: number;
  color: string;
  title: string;
}

interface Props {
  candles: Candle[];
  priceLines?: PriceLine[];
  intraday?: boolean;
}

function sma(values: number[], n: number): (number | undefined)[] {
  const out: (number | undefined)[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    out.push(i >= n - 1 ? sum / n : undefined);
  }
  return out;
}

const COLORS = {
  ma5: "#ffb02e",
  ma20: "#4c8dff",
  ma60: "#c46bff",
  up: "#2bd47a",
  down: "#ff5470",
};

export default function PriceChart({ candles, priceLines = [], intraday = false }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const ma5Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ma20Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ma60Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const lineRefs = useRef<IPriceLine[]>([]);

  // create chart once
  useEffect(() => {
    if (!wrapRef.current) return;
    const chart = createChart(wrapRef.current, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: "#9aa6b8",
        fontFamily:
          "SFMono-Regular, ui-monospace, Menlo, Consolas, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(35,42,54,0.5)" },
        horzLines: { color: "rgba(35,42,54,0.5)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#232a36", scaleMargins: { top: 0.06, bottom: 0.28 } },
      timeScale: { borderColor: "#232a36", timeVisible: false, secondsVisible: false },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderUpColor: COLORS.up,
      borderDownColor: COLORS.down,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
    });

    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      color: "#2a3340",
    });
    volSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 },
    });

    const mkLine = (color: string) =>
      chart.addLineSeries({
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });

    ma5Ref.current = mkLine(COLORS.ma5);
    ma20Ref.current = mkLine(COLORS.ma20);
    ma60Ref.current = mkLine(COLORS.ma60);

    chartRef.current = chart;
    candleRef.current = candleSeries;
    volRef.current = volSeries;

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  // push data when candles change
  useEffect(() => {
    const candle = candleRef.current;
    const vol = volRef.current;
    if (!candle || !vol) return;

    // de-dup by ts (yfinance/akshare sometimes repeat), keep ascending
    const seen = new Set<number>();
    const clean = candles
      .filter((c) => {
        const t = Math.floor(c.ts);
        if (seen.has(t)) return false;
        seen.add(t);
        return true;
      })
      .sort((a, b) => a.ts - b.ts);

    candle.setData(
      clean.map((c) => ({
        time: Math.floor(c.ts) as UTCTimestamp as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );

    vol.setData(
      clean.map((c) => ({
        time: Math.floor(c.ts) as UTCTimestamp as Time,
        value: c.volume,
        color: c.close >= c.open ? "rgba(43,212,122,0.4)" : "rgba(255,84,112,0.4)",
      }))
    );

    const closes = clean.map((c) => c.close);
    const applyMa = (
      ref: React.MutableRefObject<ISeriesApi<"Line"> | null>,
      n: number
    ) => {
      const ma = sma(closes, n);
      const data = clean
        .map((c, i) =>
          ma[i] === undefined
            ? null
            : { time: Math.floor(c.ts) as UTCTimestamp as Time, value: ma[i] as number }
        )
        .filter((x): x is { time: Time; value: number } => x !== null);
      ref.current?.setData(data);
    };
    applyMa(ma5Ref, 5);
    applyMa(ma20Ref, 20);
    applyMa(ma60Ref, 60);

    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  // draw alert-rule price lines as horizontal overlays
  useEffect(() => {
    const candle = candleRef.current;
    if (!candle) return;
    for (const pl of lineRefs.current) {
      try { candle.removePriceLine(pl); } catch { /* ignore */ }
    }
    lineRefs.current = [];
    for (const l of priceLines) {
      if (!l.price || !isFinite(l.price)) continue;
      lineRefs.current.push(
        candle.createPriceLine({
          price: l.price,
          color: l.color,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: l.title,
        })
      );
    }
  }, [priceLines, candles]);

  // show intraday time on the axis for minute-level intervals
  useEffect(() => {
    chartRef.current?.applyOptions({
      timeScale: { timeVisible: intraday, secondsVisible: false },
    });
  }, [intraday]);

  return <div className="chart-box" ref={wrapRef} />;
}
