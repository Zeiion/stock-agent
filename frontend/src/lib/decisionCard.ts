// Renders an AI decision card to a PNG image (canvas-drawn, no dependencies).
// Produces a clean, branded share-card that matches the app's dark theme, then
// exposes copy-to-clipboard / download helpers around the resulting Blob.

import { Decision, fmtDateTime, fmtNum } from "./api";

const SANS =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", ' +
  '"Microsoft YaHei", Roboto, Helvetica, Arial, sans-serif';
const MONO =
  '"SFMono-Regular", "JetBrains Mono", "Roboto Mono", ui-monospace, ' +
  "Menlo, Consolas, monospace";

// Theme tokens mirrored from index.css :root
const C = {
  bg: "#0a0c10",
  card: "#0e1117",
  cell: "#0e1117",
  bg2: "#141821",
  bg3: "#1b212c",
  border: "#232a36",
  borderSoft: "#1a202b",
  text: "#e6ebf2",
  dim: "#9aa6b8",
  faint: "#5e6a7d",
  accent: "#4c8dff",
  up: "#2bd47a",
  upBg: "rgba(43,212,122,0.12)",
  upBorder: "rgba(43,212,122,0.40)",
  down: "#ff5470",
  downBg: "rgba(255,84,112,0.12)",
  downBorder: "rgba(255,84,112,0.40)",
  warn: "#ffb02e",
};

export interface CardLabels {
  strategyLabel?: string;
  horizonLabel: string;
}

// ---- layout geometry (logical pixels; scaled by DPR at render time) ----
const IMG_W = 600;
const M = 16; // gap between card edge and image edge
const PAD = 22; // inner padding of the card
const X0 = M + PAD; // content left
const CONTENT_W = IMG_W - 2 * M - 2 * PAD; // content width
const DPR = 2;

type Ctx = CanvasRenderingContext2D;

function short(symbol: string): string {
  return symbol.includes(":") ? symbol.split(":")[1] : symbol;
}

function actionColors(action: string): { bg: string; fg: string; border: string } {
  if (action === "BUY" || action === "ADD")
    return { bg: C.upBg, fg: C.up, border: C.upBorder };
  if (action === "SELL" || action === "REDUCE")
    return { bg: C.downBg, fg: C.down, border: C.downBorder };
  return { bg: C.bg3, fg: C.dim, border: C.border };
}

function roundRect(ctx: Ctx, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

// Tokenize into CJK chars / latin word-runs / whitespace so wrapping breaks at
// sensible spots in both Chinese and English.
function tokenize(text: string): string[] {
  const re =
    /[　-〿㐀-䶿一-鿿豈-﫿＀-￯]|[^　-〿㐀-䶿一-鿿豈-﫿＀-￯\s]+|\s+/g;
  const out: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) out.push(m[0]);
  return out;
}

// ctx.font must already be set. Returns the laid-out lines.
function wrapLines(ctx: Ctx, text: string, maxW: number): string[] {
  const lines: string[] = [];
  for (const para of (text || "").split("\n")) {
    let line = "";
    for (const tok of tokenize(para)) {
      const trial = line + tok;
      if (line !== "" && ctx.measureText(trial).width > maxW) {
        lines.push(line.replace(/\s+$/, ""));
        line = /^\s+$/.test(tok) ? "" : tok;
      } else {
        line = trial;
      }
    }
    lines.push(line.replace(/\s+$/, ""));
  }
  return lines.length ? lines : [""];
}

// A rounded "pill". Returns its total width.
function drawPill(
  ctx: Ctx,
  x: number,
  y: number,
  text: string,
  opt: { font: string; fg: string; bg?: string; border?: string; padX?: number; h?: number },
  draw: boolean,
): number {
  const padX = opt.padX ?? 7;
  const h = opt.h ?? 18;
  ctx.font = opt.font;
  const w = ctx.measureText(text).width + padX * 2;
  if (draw) {
    if (opt.bg) {
      ctx.fillStyle = opt.bg;
      roundRect(ctx, x, y, w, h, 4);
      ctx.fill();
    }
    if (opt.border) {
      ctx.strokeStyle = opt.border;
      ctx.lineWidth = 1;
      roundRect(ctx, x + 0.5, y + 0.5, w - 1, h - 1, 4);
      ctx.stroke();
    }
    ctx.fillStyle = opt.fg;
    ctx.textBaseline = "middle";
    ctx.fillText(text, x + padX, y + h / 2 + 0.5);
    ctx.textBaseline = "top";
  }
  return w;
}

// Single pass that measures (draw=false) or paints (draw=true). Returns the y
// at which content ends so the canvas height can be sized exactly.
function paint(ctx: Ctx, draw: boolean, d: Decision, labels: CardLabels): number {
  ctx.textBaseline = "top";
  let y = M + PAD;

  // brand strip
  ctx.font = `600 11px ${SANS}`;
  if (draw) {
    ctx.fillStyle = C.faint;
    ctx.fillText("STOCK AGENT · AI 决策", X0, y);
  }
  y += 24;

  // symbol (big) with horizon pill on the right
  ctx.font = `700 30px ${SANS}`;
  if (draw) {
    ctx.fillStyle = C.text;
    ctx.fillText(short(d.symbol), X0, y);
  }
  const horizonW = drawPill(ctx, 0, 0, labels.horizonLabel, {
    font: `600 11px ${SANS}`,
    fg: C.dim,
    bg: C.bg3,
    h: 22,
  }, false);
  if (draw) {
    drawPill(ctx, X0 + CONTENT_W - horizonW, y + 6, labels.horizonLabel, {
      font: `600 11px ${SANS}`,
      fg: C.dim,
      bg: C.bg3,
      h: 22,
    }, true);
  }
  y += 42;

  // action badge + conviction stars
  const ac = actionColors(d.action);
  ctx.font = `700 15px ${SANS}`;
  const badgeW = ctx.measureText(d.action).width + 24;
  const badgeH = 30;
  if (draw) {
    ctx.fillStyle = ac.bg;
    roundRect(ctx, X0, y, badgeW, badgeH, 6);
    ctx.fill();
    ctx.strokeStyle = ac.border;
    ctx.lineWidth = 1;
    roundRect(ctx, X0 + 0.5, y + 0.5, badgeW - 1, badgeH - 1, 6);
    ctx.stroke();
    ctx.fillStyle = ac.fg;
    ctx.textBaseline = "middle";
    ctx.fillText(d.action, X0 + 12, y + badgeH / 2 + 1);
    ctx.textBaseline = "top";
  }
  // stars
  const full = Math.max(0, Math.min(5, d.conviction));
  ctx.font = `400 18px ${SANS}`;
  const starsX = X0 + badgeW + 12;
  if (draw) {
    ctx.textBaseline = "middle";
    const fullStr = "★".repeat(full);
    ctx.fillStyle = C.warn;
    ctx.fillText(fullStr, starsX, y + badgeH / 2 + 1);
    const fullW = ctx.measureText(fullStr).width;
    ctx.fillStyle = C.bg3;
    ctx.fillText("★".repeat(5 - full), starsX + fullW, y + badgeH / 2 + 1);
    ctx.textBaseline = "top";
  }
  y += badgeH + 18;

  // rationale
  ctx.font = `400 14.5px ${SANS}`;
  const rlines = wrapLines(ctx, d.rationale, CONTENT_W);
  if (draw) {
    ctx.fillStyle = C.text;
    rlines.forEach((ln, i) => ctx.fillText(ln, X0, y + i * 22));
  }
  y += rlines.length * 22 + 16;

  // 3-cell grid: entry / stop / target
  const gap = 8;
  const cellW = (CONTENT_W - 2 * gap) / 3;
  const cellH = 54;
  const entry =
    d.entry_zone && d.entry_zone.length
      ? d.entry_zone.map((n) => fmtNum(n)).join(" – ")
      : "—";
  const target =
    d.take_profit && d.take_profit.length
      ? d.take_profit.map((n) => fmtNum(n)).join(" / ")
      : "—";
  const cells = [
    { k: "入场区间", v: entry, color: C.text },
    { k: "止损", v: fmtNum(d.stop_loss), color: C.down },
    { k: "目标", v: target, color: C.up },
  ];
  cells.forEach((cell, i) => {
    const cx = X0 + i * (cellW + gap);
    if (draw) {
      ctx.fillStyle = C.bg2;
      roundRect(ctx, cx, y, cellW, cellH, 6);
      ctx.fill();
      ctx.strokeStyle = C.borderSoft;
      ctx.lineWidth = 1;
      roundRect(ctx, cx + 0.5, y + 0.5, cellW - 1, cellH - 1, 6);
      ctx.stroke();
      ctx.fillStyle = C.faint;
      ctx.font = `600 10px ${SANS}`;
      ctx.fillText(cell.k, cx + 10, y + 11);
      ctx.fillStyle = cell.color;
      ctx.font = `500 14px ${MONO}`;
      // clip long values to the cell
      let v = cell.v;
      while (v.length > 3 && ctx.measureText(v).width > cellW - 18) {
        v = v.slice(0, -1);
      }
      if (v !== cell.v) v = v.slice(0, -1) + "…";
      ctx.fillText(v, cx + 10, y + 28);
    }
  });
  y += cellH + 16;

  // key risks
  if (d.key_risks && d.key_risks.length) {
    ctx.font = `600 10px ${SANS}`;
    if (draw) {
      ctx.fillStyle = C.faint;
      ctx.fillText("主要风险", X0, y);
    }
    y += 18;
    ctx.font = `400 12.5px ${SANS}`;
    for (const risk of d.key_risks) {
      const lines = wrapLines(ctx, risk, CONTENT_W - 16);
      lines.forEach((ln, i) => {
        if (draw) {
          if (i === 0) {
            ctx.fillStyle = C.accent;
            ctx.fillText("•", X0, y);
          }
          ctx.fillStyle = C.dim;
          ctx.fillText(ln, X0 + 16, y);
        }
        y += 19;
      });
      y += 3;
    }
    y += 13;
  }

  // ensemble row
  if (d.ensemble) {
    const e = d.ensemble;
    const txt = `${e.agree ? "✓ 集成一致" : "⚠ 集成分歧"} · ${e.provider} 给出 ${e.action}（确信 ${e.conviction}/5）`;
    ctx.font = `400 11.5px ${SANS}`;
    const lines = wrapLines(ctx, txt, CONTENT_W - 20);
    const boxH = lines.length * 17 + 16;
    if (draw) {
      ctx.fillStyle = C.bg2;
      roundRect(ctx, X0, y, CONTENT_W, boxH, 6);
      ctx.fill();
      ctx.strokeStyle = e.agree ? C.upBorder : "rgba(255,176,46,0.45)";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      roundRect(ctx, X0 + 0.5, y + 0.5, CONTENT_W - 1, boxH - 1, 6);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = C.dim;
      lines.forEach((ln, i) => ctx.fillText(ln, X0 + 10, y + 8 + i * 17));
    }
    y += boxH + 14;
  }

  // footer: divider + pills + timestamp
  if (draw) {
    ctx.strokeStyle = C.borderSoft;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(X0, y + 0.5);
    ctx.lineTo(X0 + CONTENT_W, y + 0.5);
    ctx.stroke();
  }
  y += 14;

  const pillFont = `400 11px ${MONO}`;
  const pillH = 20;
  let px = X0;
  const pills: { text: string; fg: string; bg: string }[] = [];
  if (labels.strategyLabel)
    pills.push({ text: labels.strategyLabel, fg: C.dim, bg: C.bg3 });
  if (d.provider) pills.push({ text: d.provider, fg: C.dim, bg: C.bg3 });
  if (d.model) pills.push({ text: d.model, fg: C.dim, bg: C.bg3 });
  if (!d.data_freshness_ok)
    pills.push({ text: "数据可能过期", fg: C.warn, bg: "rgba(255,176,46,0.12)" });
  for (const p of pills) {
    const w = drawPill(ctx, px, y, p.text, { font: pillFont, fg: p.fg, bg: p.bg, h: pillH }, draw);
    px += w + 6;
  }
  // timestamp, right-aligned
  if (draw) {
    ctx.font = `400 10px ${SANS}`;
    ctx.fillStyle = C.faint;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(fmtDateTime(d.ts), X0 + CONTENT_W, y + pillH / 2);
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
  }
  y += pillH;

  return y;
}

export function renderDecisionCard(d: Decision, labels: CardLabels): Promise<Blob> {
  // measure pass for exact height
  const probe = document.createElement("canvas").getContext("2d");
  if (!probe) return Promise.reject(new Error("Canvas 2D 不可用"));
  const yEnd = paint(probe, false, d, labels);
  const H = Math.ceil(yEnd + PAD + M);

  const canvas = document.createElement("canvas");
  canvas.width = IMG_W * DPR;
  canvas.height = H * DPR;
  const ctx = canvas.getContext("2d");
  if (!ctx) return Promise.reject(new Error("Canvas 2D 不可用"));
  ctx.scale(DPR, DPR);

  // image background
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, IMG_W, H);
  // card panel
  ctx.fillStyle = C.card;
  roundRect(ctx, M, M, IMG_W - 2 * M, H - 2 * M, 12);
  ctx.fill();
  ctx.strokeStyle = C.border;
  ctx.lineWidth = 1;
  roundRect(ctx, M + 0.5, M + 0.5, IMG_W - 2 * M - 1, H - 2 * M - 1, 12);
  ctx.stroke();

  paint(ctx, true, d, labels);

  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("图片生成失败"));
    }, "image/png");
  });
}

export async function copyImageToClipboard(blob: Blob): Promise<void> {
  if (!navigator.clipboard || typeof ClipboardItem === "undefined") {
    throw new Error("当前浏览器不支持复制图片，请改用下载");
  }
  await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
}

export function downloadImage(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function cardFilename(d: Decision): string {
  const sym = short(d.symbol).replace(/[^\w.-]/g, "_");
  const dt = new Date(d.ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  const stamp =
    `${dt.getFullYear()}${p(dt.getMonth() + 1)}${p(dt.getDate())}` +
    `-${p(dt.getHours())}${p(dt.getMinutes())}`;
  return `stock-agent-${sym}-${d.action}-${stamp}.png`;
}
