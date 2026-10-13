import { INDICATORS, PALETTE, PARAM_LABELS } from "./indicators.js";

const LW = LightweightCharts;
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const $ = (sel) => document.querySelector(sel);

// 차트에 겹쳐 그리는 분석 선들. "+ 추가" 메뉴에서 켜고 끈다. (보조지표는 indicators.js)
const LAYERS = {
  levels: { name: "지지/저항선", params: { count: 1 }, paramLabel: { count: "위아래 개수" },
    desc: "과거에 여러 번 방향이 바뀐 가격대. 현재가 아래에 있으면 지지, 위에 있으면 저항이에요 (막혔던 가격을 뚫고 올라오면 지지로 바뀌어요). 가장 가까운 선만 진하게 보여요." },
  extremes: { name: "52주 최고·최저", desc: "최근 1년(일봉이 아니면 보고 있는 기간) 중 가장 높았던 가격과 가장 낮았던 가격. 지지/저항선이 안 잡히는 신고가 근처에서도 기준이 돼요." },
  trend: { name: "자동 추세선", desc: "저점끼리 이은 상승 추세선, 고점끼리 이은 하락 추세선. 비스듬한 지지/저항이에요. 두 점만 이은 선은 '임시'로 표시해요 (세 번째로 닿아야 확인된 선)." },
  channel: { name: "추세 채널", desc: "추세선과, 그 반대편 끝에 평행하게 그은 선으로 만든 통로. 천장·바닥에서 막히거나 튕길 거라고 보는 사람이 많지만, 과거 검증에선 무작위로 그은 선과 차이가 없었어요." },
  avg: { name: "내 평단", desc: "토스 계좌에 보유 중인 종목이면 내 매수 평균가를 흰색 점선으로 보여줘요." },
  drawings: { name: "내가 그은 선", desc: "✏️/― 버튼으로 직접 그은 선. 채널은 같은 폭으로 위아래 층을 복사하고 층마다 4등분 점선을 넣어요. 관심종목이면 닿을 때 알림이 와요." },
  volume: { name: "거래량", desc: "그날 거래된 주식 수. 큰 움직임에 거래량이 같이 터지면 신뢰도가 높다고 봐요." },
  news: { name: "뉴스 마커", desc: "뉴스가 나온 날에 점을 찍어요. 빨강=호재, 파랑=악재, 회색=중립." },
  patterns: { name: "차트 패턴", desc: "상승형(이중 바닥·역헤드앤숄더·상승 삼각수렴·상승 깃발형·컵앤핸들)과 하락형(쌍봉·헤드앤숄더·하락 삼각수렴) 모양을 찾아 노란 선으로 그려요. 점선은 돌파(이탈)선, 가는 점선은 무효선. 실제로 맞는지는 '차트 패턴' 탭의 검증 숫자를 같이 보세요." },
  signals: { name: "차트 신호", desc: "추세선 돌파·신고가·패턴 돌파 같은 일이 생긴 날에 화살표를 찍어요 (빨강=상승 쪽, 파랑=하락 쪽). 사라·팔라는 뜻이 아니에요. 이름은 최근 3개에만 붙고, 화살표에 마우스를 올리면 설명이 떠요. 종류는 '신호 설정'에서 골라요." },
};
// 차트에 기본으로 표시할 신호: 드물게 나오는 큰 신호만. 자주 나오는 것(MACD·거래량·RSI·20·60일 크로스)은 '신호 설정'에서 켠다
const FREQUENT_SIGNALS = ["rsi_os_exit", "rsi_ob_exit", "gc", "dc"];
const DEFAULT_SIG_SHOW = ["gc_long", "dc_long", "high52", "low52", "tl_up", "tl_down", "ch_up", "ch_down",
  "pat_db", "pat_ihs", "pat_asc", "pat_flag", "pat_cup", "pat_dt", "pat_hs", "pat_desc"];
const BEAR_PATTERNS = ["pat_dt", "pat_hs", "pat_desc"];
const DEFAULT_LAYERS = { levels: { count: 1 }, trend: {}, avg: {}, drawings: {}, volume: {}, patterns: {} };

const store = {
  load(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
  },
  save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* 저장 못 해도 동작엔 지장 없음 */ }
  },
};
const saveIndicators = () => store.save("stocking.indicators", state.indicators);
const saveLayers = () => store.save("stocking.layers.v2", state.layers);

const state = {
  data: null, news: [], watchlist: [], watchSummary: {}, drawings: [], holdings: [], tab: "holdings",
  priceLines: [], trendSeries: [], indSeries: [], panes: [],
  indicators: store.load("stocking.indicators", []),
  layers: store.load("stocking.layers.v2", DEFAULT_LAYERS),   // { 켜진 레이어: 파라미터 }
  vis: {}, focus: null,                                        // 자동 선: 보이기 덮어쓰기, 지금 고른 선
  sigShow: store.load("stocking.sigShow", DEFAULT_SIG_SHOW),   // 차트에 표시할 신호 종류 (브라우저별)
  sigCatalog: {}, sigAlert: [],                                // 알림 받을 종류는 서버 설정 (알림은 서버가 보내니까)
  drawMode: null, pending: null,
};
const on = (layer) => layer in state.layers;
// 하락형 패턴이 생기기 전에 저장된 설정에는 새 종류가 없다 → 한 번만 켜준다 (그 뒤엔 사용자가 끈 걸 존중)
if (!store.load("stocking.sigShow.bear", false)) {
  state.sigShow = [...new Set([...state.sigShow, ...BEAR_PATTERNS])];
  store.save("stocking.sigShow", state.sigShow);
  store.save("stocking.sigShow.bear", true);
}
// 자주 나오는 신호를 기본에서 뺀 것도 예전 설정에 한 번만 반영 (그 뒤 사용자가 다시 켜면 그대로 둔다)
if (!store.load("stocking.sigShow.quiet", false)) {
  state.sigShow = state.sigShow.filter((t) => !FREQUENT_SIGNALS.includes(t));
  store.save("stocking.sigShow", state.sigShow);
  store.save("stocking.sigShow.quiet", true);
}

// ── 차트 생성 ──
const baseOptions = () => ({
  layout: { background: { color: "transparent" }, textColor: css("--muted") },
  grid: { vertLines: { color: css("--line") }, horzLines: { color: css("--line") } },
  rightPriceScale: { borderColor: css("--line"), minimumWidth: 80 },
  timeScale: { borderColor: css("--line") },
  crosshair: { mode: LW.CrosshairMode.Normal },
  localization: { locale: "ko-KR" },
  autoSize: true,
});

const chart = LW.createChart($("#chart"), baseOptions());
const candles = chart.addCandlestickSeries({
  upColor: css("--up"), downColor: css("--down"), borderVisible: false,
  wickUpColor: css("--up"), wickDownColor: css("--down"),
  // 현재가 선: 기본(1px 촘촘한 점선)은 잘 안 보여서 굵은 긴 대시. 색은 마지막 봉 색(빨강/파랑)을 따라감
  // → 흰색 짧은 대시인 '내 평단' 선과 구분
  priceLineWidth: 2, priceLineStyle: LW.LineStyle.LargeDashed,
});
const volume = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.22 } });
chart.timeScale().subscribeVisibleLogicalRangeChange((r) => r && state.panes.forEach((p) => p.chart.timeScale().setVisibleLogicalRange(r)));
window.__app = { chart, state };   // 콘솔 디버깅용

// ── 포맷 / DOM ──
const fmtIn = (p, cur) => cur === "KRW" ? `${Math.round(p).toLocaleString("ko-KR")}원`
  : cur === "PT" ? p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })   // 지수 포인트
  : cur === "PCT" ? `${p.toFixed(3)}%`                                                                  // 금리
  : `$${p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmt = (p) => fmtIn(p, state.data?.currency);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => (k === "class" ? (node.className = v) : node.setAttribute(k, v)));
  children.forEach((c) => node.append(c));
  return node;
}

function banner(text, kind = "") {
  const b = $("#banner");
  b.hidden = !text;
  b.className = kind;
  b.textContent = text || "";
}

const toPoints = (data) => state.data.candles.map((c, i) => (data[i] == null || Number.isNaN(data[i]) ? { time: c.time } : { time: c.time, value: data[i] }));

// ── 자동으로 그은 선 (지지/저항 · 추세선 후보 · 채널) ──
// 가독성 원칙: 가격축 라벨은 현재가 + 가장 가까운 지지/저항 1개씩 + 평단 + 지금 고른 선만.
// 나머지 선은 라벨 없이 얇고 흐리게. 이름은 범례·오른쪽 목록에서.
// (별점 신뢰도는 뺐다: 백테스트에서 별이 많은 선이 더 잘 지켜지지 않았음 — research/results/summary.md)
const faded = (hex, strong) => hex + (strong ? "" : "8c");
const kindColor = (k) => css(k === "support" ? "--support" : "--resistance");
// 추세선은 수평선과 색을 달리한다 (상승 추세선과 지지선이 같은 청록이라 구분이 안 됐다): 저점선 = 연두, 고점선 = 분홍
const trendColor = (k) => css(k === "support" ? "--trend-support" : "--trend-resistance");
const CHANNEL_COLOR = "#74c0fc";
const itemColor = (it) => (it.group === "channel" ? CHANNEL_COLOR : it.group === "trend" ? trendColor(it.kind) : kindColor(it.kind));
const PATTERN_COLOR = "#ffd43b";

// 화면에 그릴 수 있는 자동 선 전체 목록. visible 기본값: 각 레이어의 1등만
function autoItems() {
  const d = state.data;
  if (!d) return [];
  const items = [];
  const count = state.layers.levels?.count ?? 1;
  for (const kind of ["resistance", "support"]) {
    d.levels[kind].forEach((lv, i) => items.push({
      id: `lv-${kind}-${lv.price}`, group: "level", layer: "levels", kind, rank: i + 1,
      name: kind === "support" ? "지지선" : "저항선", why: lv.why, now: lv.price, price: lv.price,
      touch_points: lv.touch_points, defaultVisible: i < count, nearest: i === 0, atPrice: lv.at_price,
    }));
  }
  for (const kind of ["resistance", "support"]) {
    (d.trend_candidates?.[kind] || []).forEach((tl) => items.push({
      id: tl.id, group: "trend", layer: "trend", kind, rank: tl.rank, name: tl.label, why: tl.why,
      now: tl.price, points: tl.points, touch_points: tl.touch_points, defaultVisible: tl.rank === 1,
      tentative: tl.tentative,
    }));
  }
  if (d.channel) {
    const ch = d.channel;
    items.push({
      id: "channel", group: "channel", layer: "channel", kind: ch.base_kind, rank: 1, name: ch.label,
      why: `${ch.why}. 지금 채널 안 ${ch.position_pct.toFixed(0)}% 위치 (0%=바닥, 100%=천장)`,
      now: ch.base_kind === "support" ? ch.lower : ch.upper, channel: ch, touch_points: ch.touch_points, defaultVisible: true,
    });
  }
  return items;
}

const isVisible = (it) => on(it.layer) && (state.vis[it.id] ?? it.defaultVisible);

function renderLevels() {
  state.priceLines.forEach((l) => candles.removePriceLine(l));
  state.priceLines = [];
  if (!state.data) return;
  for (const it of autoItems().filter((x) => x.group === "level" && isVisible(x))) {
    const focused = state.focus === it.id;
    const label = focused || it.nearest;
    state.priceLines.push(candles.createPriceLine({
      price: it.price,
      // 반투명: 현재가·캔들이 선 위에서 또렷하게 보이도록 (가장 가까운 선 ~65%, 나머지 ~35%, 고른 선만 불투명)
      color: kindColor(it.kind) + (focused ? "" : it.nearest ? "a6" : "59"),
      lineWidth: focused ? 4 : it.nearest ? 3 : 1,
      lineStyle: focused || it.nearest ? LW.LineStyle.Solid : LW.LineStyle.Dashed,
      axisLabelVisible: label,
      axisLabelColor: kindColor(it.kind),          // 가격 라벨은 진하게 그대로
      axisLabelTextColor: "#111",
      title: label ? (it.kind === "support" ? "지지" : "저항") : "",
    }));
  }
  // 52주(또는 보고 있는 기간) 최고·최저: 회색 점선
  const ex = state.data.extremes;
  if (ex && on("extremes")) for (const [side, word] of [["high", "최고"], ["low", "최저"]]) state.priceLines.push(candles.createPriceLine({
    price: ex[side].price, color: "#adb5bd", lineWidth: 1, lineStyle: LW.LineStyle.LargeDashed, axisLabelVisible: true,
    title: `${ex.label} ${word}`,
  }));
  // 보유 중이면 내 평단
  const held = heldOf(state.data.symbol);
  if (held && on("avg")) state.priceLines.push(candles.createPriceLine({
    // 흰색 점선 (라이트 모드에선 글자색을 따라 검정 계열 → 배경에 묻히지 않게)
    price: held.avg_price, color: css("--text"), lineWidth: 2, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true,
    title: `내 평단 ${held.pl_rate >= 0 ? "+" : ""}${held.pl_rate.toFixed(1)}%`,
  }));
  // 내가 그은 수평선
  if (on("drawings")) state.drawings.filter((d) => d.type === "horizontal").forEach((d) => state.priceLines.push(candles.createPriceLine({
    price: d.p1.price, color: css("--accent"), lineWidth: 2, lineStyle: LW.LineStyle.Solid, axisLabelVisible: true, title: "내 선",
  })));
}

// ── 추세선 (자동 + 내가 그은 것) ──
// 내가 그은 선 → [{ label, points, now }]. 채널은 기준선 + 평행선 두 줄. (서버 analysis.drawing_values 와 같은 계산)
function drawingLines(d) {
  const times = state.data.candles.map((c) => c.time);
  if (d.type === "horizontal") return [{ label: "수평선", points: null, now: d.p1.price }];
  const i1 = times.indexOf(d.p1.time), i2 = times.indexOf(d.p2.time);
  if (i1 < 0 || i2 < 0 || i1 === i2) return [];
  const slope = (d.p2.price - d.p1.price) / (i2 - i1);
  const last = times.length - 1;
  const seg = (offset) => [{ time: times[i1], value: d.p1.price + offset }, { time: times[last], value: d.p1.price + offset + slope * (last - i1) }];
  if (d.type === "trend") return [{ label: "추세선", points: seg(0), now: seg(0)[1].value }];
  const i3 = times.indexOf(d.p3?.time);
  const offset = i3 < 0 ? 0 : d.p3.price - (d.p1.price + slope * (i3 - i1));
  return [{ label: "채널 기준선", points: seg(0), now: seg(0)[1].value },
          { label: "채널 평행선", points: seg(offset), now: seg(offset)[1].value }];
}

// 층 채널: 기준선(p1→p2)과 평행선(p3) 사이 폭을 그대로 위아래로 복사하고, 층마다 4등분 점선을 넣는다.
// 반환: { layer(현재가가 있는 층: 0 = 기준선~평행선), pos(그 층 안 위치 0~1), lines: [{ points, quarter }] } — 기준선·평행선 자체는 뺀 나머지
const MAX_LAYERS = 8;      // 가격이 지나간 범위가 이보다 많은 층에 걸치면(=폭이 너무 좁으면) 층을 쌓지 않는다
function channelLayers(d) {
  const cs = state.data.candles, times = cs.map((c) => c.time);
  const i1 = times.indexOf(d.p1.time), i2 = times.indexOf(d.p2?.time), i3 = times.indexOf(d.p3?.time);
  if (i1 < 0 || i2 < 0 || i3 < 0 || i1 === i2) return null;
  const slope = (d.p2.price - d.p1.price) / (i2 - i1);
  const at = (i) => d.p1.price + slope * (i - i1);
  const off = d.p3.price - at(i3);
  if (Math.abs(off) < 1e-9) return null;
  const last = cs.length - 1;
  let lo = 0, hi = 1;                                   // 가격이 지나간 범위 (층 단위)
  for (let i = i1; i <= last; i++) for (const v of [cs[i].low, cs[i].high]) {
    const u = (v - at(i)) / off;
    lo = Math.min(lo, u); hi = Math.max(hi, u);
  }
  const u = (cs[last].close - at(last)) / off;
  const k0 = Math.floor(lo), k1 = Math.ceil(hi);
  // 폭이 좁은 채널은 층을 쌓으면 선 수십 개가 차트를 덮는다 → 기준선·평행선만 그린다
  if (k1 - k0 > MAX_LAYERS) return { layer: Math.floor(u), pos: u - Math.floor(u), lines: [], narrow: true };
  const seg = (k) => [{ time: times[i1], value: at(i1) + k * off }, { time: times[last], value: at(last) + k * off }];
  const lines = [];
  for (let k = k0; k <= k1; k++) {
    if (k !== 0 && k !== 1) lines.push({ points: seg(k), quarter: false });
    if (k < k1) for (const q of k1 - k0 > 5 ? [0.5] : [0.25, 0.5, 0.75]) lines.push({ points: seg(k + q), quarter: true });   // 층이 많으면 가운데 선만
  }
  return { layer: Math.floor(u), pos: u - Math.floor(u), lines: lines.filter((ln) => ln.points.every((pt) => pt.value > 0)) };
}

// '채널 긋기' 클릭을 가까운 봉의 고가·저가에 붙인다 (초보자가 꼬리 끝을 정확히 찍기 어렵다)
function snapToBar(idx, price, x, y) {
  const cs = state.data.candles;
  let best = null;
  for (let i = Math.max(0, idx - 3); i <= Math.min(cs.length - 1, idx + 3); i++) for (const v of [cs[i].high, cs[i].low]) {
    const px = chart.timeScale().logicalToCoordinate(i), py = candles.priceToCoordinate(v);
    if (px == null || py == null) continue;
    const dist = Math.hypot(px - x, py - y);
    if (dist <= 16 && (!best || dist < best.dist)) best = { dist, idx: i, price: v };
  }
  return best || { idx, price };
}

function renderTrendlines() {
  state.trendSeries.forEach((s) => chart.removeSeries(s));
  state.trendSeries = [];
  if (!state.data) return;
  // quiet: 가격축 자동 범위에 영향을 주지 않는 선 (층 선이 차트를 위아래로 벌려 놓지 않게)
  const line = (points, color, { width = 2, style = LW.LineStyle.Solid, title = "", quiet = false } = {}) => {
    const s = chart.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false,
      lastValueVisible: !!title, title, crosshairMarkerVisible: false, ...(quiet ? { autoscaleInfoProvider: () => null } : {}) });
    s.setData(points);
    state.trendSeries.push(s);
    return s;
  };
  const items = autoItems();
  const shownTrendIds = new Set();
  for (const it of items.filter((x) => x.group === "trend" && isVisible(x))) {
    const focused = state.focus === it.id;
    line(it.points, faded(trendColor(it.kind), focused || it.rank === 1), {
      width: focused ? 3 : it.rank === 1 ? 2 : 1, style: focused ? LW.LineStyle.Solid : LW.LineStyle.LargeDashed,
      title: focused ? it.name : "",
    });
    shownTrendIds.add(it.id);
  }
  const chItem = items.find((x) => x.group === "channel");
  if (chItem && isVisible(chItem)) {
    const ch = chItem.channel, focused = state.focus === "channel";
    const baseShown = shownTrendIds.has(`auto-${ch.base_kind}`);
    const color = baseShown ? trendColor(ch.base_kind) : CHANNEL_COLOR;
    // 기준 추세선이 이미 그려져 있으면 평행선만 같은 색 점선으로 → 어느 선의 짝인지 한눈에
    if (!baseShown) line(ch.base, color, { width: focused ? 3 : 2 });
    line(ch.other, color, { width: focused ? 3 : 2, style: LW.LineStyle.Dotted, title: focused ? "평행선" : "" });
  }
  // 차트 패턴 (노랑: 지표·지지저항 색과 안 겹침). 고른 패턴만 굵게, 아무것도 안 골랐으면 전부 보통
  if (on("patterns") && isDaily()) {
    for (const p of state.data.patterns || []) {
      if (state.patFocus && state.patFocus !== p.id) continue;
      for (const ln of p.lines) {
        const isBreak = /넥라인|저항선|지지선|테두리|윗선/.test(ln.label);
        const isInvalid = ln.label === "무효선";       // 종가가 이 선을 넘으면 깨진 모양
        line(ln.points, PATTERN_COLOR + (state.patFocus && !isInvalid ? "" : isInvalid ? "99" : "cc"), {
          width: isInvalid ? 1 : state.patFocus ? 3 : 2,
          style: isInvalid ? LW.LineStyle.Dotted : isBreak ? LW.LineStyle.Dashed : LW.LineStyle.Solid,
          title: !state.patFocus ? "" : isInvalid ? "무효선" : isBreak ? `${p.name} ${p.dir === "bear" ? "이탈선" : "돌파선"}` : "" });
      }
    }
  }
  // 고른 선이 실제로 닿은 지점 (근거)
  const f = items.find((x) => x.id === state.focus);
  if (f?.touch_points?.length && isVisible(f)) {
    const seen = new Set();
    const pts = f.touch_points.filter((p) => !seen.has(p.time) && seen.add(p.time))
      .sort((a, b) => (a.time < b.time ? -1 : 1));
    const dots = chart.addLineSeries({ color: itemColor(f), lineVisible: false,
      pointMarkersVisible: true, pointMarkersRadius: 5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    dots.setData(pts);
    state.trendSeries.push(dots);
  }
  if (!isDaily()) return renderLegend();                           // 비스듬한 선은 날짜로 저장돼서 일봉에서만
  const accent = css("--accent");
  const drawChannel = (d, strong) => {
    drawingLines(d).forEach((ln) => line(ln.points, accent + (strong ? "" : "b3"), { style: strong ? LW.LineStyle.Solid : LW.LineStyle.LargeDashed }));
    for (const ln of channelLayers(d)?.lines || []) line(ln.points, accent + (ln.quarter ? "40" : strong ? "a6" : "66"), {
      width: 1, style: ln.quarter ? LW.LineStyle.Dotted : LW.LineStyle.Solid, quiet: true });
  };
  if (on("drawings")) for (const d of state.drawings.filter((w) => w.type !== "horizontal")) {
    if (d.type === "channel") drawChannel(d, true);
    else drawingLines(d).forEach((ln) => line(ln.points, accent));
  }
  // 채널 긋는 중: 추천 채널 미리보기 + 찍을 자리 ①②③
  const sg = state.drawMode === "channel" && state.hint;
  if (sg) {
    drawChannel({ type: "channel", ...sg }, false);
    [sg.p1, sg.p2, sg.p3].forEach((pt, i) => {
      const dot = chart.addLineSeries({ color: accent, lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 6,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      dot.setData([{ time: pt.time, value: pt.price }]);
      dot.setMarkers([{ time: pt.time, position: "inBar", shape: "circle", color: accent, text: String(i + 1) }]);
      state.trendSeries.push(dot);
    });
  }
  renderLegend();
}

// 차트 왼쪽 위 범례: 지금 차트에 보이는 선과 보조지표 이름·값 (가격축 라벨 대신)
function renderLegend(param) {
  const box = $("#legend");
  if (!state.data) return box.replaceChildren();
  const rows = [];
  for (const it of autoItems().filter(isVisible)) {
    if (it.group === "level" && !it.nearest && state.focus !== it.id) continue;    // 범례도 핵심만
    const color = itemColor(it);
    const row = el("span", { class: `lg${state.focus === it.id ? " on" : ""}`, title: it.why },
      el("i", { style: `background:${color}` }), `${it.name}${it.rank > 1 ? ` 후보${it.rank}` : ""} `,
      `${fmt(it.now)}`);
    row.addEventListener("click", () => setFocus(it.id));
    rows.push(row);
  }
  const seen = new Set();
  for (const e of state.indLive || []) {
    if (e.def.type !== "overlay" || seen.has(e.ind)) continue;
    seen.add(e.ind);
    const vals = state.indLive.filter((x) => x.ind === e.ind).map((x) => {
      const v = param?.seriesData?.get(x.series)?.value ?? x.series.data().at(-1)?.value;
      return v == null ? "-" : fmt(v);
    });
    rows.push(el("span", { class: "lg", title: e.def.desc },
      el("i", { style: `background:${e.ind.colors?.[0] || "var(--muted)"}` }), `${labelOf(e.ind)} `,
      el("span", { class: "v" }, vals.join(" / "))));
  }
  box.replaceChildren(...rows);
}
chart.subscribeCrosshairMove((p) => renderLegend(p.time ? p : undefined));

function setFocus(id) {
  state.focus = state.focus === id ? null : id;
  if (state.focus) state.vis[state.focus] = true;     // 고르면 보이게
  renderLevels();
  renderTrendlines();
  renderAutoLines();
}

// ── 보조지표 ──
function clearIndicators() {
  state.indSeries.forEach((s) => chart.removeSeries(s));
  state.indSeries = [];
  state.panes.forEach((p) => { p.chart.remove(); p.div.remove(); });
  state.panes = [];
  state.indLive = [];
}

// 실시간 체결로 마지막 봉이 바뀌면 지표의 마지막 값만 다시 계산해 덮어쓴다 (전체 다시 그리기 X)
let indTimer = null;
function refreshIndicatorsLive() {
  if (indTimer || !state.indLive?.length) return;
  indTimer = setTimeout(() => {                 // 체결이 몰려도 0.25초에 한 번
    indTimer = null;
    const cs = state.data.candles, i = cs.length - 1;
    const outs = new Map();
    for (const e of state.indLive) {
      if (!outs.has(e.ind)) outs.set(e.ind, e.def.calc(cs, e.ind.params));
      const v = outs.get(e.ind).lines[e.lineIdx].data[i];
      if (v == null || Number.isNaN(v)) continue;
      const pt = { time: cs[i].time, value: v };
      if (e.hist) pt.color = (v >= 0 ? css("--up") : css("--down")) + "99";
      e.series.update(pt);
    }
  }, 250);
}

function renderIndicators() {
  clearIndicators();
  renderIndicatorChips();
  if (!state.data) return;
  state.indLive = [];
  let colorIdx = 0;
  const nextColor = () => PALETTE[colorIdx++ % PALETTE.length];
  for (const ind of state.indicators) {
    const def = INDICATORS[ind.key];
    if (!def) continue;
    const out = def.calc(state.data.candles, ind.params);
    ind.colors = [];
    let target = chart;
    let first = null;
    // 아래 칸 지표는 칸이 따로라 색이 겹칠 일이 없다 → 칸마다 첫 색(보라)부터.
    // (차트 위 지표와 순서를 같이 세면, 이동평균선 3줄 뒤에 넣은 이격도·RSI 가 회색으로 나온다)
    let paneIdx = 0;
    const pickColor = def.type === "pane" ? () => PALETTE[paneIdx++ % PALETTE.length] : nextColor;
    if (def.type === "pane") {
      const div = el("div", { class: "pane" }, el("span", { class: "pane-label" }, labelOf(ind)));
      $("#panes").append(div);
      target = LW.createChart(div, { ...baseOptions(), timeScale: { borderColor: css("--line"), visible: false } });
      state.panes.push({ chart: target, div });
    }
    for (const [li, ln] of out.lines.entries()) {
      // 사용자가 고른 색(ind.color)이 있으면 첫 줄에, 볼린저밴드처럼 같은 계열 선들(상단·중심·하단)엔 전부
      const auto = pickColor();
      const color = ind.color && (li === 0 || ind.key === "bb") ? ind.color : auto;
      ind.colors.push(color);
      // 차트 위 지표는 가격축 라벨 대신 범례로 (가격축이 복잡해지지 않게)
      const common = { priceLineVisible: false, lastValueVisible: def.type === "pane", title: "" };
      const s = ln.type === "histogram"
        ? target.addHistogramSeries({ ...common })
        : target.addLineSeries({ ...common, color, lineWidth: 2, lineStyle: ln.dashed ? LW.LineStyle.Dashed : LW.LineStyle.Solid, crosshairMarkerVisible: false });
      const pts = toPoints(ln.data);
      if (ln.type === "histogram") pts.forEach((p) => p.value != null && (p.color = (p.value >= 0 ? css("--up") : css("--down")) + "99"));
      s.setData(pts);
      if (def.type === "overlay") state.indSeries.push(s);
      state.indLive.push({ ind, def, lineIdx: out.lines.indexOf(ln), series: s, hist: ln.type === "histogram" });
      first ??= s;
    }
    (out.bands || []).forEach((b) => first.createPriceLine({ price: b.price, color: css("--muted"), lineStyle: LW.LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: b.label }));
  }
  const r = chart.timeScale().getVisibleLogicalRange();
  if (r) state.panes.forEach((p) => p.chart.timeScale().setVisibleLogicalRange(r));
  renderIndicatorChips();
  renderLegend();          // 범례 색·이름도 새 지표 상태로
}

// <input type=color> 는 #rrggbb 만 받는다 (#abc, 투명도 붙은 값 정리)
function toHex(c) {
  if (/^#[0-9a-f]{3}$/i.test(c)) return "#" + [...c.slice(1)].map((x) => x + x).join("");
  return /^#[0-9a-f]{6}/i.test(c) ? c.slice(0, 7) : "#be4bdb";
}

function labelOf(ind) {
  const def = INDICATORS[ind.key];
  const ps = Object.values(ind.params);
  return `${def.name.split(" (")[0]}${ps.length ? ` (${ps.join(", ")})` : ""}`;
}

// 레이어 하나 켜고/끄고 → 해당 그림만 다시
function applyLayers() {
  saveLayers();
  volume.applyOptions({ visible: on("volume") });
  renderLevels();
  renderTrendlines();
  renderMarkers();
  if (state.data) { renderSide(); renderAutoLines(); }
  renderIndicatorChips();
  buildIndicatorMenu();
}

function toggleLayer(key) {
  if (on(key)) delete state.layers[key];
  else state.layers[key] = { ...(LAYERS[key].params || {}) };
  applyLayers();
  if (key === "news" && on("news")) refineNews();      // 뉴스 마커의 호재/악재 색은 AI 정리가 끝나야 나온다
}

function layerChips() {
  return Object.keys(LAYERS).filter(on).map((key) => {
    const def = LAYERS[key];
    const inputs = Object.entries(state.layers[key]).map(([k, v]) => {
      const input = el("input", { type: "number", min: "1", max: "3", value: String(v), title: def.paramLabel?.[k] || k });
      input.addEventListener("change", () => {
        const num = Math.round(Number(input.value));
        if (num >= 1 && num <= 3) { state.layers[key][k] = num; applyLayers(); }
      });
      return input;
    });
    const x = el("span", { class: "x", title: "빼기" }, "×");
    x.addEventListener("click", () => toggleLayer(key));
    return el("span", { class: "ind-chip layer", title: def.desc }, def.name, ...inputs, x);
  });
}

function renderIndicatorChips() {
  $("#ind-chips").replaceChildren(...layerChips(), ...state.indicators.map((ind) => {
    const def = INDICATORS[ind.key];
    const inputs = Object.entries(ind.params).map(([k, v]) => {
      const input = el("input", { type: "number", min: "1", max: "250", step: k === "mult" ? "0.5" : "1", value: String(v), title: PARAM_LABELS[k] || k });
      input.addEventListener("change", () => {
        const num = Number(input.value);
        if (!(num > 0)) return;
        ind.params[k] = num;
        saveIndicators();
        renderIndicators();
      });
      return input;
    });
    const x = el("span", { class: "x", title: "빼기" }, "×");
    x.addEventListener("click", () => {
      state.indicators = state.indicators.filter((i) => i !== ind);
      saveIndicators();
      renderIndicators();
      buildIndicatorMenu();
    });
    // 색 고르기: 칩의 색 막대를 누르면 색상 선택. 고르는 동안 바로 반영, 다 고르면 저장
    const pick = el("input", { type: "color", class: "sw-pick", title: "선 색 바꾸기", value: toHex(ind.colors?.[0] || PALETTE[0]) });
    pick.addEventListener("input", () => {
      state.indLive.filter((e) => e.ind === ind && !e.hist && (e.lineIdx === 0 || ind.key === "bb"))
        .forEach((e) => e.series.applyOptions({ color: pick.value }));
    });
    pick.addEventListener("change", () => {
      ind.color = pick.value;
      saveIndicators();
      renderIndicators();
    });
    return el("span", { class: "ind-chip", title: def.desc },
      pick, def.name.split(" (")[0], ...inputs, x);
  }));
}

function buildIndicatorMenu() {
  const menu = $("#ind-menu");
  const layerItems = Object.entries(LAYERS).map(([key, def]) => {
    const item = el("div", { class: `item${on(key) ? " on" : ""}` },
      el("b", {}, def.name), el("span", { class: "kind" }, on(key) ? "켜짐 · 누르면 끔" : "누르면 켬"),
      el("p", {}, def.desc));
    item.addEventListener("click", (e) => { e.stopPropagation(); toggleLayer(key); });   // 여러 개 연달아 켤 수 있게 메뉴 유지
    return item;
  });
  const indItems = Object.entries(INDICATORS).map(([key, def]) => {
    const count = state.indicators.filter((i) => i.key === key).length;
    const item = el("div", { class: `item${count ? " on" : ""}` },
      el("b", {}, def.name),
      el("span", { class: "kind" }, `${def.type === "overlay" ? "차트 위" : "아래 칸"}${count ? ` · ${count}개 사용 중` : ""}`),
      el("p", {}, def.desc));
    item.addEventListener("click", () => {
      state.indicators.push({ id: Date.now().toString(36), key, params: { ...def.params } });
      saveIndicators();
      renderIndicators();
      buildIndicatorMenu();
      menu.hidden = true;
    });
    return item;
  });
  menu.replaceChildren(
    el("div", { class: "sec" }, "가격대 분석 — 누를 때마다 켜기/끄기"), ...layerItems,
    el("div", { class: "sec" }, "보조지표 — 누르면 추가 (기간 다르게 여러 개 가능), 빼기는 칩의 ×"), ...indItems);
}
$("#add-ind").addEventListener("click", (e) => { e.stopPropagation(); $("#ind-menu").hidden = !$("#ind-menu").hidden; });
document.addEventListener("click", (e) => { if (!e.target.closest(".dropdown")) $("#ind-menu").hidden = true; });

// ── 선 긋기 ──
function setDrawMode(mode) {
  state.drawMode = state.drawMode === mode ? null : mode;
  state.pending = null;
  $("#chart").classList.toggle("drawing", !!state.drawMode);
  $("#draw-trend").classList.toggle("on", state.drawMode === "trend");
  $("#draw-hline").classList.toggle("on", state.drawMode === "hline");
  $("#draw-channel").classList.toggle("on", state.drawMode === "channel");
  banner({
    trend: "추세선 시작점을 클릭하세요 (Esc 취소)",
    hline: "선을 그을 가격을 클릭하세요 (Esc 취소)",
    channel: "채널: 기준선이 지날 저점(또는 고점) 두 곳을 차례로 클릭하세요 (1/3). 클릭은 가까운 꼬리 끝에 달라붙어요. Esc 취소",
  }[state.drawMode] || (state.data?.demo ? demoText : ""));
  renderChannelHint();
}

// '채널 긋기'를 누르면: 찍을 점 3개 추천을 차트에 미리 보여주고, 그대로 쓰거나 직접 찍게 한다
function renderChannelHint() {
  const box = $("#channel-hint");
  const list = state.drawMode === "channel" && state.data && isDaily() ? state.data.channel_suggestions || [] : [];
  state.hintIdx = Math.min(state.hintIdx || 0, Math.max(list.length - 1, 0));
  state.hint = list[state.hintIdx] || null;
  box.hidden = state.drawMode !== "channel";
  if (box.hidden) { box.replaceChildren(); return renderTrendlines(); }
  if (!state.hint) {
    box.replaceChildren(el("span", { class: "muted" }, "이 차트에선 추천할 채널을 못 찾았어요. 저점 두 곳과 반대편 고점을 직접 찍어 보세요."));
    return renderTrendlines();
  }
  const sg = state.hint;
  const use = el("button", { class: "small" }, "추천대로 긋기");
  use.addEventListener("click", () => {
    if (!on("drawings")) toggleLayer("drawings");
    saveDrawings([...state.drawings, { id: Date.now().toString(36), type: "channel", p1: sg.p1, p2: sg.p2, p3: sg.p3 }]);
    setDrawMode(null);
  });
  const kids = [el("b", {}, `추천 채널 · ${sg.label}`),
    el("span", {}, `차트의 ①②가 기준선, ③이 폭이에요. ${sg.why}${sg.layers ? ` 지금까지 가격이 ${sg.layers}층에 걸쳐 움직였어요.` : ""}`), use];
  if (list.length > 1) {
    const other = list[(state.hintIdx + 1) % list.length];
    const swap = el("button", { class: "ghost small" }, `다른 추천: ${other.label}`);
    swap.addEventListener("click", () => { state.hintIdx = (state.hintIdx + 1) % list.length; renderChannelHint(); });
    kids.push(swap);
  }
  kids.push(el("span", { class: "muted" }, "마음에 안 들면 차트에서 직접 세 점을 찍으면 돼요."));
  box.replaceChildren(...kids);
  renderTrendlines();
  // 추천한 세 점이 화면 왼쪽 밖에 있으면 안 보인다 → 가장 이른 점부터 오늘까지 보이게
  const times = state.data.candles.map((c) => c.time);
  const first = Math.min(...[sg.p1, sg.p2, sg.p3].map((pt) => times.indexOf(pt.time)).filter((i) => i >= 0));
  const range = chart.timeScale().getVisibleLogicalRange();
  if (Number.isFinite(first) && range && first < range.from + 2) chart.timeScale().setVisibleLogicalRange({ from: first - 8, to: times.length + 3 });
}
const demoText = "데모 데이터예요 (실제 시세 아님). .env 에 토스 API 키를 넣으면 실제 시세로 바뀌어요.";

$("#draw-trend").addEventListener("click", () => setDrawMode("trend"));
$("#draw-hline").addEventListener("click", () => setDrawMode("hline"));
$("#draw-channel").addEventListener("click", () => setDrawMode("channel"));
document.addEventListener("keydown", (e) => e.key === "Escape" && state.drawMode && setDrawMode(null));

// chart.subscribeClick 은 빠른 연속 클릭을 더블클릭으로 묶어 삼켜서, DOM 클릭으로 직접 좌표 계산
$("#chart").addEventListener("click", (e) => {
  if (!state.drawMode || !state.data) return;
  const rect = $("#chart").getBoundingClientRect();
  const x = e.clientX - rect.left, y = e.clientY - rect.top;
  if (x > chart.timeScale().width()) return;          // 오른쪽 가격축 클릭은 무시
  const logical = chart.timeScale().coordinateToLogical(x);
  if (logical == null) return;
  const cs = state.data.candles;
  const rawIdx = Math.max(0, Math.min(cs.length - 1, Math.round(logical)));
  const rawPrice = candles.coordinateToPrice(y);
  if (rawPrice == null) return;
  // 비스듬한 선은 가까운 봉의 고가·저가에 달라붙는다 (수평선은 찍은 가격 그대로)
  const { idx, price } = state.drawMode === "hline" ? { idx: rawIdx, price: rawPrice } : snapToBar(rawIdx, rawPrice, x, y);
  const pt = { time: cs[idx].time, price: Number(price.toFixed(4)) };
  const id = Date.now().toString(36);
  if (state.drawMode === "hline") {
    saveDrawings([...state.drawings, { id, type: "horizontal", p1: pt }]);
    return setDrawMode(null);
  }
  const pending = state.pending || [];
  if (pending.length === 0) {
    state.pending = [pt];
    return banner(state.drawMode === "channel" ? "기준선 끝점을 클릭하세요 (2/3)" : "끝점을 클릭하세요 (Esc 취소)");
  }
  if (pending.length === 1) {
    if (pending[0].time === pt.time) return;
    const [p1, p2] = [pending[0], pt].sort((a, b) => (a.time < b.time ? -1 : 1));
    if (state.drawMode === "trend") {
      saveDrawings([...state.drawings, { id, type: "trend", p1, p2 }]);
      return setDrawMode(null);
    }
    state.pending = [p1, p2];
    return banner("평행선이 지나갈 곳을 클릭하세요 — 반대편 고점(또는 저점) (3/3)");
  }
  const [p1, p2] = pending;
  saveDrawings([...state.drawings, { id, type: "channel", p1, p2, p3: pt }]);
  setDrawMode(null);
});

async function saveDrawings(items) {
  const symbol = state.data.symbol;
  const res = await fetch(`/api/drawings/${encodeURIComponent(symbol)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }),
  });
  if (!res.ok) return banner("선 저장 실패", "error");
  state.drawings = (await res.json()).items;
  renderDrawings();
}

function renderDrawings() {
  renderLevels();
  renderTrendlines();
  const list = $("#drawings");
  if (!state.drawings.length) {
    return list.replaceChildren(el("li", { class: "muted" }, "차트 위 ✏️/― 버튼으로 그을 수 있어요. 관심종목이면 닿을 때 알림이 와요."));
  }
  const last = state.data.candles.at(-1).close;
  list.replaceChildren(...state.drawings.map((d) => {
    const x = el("span", { class: "x", title: "지우기" }, "×");
    x.addEventListener("click", () => saveDrawings(state.drawings.filter((w) => w.id !== d.id)));
    const name = { trend: "추세선", horizontal: "수평선", channel: "채널" }[d.type];
    const where = drawingLines(d).map((ln) => `${fmt(ln.now)} (${((ln.now / last - 1) * 100).toFixed(1)}%)`).join(" / ") || "-";
    // 채널: 현재가가 몇 층에 있는지 (1층 = 기준선~평행선 사이)
    const ly = d.type === "channel" ? channelLayers(d) : null;
    const floor = !ly ? "" : ly.narrow ? " · 폭이 좁아 층은 안 그려요"
      : ` · 현재가 ${ly.layer >= 0 ? `${ly.layer + 1}층` : `기준선 밖 ${-ly.layer}층`}의 ${(ly.pos * 100).toFixed(0)}% 위치`;
    return el("li", {}, el("span", {}, `${name} · 오늘 ${where}${floor}`), x);
  }));
}

// ── 차트 마커 (뉴스 + 차트 신호) ──
function newsMarkers() {
  if (!on("news") || !state.news.length || !isDaily()) return [];
  const days = state.data.candles.map((c) => c.time);
  const color = { 호재: css("--up"), 악재: css("--down"), 중립: "#8b95a1" };
  const byDay = new Map();
  for (const n of state.news) {
    const day = days.find((d) => d >= n.date);          // 휴일 뉴스는 다음 거래일에
    if (!day) continue;
    (byDay.get(day) || byDay.set(day, []).get(day)).push(n);
  }
  state.newsByDay = byDay;          // 마우스 올렸을 때 설명 상자용
  // 글자는 기사 수만 (선과 겹쳐도 읽기 쉽게). 제목은 마우스를 올리면
  return [...byDay.entries()].map(([time, list]) => {
    const tone = list.some((n) => n.sentiment === "악재") ? "악재" : list.some((n) => n.sentiment === "호재") ? "호재" : "중립";
    return { time, position: "aboveBar", shape: "circle", color: color[tone], size: 0.6, text: list.length > 1 ? `${list.length}` : "" };
  });
}

const shownSignals = () => (state.data?.signals?.events || []).filter((e) => state.sigShow.includes(e.type));

function signalMarkers() {
  if (!on("signals") || !isDaily()) return [];
  // 같은 날 여러 신호는 한 마커로 묶는다 (상승 쪽은 봉 아래, 하락 쪽은 봉 위)
  const groups = new Map();
  for (const e of shownSignals()) {
    const dir = e.dir === "info" ? (e.up_candle ? "bull" : "bear") : e.dir;
    const k = `${e.time}|${dir}`;
    (groups.get(k) || groups.set(k, []).get(k)).push(e);
  }
  // 글자는 가장 최근 신호가 난 3일에만. 나머지는 화살표만 (글자가 겹쳐 차트를 가렸다) — 자세한 건 마우스를 올리면 설명 상자에
  const named = new Set([...new Set([...groups.keys()].map((k) => k.split("|")[0]))].sort().slice(-3));
  return [...groups.entries()].map(([k, list]) => {
    const [time, dir] = k.split("|");
    const bull = dir === "bull";
    const text = !named.has(time) ? "" : list.length > 1 ? `${list[0].short} +${list.length - 1}` : list[0].short;
    return { time, position: bull ? "belowBar" : "aboveBar", shape: bull ? "arrowUp" : "arrowDown",
             color: bull ? css("--up") : css("--down"), size: text ? 0.9 : 0.6, text };
  });
}

function renderMarkers() {
  if (!state.data) return candles.setMarkers([]);
  const all = [...newsMarkers(), ...signalMarkers()].sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
  candles.setMarkers(all);
}

// 마커가 있는 날에 마우스를 올리면: 그날 신호·뉴스를 풀어서 보여주는 작은 상자
const timeKey = (t) => (typeof t === "object" && t ? `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}` : t);
chart.subscribeCrosshairMove((p) => {
  const tip = $("#tip");
  if (!p.time || !p.point || !state.data || !isDaily()) return (tip.hidden = true);
  const day = timeKey(p.time);
  const sigs = on("signals") ? shownSignals().filter((e) => e.time === day) : [];
  const news = on("news") ? state.newsByDay?.get(day) || [] : [];
  if (!sigs.length && !news.length) return (tip.hidden = true);
  tip.replaceChildren(el("div", { class: "tip-day" }, day),
    ...sigs.map((e) => el("div", { class: `tip-sig ${e.dir}` }, `${e.dir === "bear" ? "▼" : "▲"} ${e.name}${e.note ? ` · ${e.note}` : ""}`)),
    ...(news.length ? [el("div", { class: "tip-news" }, `뉴스 ${news.length}건`),
      ...news.slice(0, 3).map((n) => el("div", { class: "tip-nt" }, `[${n.sentiment}] ${n.title}`))] : []));
  tip.hidden = false;
  const wrap = $(".chart-wrap").getBoundingClientRect();
  const x = Math.min(p.point.x + 14, wrap.width - tip.offsetWidth - 8);
  const y = Math.max(8, Math.min(p.point.y + 14, wrap.height - tip.offsetHeight - 8));
  tip.style.transform = `translate(${x}px, ${y}px)`;
});

// ── 차트 패턴 탭 ──
function evidenceText(ev, bear) {
  if (!ev || ev.n == null) return "아직 검증 숫자가 없어요 (python -m research.patterns_backtest).";
  const r = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  const zero = ev.x20_lo <= 0 && ev.x20_hi >= 0;
  const word = bear ? "이탈" : "돌파";
  return `검증(80종목·약 8년, ${word} ${Math.round(ev.n).toLocaleString()}건): ${word} 후 20봉 수익이 그 종목 평소보다 ${r(ev.x20)} `
    + `(95% 구간 ${r(ev.x20_lo)} ~ ${r(ev.x20_hi)})${zero ? " — 아무 날 산 것과 차이가 없었어요." : "."}`
    + (ev.x60 != null ? ` 60봉 뒤 ${r(ev.x60)}.` : "");
}

function renderPatterns() {
  const list = $("#pattern-list");
  if (!state.data) return list.replaceChildren();
  if (!isDaily()) return list.replaceChildren(el("li", { class: "muted" }, "차트 패턴은 '일' 봉에서 찾아요"));
  const pats = state.data.patterns || [];
  const evs = state.data.pattern_evidence || {};
  if (!pats.length) {
    return list.replaceChildren(el("li", { class: "muted" }, "지금 형성 중이거나 최근 10봉 안에 돌파·이탈한 패턴이 없어요. 과거 기록은 '차트 신호' 탭에 있어요."));
  }
  list.replaceChildren(...pats.map((p) => {
    const focused = state.patFocus === p.id;
    const bear = p.dir === "bear";
    const badge = el("span", { class: `pbadge ${p.status === "형성 중" ? "form" : p.status === "실패" ? "novol" : "brk"}` }, p.status);
    const side = el("span", { class: `pbadge ${bear ? "bear" : "bull"}` }, bear ? "하락형" : "상승형");
    const vol = p.volume_confirmed == null ? "" : el("span", { class: `pbadge ${p.volume_confirmed ? "vol" : "novol"}` },
      p.volume_confirmed ? "거래량 동반" : "거래량 부족");
    const li = el("li", { class: `pat${focused ? " focus" : ""}` },
      el("b", {}, p.name, side, badge, vol, el("span", { class: "t" }, p.break_time || `${p.start}~`)),
      el("p", {}, p.why),
      el("p", { class: "muted" }, p.desc),
      el("p", { class: "ev" }, evidenceText(evs[p.type], bear)));
    li.addEventListener("click", () => {
      state.patFocus = focused ? null : p.id;
      if (!on("patterns")) toggleLayer("patterns");
      renderTrendlines(); renderPatterns();
    });
    return li;
  }));
}

// ── 차트 신호 패널 ──
// 기준선(아무 날 샀을 때)을 같이 보여준다: 오르는 종목에선 하락 신호 뒤에도 평균이 +로 나오기 때문
function statText(st, minSample = 5) {
  if (!st || !st.n20) return "이 종목에선 과거 기록이 부족해요.";
  if (st.n20 < minSample) return `이 종목에선 과거 ${st.n20}번뿐이라 숫자로 판단하기 어려워요.`;
  const r = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
  const base = st.base20 == null ? "" : ` (아무 날 샀을 때 ${r(st.base20)})`;
  return `과거 ${st.count}번 · 20거래일 뒤 평균 ${r(st.avg20)}${base} · 오른 경우 ${st.up20}/${st.n20}`;
}

function renderSignals() {
  const list = $("#signals");
  const sig = state.data?.signals;
  if (!sig) {
    $("#sig-through").textContent = "";
    return list.replaceChildren(el("li", { class: "muted" }, "차트 신호는 장 마감 확정 일봉 기준이라 '일' 봉에서 보여요"));
  }
  $("#sig-through").textContent = sig.confirmed_through ? `${sig.confirmed_through.slice(5)} 종가까지 확정` : "";
  const items = shownSignals().slice(-10).reverse();
  if (!items.length) return list.replaceChildren(el("li", { class: "muted" }, "표시할 신호가 없어요 (설정에서 종류를 골라요)"));
  list.replaceChildren(...items.map((e) => {
    const cat = state.sigCatalog[e.type] || {};
    const isNew = e.time === sig.confirmed_through;
    const li = el("li", { class: e.dir },
      el("b", {}, e.name, isNew ? el("span", { class: "new" }, "최신") : "", el("span", { class: "t" }, e.time)),
      el("p", {}, `${e.note ? `${e.note}. ` : ""}${cat.desc || ""}`),
      el("p", { class: "stat" }, statText(sig.stats[e.type], sig.min_sample)));
    li.addEventListener("click", () => focusDate(e.time));
    return li;
  }));
}

async function loadSignalSettings() {
  const res = await fetch("/api/signals/settings");
  if (!res.ok) return;
  const body = await res.json();
  state.sigCatalog = body.catalog;
  state.sigAlert = body.alert_types;
  renderSignalSettings();
}

function renderSignalSettings() {
  const box = $("#sig-settings");
  const dirColor = { bull: "var(--up)", bear: "var(--down)", info: "#8b95a1" };
  const check = (checked, onChange) => {
    const cb = el("input", { type: "checkbox" });
    cb.checked = checked;
    cb.addEventListener("change", () => onChange(cb.checked));
    return cb;
  };
  const rows = Object.entries(state.sigCatalog).map(([id, cat]) => el("tr", { title: cat.desc },
    el("td", {}, el("span", { class: "dir", style: `background:${dirColor[cat.dir]}` }), cat.name),
    el("td", {}, check(state.sigShow.includes(id), (v) => {
      state.sigShow = v ? [...state.sigShow, id] : state.sigShow.filter((s) => s !== id);
      store.save("stocking.sigShow", state.sigShow);
      if (v && !on("signals")) toggleLayer("signals");     // '차트 신호' 표시가 꺼져 있으면 체크해도 아무것도 안 보인다
      renderMarkers();
      renderSignals();
    })),
    el("td", {}, check(state.sigAlert.includes(id), async (v) => {
      const types = v ? [...state.sigAlert, id] : state.sigAlert.filter((s) => s !== id);
      const res = await fetch("/api/signals/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ alert_types: types }) });
      if (res.ok) state.sigAlert = (await res.json()).alert_types;
    }))));
  box.replaceChildren(
    el("p", { class: "muted hint" }, "빨강=상승 쪽, 파랑=하락 쪽 신호. 알림은 관심종목에서 장 마감 후 확정된 신호만 와요."),
    el("table", {}, el("tr", {}, el("th", {}, "신호"), el("th", {}, "차트"), el("th", {}, "알림")), ...rows));
}
$("#sig-settings-btn").addEventListener("click", () => { $("#sig-settings").hidden = !$("#sig-settings").hidden; });

// ── 보유종목 ──
const heldOf = (symbol) => state.holdings.find((h) => h.symbol === symbol);
const qty = (v) => v.toLocaleString("en-US", { maximumFractionDigits: 3 });   // 소수점 주식
const pct = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
const money = (v, cur) => cur === "KRW" ? `${Math.round(v).toLocaleString("ko-KR")}원` : `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

async function loadHoldings() {
  const list = $("#holdings");
  const res = await fetch("/api/holdings");
  const body = await res.json();
  if (!res.ok) return list.replaceChildren(el("li", { class: "muted" }, body.detail || "불러오기 실패"));
  if (!body.enabled) return list.replaceChildren(el("li", { class: "muted" }, "토스 API 키가 있어야 보유종목을 불러와요."));
  state.holdings = body.items;
  state.acct = body.summary;
  state.usdKrw = body.usd_krw;
  renderHoldings();
  if (state.data) { renderLevels(); renderSide(); }
}

// 종목 로고 대신 이름 첫 글자 + 종목별 고정 색
const LOGO_COLORS = ["#3182f6", "#f04452", "#12b886", "#7950f2", "#fd7e14", "#15aabf", "#e64980", "#495057"];
function logo(symbol, name) {
  let h = 0;
  for (const ch of symbol) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return el("span", { class: "logo", style: `background:${LOGO_COLORS[h % LOGO_COLORS.length]}` }, (name || symbol).slice(0, 2));
}
const cls = (v) => (v >= 0 ? "up" : "down");
const krw = (v) => `${Math.round(v).toLocaleString("ko-KR")}원`;
const signed = (v, f) => `${v >= 0 ? "+" : "-"}${f(Math.abs(v))}`;
state.holdView = store.load("stocking.holdView", "value");      // 현재가 | 평가금
state.acctCur = store.load("stocking.acctCur", "krw");          // 원 | $
state.acctFolded = store.load("stocking.acctFolded", false);    // 계좌 요약 접기

function renderAcct() {
  const a = state.acct;
  if (!a) return $("#acct").replaceChildren();
  const k = state.acctCur;
  const fmtA = (v) => (k === "krw" ? krw(v) : `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`);
  const seg = el("span", { class: "seg" }, ...[["usd", "$"], ["krw", "원"]].map(([c, label]) => {
    const btn = el("button", { class: c === k ? "on" : "" }, label);
    btn.addEventListener("click", () => { state.acctCur = c; store.save("stocking.acctCur", c); renderAcct(); });
    return btn;
  }));
  // 접기: 금액을 가리고 한 줄만 (화면 공유할 때 등). 상태 기억
  const fold = el("button", { class: "fold", title: state.acctFolded ? "펼치기" : "접기" }, state.acctFolded ? "▸" : "▾");
  fold.addEventListener("click", () => {
    state.acctFolded = !state.acctFolded;
    store.save("stocking.acctFolded", state.acctFolded);
    renderAcct();
  });
  const head = el("div", { class: "row", style: "margin-top:0" }, el("span", { class: "acct-title" }, fold, "총 평가금"),
    state.acctFolded ? el("span", { class: "muted" }, "접힘") : seg);
  $("#acct").classList.toggle("folded", state.acctFolded);
  if (state.acctFolded) return $("#acct").replaceChildren(head);
  $("#acct").replaceChildren(
    head,
    el("div", { class: "big" }, fmtA(a.market_value[k])),
    el("div", { class: "row" }, el("span", {}, "총 손익"),
      el("span", { class: cls(a.profit_rate) }, `${signed(a.profit[k], fmtA)} (${pct(a.profit_rate)})`)),
    el("div", { class: "row" }, el("span", {}, "오늘"),
      el("span", { class: cls(a.daily_rate) }, `${signed(a.daily[k], fmtA)} (${pct(a.daily_rate)})`)));
}

function renderHoldings() {
  renderAcct();
  document.querySelectorAll("[data-hv]").forEach((b) => b.classList.toggle("on", b.dataset.hv === state.holdView));
  const list = $("#holdings");
  if (!state.holdings.length) return list.replaceChildren(el("li", { class: "muted" }, "보유 종목이 없어요"));
  list.replaceChildren(...state.holdings.map((h) => {
    // 토스처럼: 평가금 보기 = 원화 평가금 + 손익(원, %) / 현재가 보기 = 현재가 + 오늘 등락
    const right = state.holdView === "value"
      ? [el("span", { class: "amt" }, krw(h.market_value_krw)),
         el("span", { class: `sub r ${cls(h.pl_rate)}` }, `${signed(h.pl_amount_krw, krw)} (${Math.abs(h.pl_rate).toFixed(2)}%)`)]
      : [el("span", { class: "amt" }, money(h.last_price, h.currency)),
         el("span", { class: `sub r ${cls(h.daily_rate)}` }, `오늘 ${pct(h.daily_rate)}`)];
    const li = el("li", { class: `row2${h.symbol === state.data?.symbol ? " active" : ""}`, title: `평단 ${money(h.avg_price, h.currency)}` },
      logo(h.symbol, h.name), el("span", { class: "nm" }, h.name), right[0],
      el("span", { class: "sub" }, `${qty(h.quantity)}주`), right[1]);
    li.addEventListener("click", () => load(h.symbol));
    return li;
  }));
}
document.querySelectorAll("[data-hv]").forEach((b) => b.addEventListener("click", () => {
  state.holdView = b.dataset.hv;
  store.save("stocking.holdView", state.holdView);
  renderHoldings();
}));

$("#list-refresh").addEventListener("click", () => (state.tab === "watch" ? loadWatchSummary() : loadHoldings()));

// ── 왜 움직였지? (최근 뉴스로 오늘 등락 이유 찾기) ──
$("#why-btn").addEventListener("click", async () => {
  const sym = state.data?.symbol;
  if (!sym) return;
  setStockTab("overview");
  const box = $("#why-box");
  box.replaceChildren(el("div", { class: "card why" }, el("b", {}, "오늘 움직임 이유 찾는 중…"),
    el("p", {}, "최근 뉴스와 시장 전체 움직임을 보고 있어요 (몇 초 걸려요)")));
  const res = await fetch(`/api/why/${encodeURIComponent(sym)}`);
  const w = await res.json();
  if (state.data?.symbol !== sym) return;
  if (!res.ok) return box.replaceChildren(el("div", { class: "card why" }, el("b", {}, "이유를 찾지 못했어요"), el("p", {}, w.detail || "")));
  const mkt = w.market_change_pct != null ? ` · ${w.market_label} ${w.market_change_pct >= 0 ? "+" : ""}${w.market_change_pct.toFixed(2)}%` : "";
  const close = el("span", { class: "x", title: "닫기" }, "×");
  close.addEventListener("click", () => box.replaceChildren());
  box.replaceChildren(el("div", { class: `card why ${w.change_pct >= 0 ? "up-b" : "down-b"}` },
    el("b", {}, `오늘 ${w.change_pct >= 0 ? "+" : ""}${w.change_pct.toFixed(2)}%${mkt}`, close),
    w.underlying ? el("p", { class: "und" }, `기초자산: ${w.underlying.label} ${w.underlying.change_pct == null ? "-" : `${w.underlying.change_pct >= 0 ? "+" : ""}${w.underlying.change_pct.toFixed(2)}%`}`
      + ` · ${w.underlying.describe}${w.underlying.note ? ` · ${w.underlying.note}` : ""}`) : "",
    el("p", {}, w.reason),
    sourceLinks(w.sources, w.found ? "근거 기사" : "참고 기사"),
    el("p", { class: "muted hint" }, "주어진 기사 안에서만 이유를 찾아요. 기사로 설명이 안 되면 못 찾았다고 말해요.")));
});

// ── 오른쪽 아이콘 탭 (내 투자 / 관심 / 최근 본 / 알림) ──
const SIDE_TITLES = { holdings: "내 투자", watch: "관심종목", recent: "최근 본 종목", market: "시장 한눈에", alerts: "알림", ask: "질문" };
function setTab(tab) {
  state.tab = tab;
  store.save("stocking.tab", tab);
  document.querySelectorAll(".rail-btn[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== tab));
  $("#side-title").textContent = SIDE_TITLES[tab];
  $("#list-refresh").hidden = !["holdings", "watch"].includes(tab);
  $("#chat-clear").hidden = tab !== "ask";
  if (tab === "alerts") $("#alert-badge").hidden = true;
  if (tab === "recent") renderRecent();
  if (tab === "market") loadMarket();
  else updateLiveSubscription();
  setCollapsed(false);
}
document.querySelectorAll(".rail-btn[data-tab]").forEach((b) => b.addEventListener("click", () => {
  // 이미 열린 탭을 다시 누르면 패널 접기 (토스처럼)
  if (state.tab === b.dataset.tab && !$("#app").classList.contains("collapsed")) return setCollapsed(true);
  setTab(b.dataset.tab);
}));
const isPhone = () => matchMedia("(max-width: 700px)").matches;
function setCollapsed(c) {
  $("#app").classList.toggle("collapsed", c);
  $("#rail-collapse").textContent = c ? "«" : "»";
  // 폰에선 '차트'가 열린 탭처럼 보이게, 목록 탭 강조는 목록이 열렸을 때만
  $("#rail-chart").classList.toggle("on", c);
  document.querySelectorAll(".rail-btn[data-tab]").forEach((b) => b.classList.toggle("on", !c && b.dataset.tab === state.tab));
  if (!isPhone()) store.save("stocking.sideCollapsed", c);      // 폰에서 접은 상태는 PC 설정에 안 남김
}
$("#rail-collapse").addEventListener("click", () => setCollapsed(!$("#app").classList.contains("collapsed")));
$("#rail-chart").addEventListener("click", () => setCollapsed(true));

// ── 종목 정보 탭 (한눈에 / 자동 선 / 신호 / 뉴스 / 내 선) ──
function setStockTab(t) {
  state.stockTab = t;
  store.save("stocking.stockTab", t);
  document.querySelectorAll(".stab").forEach((b) => b.classList.toggle("on", b.dataset.st === t));
  document.querySelectorAll("[data-stpanel]").forEach((p) => (p.hidden = p.dataset.stpanel !== t));
  if (t === "news") refineNews();              // 뉴스 탭을 열 때 AI 정리 (함수 선언이라 아래에 있어도 된다)
  if (t === "ask") loadRulesCheck();
  if (t === "judge") loadJudgments();
}
document.querySelectorAll(".stab").forEach((b) => b.addEventListener("click", () => setStockTab(b.dataset.st)));
setStockTab(store.load("stocking.stockTab", "overview"));

// ── 시장 탭 (지수·선물·국채·원자재·환율·코인) ──
function sparkline(values, up) {
  const w = 64, h = 22, min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - min) / span) * h}`).join(" ");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("class", "spark");
  const pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  pl.setAttribute("points", pts);
  pl.setAttribute("fill", "none");
  pl.setAttribute("stroke", up ? css("--up") : css("--down"));
  pl.setAttribute("stroke-width", "1.5");
  svg.append(pl);
  return svg;
}

const MACRO_CUR = { pt: "PT", pct: "PCT", usd: "USD", krw: "KRW" };
let marketTimer = null, marketRenderQueued = false;
state.market = [];
state.marketTab = store.load("stocking.marketTab", "전체");

async function loadMarket() {
  const box = $("#market-list");
  if (!state.market.length) box.replaceChildren(el("p", { class: "muted" }, "불러오는 중…"));
  const res = await fetch("/api/macro");
  const body = await res.json();
  if (!res.ok) return box.replaceChildren(el("p", { class: "muted" }, body.detail || "불러오기 실패"));
  state.market = body.groups;
  renderMarket();
  loadMarketBrief();
  updateLiveSubscription();                    // 실시간 ETF 를 토스 체결로 받기
  clearTimeout(marketTimer);
  marketTimer = setTimeout(() => state.tab === "market" && !$("#app").classList.contains("collapsed") && loadMarket(), 60000);
}

// 미국 시장 요약: 시장을 움직인 소식 + 지표 3줄. 서버가 하루에 한 번만 만들어 저장해 둔다 (그날 처음 열 때 20초쯤 걸림).
// 여기서는 15분마다 다시 물어보기만 한다 (날이 바뀌었으면 새 요약이 온다). refresh = '다시 만들기'를 눌렀을 때
state.brief = null;
state.briefFolded = store.load("stocking.briefFolded", false);
let briefAt = 0, briefBusy = false;

async function loadMarketBrief(refresh = false) {
  if (briefBusy || (!refresh && Date.now() - briefAt < 15 * 60000)) return;
  briefAt = Date.now();
  briefBusy = true;
  if (!state.brief || refresh) renderMarketBrief("시장 요약을 만드는 중… (20초쯤 걸려요)");
  try {
    const res = await fetch(`/api/macro/summary${refresh ? "?refresh=1" : ""}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "시장 요약을 불러오지 못했어요");
    if (!body.enabled) return ($("#market-brief").hidden = true);       // OPENAI_API_KEY 가 없으면 카드 자체를 안 보인다
    state.brief = body.summary;
    renderMarketBrief();
  } catch (error) {
    briefAt = 0;                                                         // 실패했으면 다음 갱신 때 다시 시도
    renderMarketBrief(state.brief ? undefined : error.message);
  } finally {
    briefBusy = false;
  }
}

function renderMarketBrief(notice) {
  const box = $("#market-brief");
  box.hidden = false;
  const b = state.brief;
  const fold = el("button", { class: "fold", title: state.briefFolded ? "펼치기" : "접기" }, state.briefFolded ? "▸" : "▾");
  fold.addEventListener("click", () => {
    state.briefFolded = !state.briefFolded;
    store.save("stocking.briefFolded", state.briefFolded);
    renderMarketBrief(notice);
  });
  const moodClass = { "위험 선호": "up", "위험 회피": "down" }[b?.mood] || "flat";
  const head = el("div", { class: "brief-head" }, el("span", { class: "brief-title" }, fold, "미국 시장 요약"),
    b ? el("span", { class: `brief-mood ${moodClass}` }, b.mood) : "");
  box.classList.toggle("folded", state.briefFolded);
  if (state.briefFolded) return box.replaceChildren(head);
  if (!b || notice) return box.replaceChildren(head, el("p", { class: "muted" }, notice || ""));
  const again = el("button", { class: "brief-again", title: "지금 소식으로 다시 만들어요 (AI 를 한 번 더 불러요)" }, "다시 만들기");
  again.addEventListener("click", () => loadMarketBrief(true));
  // 먼저 '어떤 소식이 시장을 움직였나'(근거 기사 링크), 그 아래 지표 3줄
  const drivers = (b.drivers || []).map((d) => el("li", {},
    d.after_close ? el("span", { class: "brief-tag", title: "미국 지수 기준일 장이 끝난 뒤에 나온 소식 (아직 지수에 반영 안 됨)" }, "마감 뒤") : "",
    el("b", {}, d.what), d.effect ? ` ${d.effect}` : "",
    el("div", { class: "brief-src" }, ...d.sources.map((s) =>
      el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", title: s.title }, s.source)))));
  box.replaceChildren(head,
    el("p", { class: "brief-headline" }, b.headline),
    el("div", { class: "brief-sub" }, "시장을 움직인 소식"),
    drivers.length ? el("ol", {}, ...drivers) : el("p", { class: "muted" }, "이 움직임을 설명하는 기사를 찾지 못했어요."),
    el("div", { class: "brief-sub" }, "지표"),
    el("ul", { class: "brief-nums" }, ...b.lines.map((l) => el("li", {}, l))),
    el("p", { class: "muted brief-meta" }, `${b.as_of ? `미국 지수 ${b.as_of} 종가 기준 · ` : ""}야후 파이낸스 · AI 요약`),
    el("p", { class: "muted brief-meta" }, `${b.made_at ? `${b.made_at} 작성 · ` : ""}하루 한 번 만들어요 · `, again));
}

function marketRow(it) {
  if (it.error) return el("li", { class: "muted" }, `${it.name}: 데이터 없음`);
  const up = it.change >= 0;
  // 금리는 % 가 아니라 bp(0.01%p) 로: '10년 금리 +5bp'
  const chg = it.unit === "pct" ? `${up ? "+" : ""}${it.change_bp.toFixed(1)}bp` : pct(it.change_pct);
  const name = el("span", { class: "nm" }, it.name);
  const li = el("li", { class: `mk${it.symbol === state.data?.symbol ? " active" : ""}`,
                        title: `${it.name} (${it.symbol}) · ${it.live ? "토스 실시간" : `${it.date} 기준, 야후 파이낸스`}` },
    // 실시간 줄은 이름 앞 초록 점 (묶음 제목에 '토스 실시간'), 나머지는 표시 없음
    el("span", { class: "nmw" }, it.live ? el("span", { class: "rt-dot", title: "토스 실시간" }, "●") : "", name),
    sparkline(it.spark, up),
    el("span", { class: "amt" }, fmtIn(it.last, MACRO_CUR[it.unit])),
    el("span", { class: `chg ${cls(it.change)}` }, chg));
  li.addEventListener("click", () => load(it.symbol));
  return li;
}

function renderMarket() {
  const tabs = ["전체", ...state.market.map((g) => g.group)];
  if (!tabs.includes(state.marketTab)) state.marketTab = "전체";
  const chips = el("div", { class: "mk-tabs" }, ...tabs.map((t) => {
    const b = el("button", { class: t === state.marketTab ? "on" : "" }, t);
    b.addEventListener("click", () => { state.marketTab = t; store.save("stocking.marketTab", t); renderMarket(); });
    return b;
  }));
  const groups = state.market.filter((g) => state.marketTab === "전체" || g.group === state.marketTab);
  $("#market-list").replaceChildren(chips, ...groups.flatMap((g) => [
    el("div", { class: "mk-group" }, g.group, el("span", { class: g.live ? "rt" : "delay" }, g.live ? "토스 실시간" : "야후 파이낸스")),
    el("ul", { class: "stock-list" }, ...g.items.map(marketRow)),
  ]));
}

const marketLiveSymbols = () => (state.tab === "market" ? state.market.filter((g) => g.live).flatMap((g) => g.items.map((i) => i.symbol)) : []);

// ── 최근 본 종목 ──
function rememberRecent(symbol, name) {
  const list = store.load("stocking.recent", []).filter((r) => r.symbol !== symbol);
  list.unshift({ symbol, name });
  store.save("stocking.recent", list.slice(0, 20));
  if (state.tab === "recent") renderRecent();
}
function renderRecent() {
  const items = store.load("stocking.recent", []);
  const list = $("#recent-list");
  if (!items.length) return list.replaceChildren(el("li", { class: "muted" }, "아직 본 종목이 없어요"));
  list.replaceChildren(...items.map((r) => {
    const h = heldOf(r.symbol), w = state.watchSummary[r.symbol];
    const price = h ? money(h.last_price, h.currency) : w && !w.error ? money(w.price, w.currency) : "";
    const li = el("li", { class: `row2${r.symbol === state.data?.symbol ? " active" : ""}` },
      logo(r.symbol, r.name), el("span", { class: "nm" }, r.name), el("span", { class: "amt" }, price),
      el("span", { class: "sub" }, r.symbol + (h ? " · 보유" : "") + (state.watchlist.includes(r.symbol) ? " · 관심" : "")),
      el("span", { class: "sub r" }, ""));
    li.addEventListener("click", () => load(r.symbol));
    return li;
  }));
}
// ── 검색 자동완성 ──
let suggestTimer = null, suggestSel = -1, suggestItems = [];

function renderSuggest() {
  const box = $("#suggest");
  box.hidden = !suggestItems.length;
  box.replaceChildren(...suggestItems.map((s, i) => {
    const li = el("li", { class: i === suggestSel ? "sel" : "" }, el("span", {}, s.name), el("span", { class: "sym" }, `${s.symbol} · ${s.market}`));
    li.addEventListener("mousedown", (e) => { e.preventDefault(); pick(s); });
    return li;
  }));
}

function pick(s) {
  suggestItems = [];
  renderSuggest();
  load(s.symbol);
  $("#symbol").blur();
}

$("#symbol").addEventListener("input", () => {
  clearTimeout(suggestTimer);
  const q = $("#symbol").value.trim();
  if (!q || q.includes(",")) { suggestItems = []; return renderSuggest(); }
  suggestTimer = setTimeout(async () => {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=10`);
    if (!res.ok || $("#symbol").value.trim() !== q) return;
    suggestItems = (await res.json()).items;
    suggestSel = suggestItems.length ? 0 : -1;
    renderSuggest();
  }, 150);
});
$("#symbol").addEventListener("keydown", (e) => {
  if (!suggestItems.length) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    suggestSel = (suggestSel + (e.key === "ArrowDown" ? 1 : -1) + suggestItems.length) % suggestItems.length;
    renderSuggest();
  } else if (e.key === "Escape") { suggestItems = []; renderSuggest(); }
});
$("#symbol").addEventListener("blur", () => setTimeout(() => { suggestItems = []; renderSuggest(); }, 100));

async function submitSearch() {
  const q = $("#symbol").value.trim();
  if (!q) return;
  if (q.includes(",")) {                       // 여러 개 붙여넣기 → 관심종목에 추가
    const body = await (await fetch(`/api/resolve?q=${encodeURIComponent(q)}`)).json();
    const add = body.items.map((s) => s.symbol).filter((s) => !state.watchlist.includes(s));
    if (add.length) await saveWatchlist([...state.watchlist, ...add]);
    toast({ type: "info", title: `관심종목 ${add.length}개 추가`,
      body: (body.items.map((s) => `${s.name}(${s.symbol})`).join(", ") || "없음")
        + (body.missing.length ? ` · 못 찾음: ${body.missing.join(", ")}` : "") });
    $("#symbol").value = "";
    return;
  }
  if (suggestItems.length && suggestSel >= 0) return pick(suggestItems[suggestSel]);
  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=1`);
  const hit = res.ok ? (await res.json()).items[0] : null;
  load(hit ? hit.symbol : q);
}

// ── 사이드 패널 ──
function renderSide() {
  const d = state.data;
  const cards = [...d.insights];
  const held = heldOf(d.symbol);
  if (held) {
    const near = d.levels.support[0];
    cards.unshift({
      kind: "holding",
      title: `내 평단 ${money(held.avg_price, held.currency)} · ${pct(held.pl_rate)}`,
      body: `${qty(held.quantity)}주 보유, 평가금액 ${money(held.market_value, held.currency)}.`
        + (near ? ` 가장 가까운 지지선(${fmt(near.price)})은 평단 대비 ${pct((near.price / held.avg_price - 1) * 100)} 위치예요.` : ""),
    });
  }
  // 차트에 꺼둔 레이어의 설명 카드는 숨긴다 (추세/RSI 요약은 항상)
  const layerOf = { support: "levels", resistance: "levels", alert: "levels", trendline: "trend", channel: "channel", holding: "avg" };   // 52주 최고·최저 카드는 항상
  const visible = cards.filter((c) => !layerOf[c.kind] || on(layerOf[c.kind]));
  $("#insights").replaceChildren(...visible.map((i) =>
    el("div", { class: `card ${i.kind}` }, el("b", {}, i.title), el("p", {}, i.body))));
}

// ── 오른쪽 '자동으로 그은 선' 목록: 후보 보기 · 근거 보기 · 내 선으로 쓰기 ──
const AUTO_GROUPS = [
  ["level", "수평 지지/저항선", "levels"],
  ["trend", "추세선 (후보 중 고르기)", "trend"],
  ["channel", "추세 채널", "channel"],
];

function renderAutoLines() {
  const box = $("#auto-lines");
  if (!state.data) return box.replaceChildren();
  const items = autoItems();
  const last = state.data.candles.at(-1).close;
  const sections = [];
  for (const [group, title, layer] of AUTO_GROUPS) {
    const list = items.filter((x) => x.group === group);
    const head = el("div", { class: "al-head" }, el("span", {}, title));
    if (!on(layer)) {
      const btn = el("button", { class: "ghost small" }, "차트에 켜기");
      btn.addEventListener("click", () => toggleLayer(layer));
      head.append(btn);
      sections.push(head);
      continue;
    }
    sections.push(head);
    if (!list.length) { sections.push(el("p", { class: "muted hint" }, "뚜렷한 선을 못 찾았어요")); continue; }
    for (const it of list) {
      const shown = isVisible(it), focused = state.focus === it.id;
      const color = itemColor(it);
      const eye = el("button", { class: `ghost small eye${shown ? " on" : ""}`, title: shown ? "차트에서 숨기기" : "차트에 보이기" }, shown ? "보임" : "숨김");
      eye.addEventListener("click", (e) => {
        e.stopPropagation();
        state.vis[it.id] = !shown;
        if (shown && state.focus === it.id) state.focus = null;
        renderLevels(); renderTrendlines(); renderAutoLines();
      });
      const use = el("button", { class: "ghost small", title: "내가 그은 선으로 저장 → 관심종목이면 닿을 때 알림" }, "이 선 쓰기");
      use.addEventListener("click", (e) => { e.stopPropagation(); adopt(it); });
      const dist = ((it.now / last - 1) * 100);
      const row = el("li", { class: `al${focused ? " focus" : ""}${shown ? "" : " off"}` },
        el("div", { class: "al-main" },
          el("i", { style: `background:${color}` }),
          el("span", { class: "nm" }, it.name, it.rank > 1 ? el("span", { class: "rk" }, `후보${it.rank}`) : "",
            it.tentative ? el("span", { class: "rk", title: "두 점만 이은 선. 세 번째로 닿아야 확인된 추세선으로 봐요" }, "임시") : "",
            it.atPrice ? el("span", { class: "rk", title: "현재가가 이 가격대에 걸쳐 있어요. 종가가 허용폭 넘게 벗어나야 뚫린 걸로 봐요" }, "걸쳐 있음") : ""),
          el("span", { class: "px" }, `${fmt(it.now)} ${dist >= 0 ? "+" : ""}${dist.toFixed(1)}%`)),
        focused ? el("p", { class: "why" }, `차트의 점들이 이 선이 닿은 곳이에요. ${it.why}`) : "",
        el("div", { class: "al-act" }, eye, use));
      row.addEventListener("click", () => setFocus(it.id));
      sections.push(row);
    }
  }
  box.replaceChildren(el("ul", {}, ...sections.map((s) => (s.tagName === "LI" ? s : el("li", { class: "al-sec" }, s)))));
}

async function adopt(it) {
  const d = state.data;
  const lastTime = d.candles.at(-1).time;
  const id = Date.now().toString(36);
  let drawing;
  if (it.group === "level") {
    drawing = { id, type: "horizontal", p1: { time: String(lastTime), price: it.price } };
  } else if (!isDaily()) {
    return toast({ type: "info", title: "일봉에서 쓸 수 있어요", body: "비스듬한 선은 날짜 기준으로 저장돼서 '일' 봉에서 '이 선 쓰기'를 눌러주세요." });
  } else if (it.group === "trend") {
    drawing = { id, type: "trend", p1: { time: it.points[0].time, price: it.points[0].value }, p2: { time: it.points[1].time, price: it.points[1].value } };
  } else {
    const ch = it.channel;
    drawing = { id, type: "channel", p1: { time: ch.base[0].time, price: ch.base[0].value },
      p2: { time: ch.base[1].time, price: ch.base[1].value }, p3: { time: ch.other[0].time, price: ch.other[0].value } };
  }
  if (!on("drawings")) toggleLayer("drawings");
  await saveDrawings([...state.drawings, drawing]);
  const watched = state.watchlist.includes(d.symbol);
  toast({ type: "info", title: "내 선에 추가했어요", body: watched ? "이 선에 닿거나 넘으면 알림이 와요." : "관심종목(☆)에 넣으면 이 선에 닿을 때 알림이 와요." });
}

function renderNews(payload) {
  const box = $("#news-summary");
  box.replaceChildren();
  if (!payload.source_enabled) {
    const key = state.data.market === "KR" ? "네이버 검색 API 키(NAVER_CLIENT_ID/SECRET)" : "FINNHUB_API_KEY";
    box.append(el("p", { class: "muted" }, `.env 에 ${key}를 넣으면 뉴스가 나와요.`));
  }
  if (payload.summary) {
    box.append(el("b", {}, `전반적 분위기: ${payload.summary.tone}`),
      el("ol", {}, ...payload.summary.lines.map((l) => el("li", {}, l))));
  } else if (payload.ai_pending) {
    box.append(el("p", { class: "muted" }, state.newsRefining === state.data.symbol
      ? "AI 가 이 종목과 무관한 기사를 거르고 요약하는 중…" : "아직 AI 로 정리하지 않은 기사 목록이에요."));
  } else if (payload.items.length && !payload.summary_enabled) {
    box.append(el("p", { class: "muted" }, "OPENAI_API_KEY 를 넣으면 3줄 요약과 호재/악재 태그가 붙어요."));
  }
  // 미국 종목은 한국어 기사(네이버)와 영어 기사를 탭으로 나눈다. 고른 탭은 기억해둔다
  const count = (lang) => payload.items.filter((n) => n.lang === lang).length;
  const split = state.data.market !== "KR" && payload.items.length > 0;
  let lang = store.load("stocking.newsLang", "ko");
  if (split && !count(lang)) lang = lang === "ko" ? "en" : "ko";      // 고른 쪽이 비어 있으면 있는 쪽을 보여준다
  if (split) {
    box.append(el("div", { class: "seg news-seg" }, ...[["ko", "한국 뉴스"], ["en", "미국 뉴스"]].map(([id, label]) => {
      const b = el("button", { class: id === lang ? "on" : "" }, `${label} ${count(id)}`);
      b.addEventListener("click", () => { store.save("stocking.newsLang", id); renderNews(payload); });
      return b;
    })));
  }
  const items = split ? payload.items.filter((n) => n.lang === lang) : payload.items;
  $("#news").replaceChildren(...items.map((n) => {
    const li = el("li", {},
      payload.ai_pending ? "" : el("span", { class: `tag ${n.sentiment}` }, n.sentiment),     // 호재/악재는 AI 가 정리한 뒤에
      el("a", { href: n.url, target: "_blank", rel: "noopener noreferrer" }, n.title),
      el("div", { class: "meta" }, `${n.date} · ${n.source}`));
    li.addEventListener("click", (e) => { if (e.target.tagName !== "A") focusDate(n.date); });
    return li;
  }));
  if (!payload.items.length && payload.source_enabled) $("#news").append(el("li", { class: "muted" }, "최근 뉴스가 없어요"));
}

function focusDate(date) {
  const days = state.data.candles.map((c) => c.time);
  const idx = days.findIndex((d) => d >= date);
  if (idx < 0) return;
  chart.timeScale().setVisibleLogicalRange({ from: idx - 30, to: idx + 15 });
}

// ── 로드 ──
// 봉 단위. 토스 API 는 1분·일봉만 주고 나머지는 서버가 묶어서 만든다
const INTERVALS = [
  ["1m", "1분"], ["3m", "3분"], ["5m", "5분"], ["15m", "15분"], ["30m", "30분"], ["60m", "1시간"],
  ["1d", "일"], ["1w", "주"], ["1M", "월"], ["1y", "년"],
];
state.interval = store.load("stocking.interval", "1d");
const isDaily = () => state.interval === "1d";

function renderIntervalBar() {
  $("#intervals").replaceChildren(...INTERVALS.map(([iv, label], i) => {
    const b = el("button", { class: `iv${iv === state.interval ? " on" : ""}${i === 6 ? " sep" : ""}` }, label);
    b.addEventListener("click", () => {
      if (iv === state.interval) return;
      state.interval = iv;
      store.save("stocking.interval", iv);
      renderIntervalBar();
      if (state.data) loadChart(state.data.symbol);
    });
    return b;
  }));
}

function renderPrice(price) {
  const d = state.data;
  const chg = (price / d.prev_close - 1) * 100;
  const prev = state.lastShown;
  state.lastShown = price;
  const box = $("#price");
  box.replaceChildren(fmt(price),
    el("span", { class: `chg ${chg >= 0 ? "up" : "down"}` }, `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`));
  if (prev != null && prev !== price) {           // 토스처럼 바뀔 때 깜빡임
    box.classList.remove("flash-up", "flash-down");
    void box.offsetWidth;
    box.classList.add(price > prev ? "flash-up" : "flash-down");
  }
}

// 차트만 다시 (봉 단위 바꿀 때). 뉴스는 그대로 둔다
async function loadChart(symbol) {
  banner("불러오는 중...");
  const res = await fetch(`/api/chart/${encodeURIComponent(symbol)}?interval=${state.interval}`);
  const d = await res.json();
  if (!res.ok) return banner(d.detail || "불러오기 실패", "error");
  if (state.data && state.data.symbol !== symbol && state.loading !== symbol) return;
  state.data = d;
  state.lastShown = null;
  state.vis = {};
  state.focus = null;
  state.patFocus = null;
  banner(d.demo ? demoText : "");

  chart.applyOptions({ timeScale: { timeVisible: d.intraday, secondsVisible: false } });
  state.panes.forEach((p) => p.chart.applyOptions({ timeScale: { timeVisible: d.intraday } }));
  candles.applyOptions({ priceFormat: d.currency === "KRW" ? { type: "price", precision: 0, minMove: 1 } : { type: "price", precision: 2, minMove: 0.01 } });
  // lightweight-charts 는 넘긴 객체의 time 을 자기 형식으로 바꿔버려서, 원본(날짜 문자열 비교에 씀) 대신 복사본을 준다
  candles.setData(d.candles.map((c) => ({ ...c })));
  volume.setData(d.candles.map((c) => ({ time: c.time, value: c.volume, color: (c.close >= c.open ? css("--up") : css("--down")) + "55" })));
  renderPrice(d.candles.at(-1).close);
  renderSide();
  renderIndicators();
  renderDrawings();
  renderChannelHint();
  renderMarkers();
  renderSignals();
  renderAutoLines();
  renderPatterns();
  $("#daily-only").hidden = isDaily();
  // 지수·선물·금리(야후)는 관심종목/이유찾기 대상이 아니고 토스 실시간도 없음
  $("#watch-toggle").hidden = $("#why-btn").hidden = $("#live-dot").hidden = !!d.macro;
  ["#draw-trend", "#draw-hline", "#draw-channel"].forEach((id) => ($(id).disabled = !isDaily()));
  // autoSize 가 첫 레이아웃 후에 폭을 잡기 때문에 한 프레임 뒤에 범위를 지정해야 안 찌그러진다
  requestAnimationFrame(() => setTimeout(() =>
    chart.timeScale().setVisibleLogicalRange({ from: d.candles.length - 150, to: d.candles.length + 3 }), 50));
}

async function load(symbol) {
  symbol = symbol.trim().toUpperCase();
  if (!symbol) return;
  if (state.drawMode) setDrawMode(null);
  $("#symbol").value = symbol;
  history.replaceState(null, "", `?s=${symbol}`);
  state.loading = symbol;
  if (symbol !== state.data?.symbol) $("#why-box").replaceChildren();   // 이전 종목의 '왜 움직였지?' 결과 지우기
  const dres = await fetch(`/api/drawings/${encodeURIComponent(symbol)}`);
  state.drawings = dres.ok ? (await dres.json()).items : [];
  state.news = [];
  await loadChart(symbol);
  if (state.data?.symbol !== symbol) return;
  $("#name").textContent = `${state.data.name} (${symbol})`;
  rememberRecent(symbol, state.data.name);
  if (isPhone()) setCollapsed(true);          // 폰: 목록에서 종목을 고르면 차트로 돌아감
  loadRulesCheck();                           // '내 규칙' 탭이 열려 있을 때만 (계산만, AI 아님)
  loadJudgments();                            // '판단 기록' 탭이 열려 있을 때만
  renderChatContext();
  renderWatchlist();
  renderHoldings();
  updateLiveSubscription();

  $("#news").replaceChildren(el("li", { class: "muted" }, "뉴스 불러오는 중..."));
  $("#news-summary").replaceChildren();
  state.newsPayload = null;
  const nres = await fetch(`/api/news/${encodeURIComponent(symbol)}`);
  if (state.data?.symbol !== symbol) return;               // 그 사이 다른 종목 눌렀으면 무시
  if (!nres.ok) return $("#news").replaceChildren(el("li", { class: "muted" }, "뉴스를 불러오지 못했어요"));
  showNews(await nres.json());
  refineNews();
}

// ── 판단 기록 ──
// AI 의 판단을 미리 적어 두고 기간이 지나면 시세로 채점한다 (서버 judgments.py). 채점이 쌓이기 전의 판단은 근거 없는 의견이라
// 종목별 판단은 접어 두고, 성적표를 먼저 보여준다.
const ARM_NAMES = { news: "숫자 + 기사", nonews: "숫자만" };
const DIR_NAMES = { outperform: "시장보다 강함", neutral: "판단 안 함", underperform: "시장보다 약함" };
let judgePoll = null;

async function loadJudgments() {
  if (state.stockTab !== "judge") return;
  const sym = state.data && !state.data.macro ? `?symbol=${encodeURIComponent(state.data.symbol)}` : "";
  const res = await fetch(`/api/judgments${sym}`);
  if (!res.ok) return $("#judge-status").replaceChildren(el("p", { class: "muted" }, "판단 기록을 불러오지 못했어요"));
  const j = await res.json();
  state.judge = j;
  renderJudgments();
  clearTimeout(judgePoll);
  if (j.state.running) judgePoll = setTimeout(loadJudgments, 3000);       // 남기는 중이면 진행 상황을 따라간다
}

function renderJudgments() {
  const j = state.judge, st = j.state, p = j.protocol;
  const left = j.left;
  const run = $("#judge-run");
  run.hidden = st.running || left <= 0;
  run.textContent = `이번 주 판단 남기기 (${left.toLocaleString()}건)`;
  // 비용은 거의 전부 두 번째(비싼) 모델에서 나온다 → 모델별 건수를 같이 보여준다
  const perModel = p.models.map((m) => `${m} ${j.by_model[m].toLocaleString()}건`).join(", ");
  run.title = `AI 를 ${left.toLocaleString()}번 불러요 (한 주 전체: ${perModel}). 한 건에 약 1,200 토큰이에요.`;
  const models = p.models.map((m, i) => (i === 0 ? `${m}(전 종목)` : `${m}(고정 ${p.premium}종목만)`)).join(", ");
  const lines = [
    `이번 주(${j.week}) ${j.recorded_this_week.toLocaleString()}건 기록` + (left <= 0 ? " · 이번 주는 끝났어요" : ` · ${left.toLocaleString()}건 남음` + (j.due ? "" : " · 월요일 아침 6시 이후에 남겨요")),
    `종목 ${p.universe}개 + 내 종목 · 모델 ${models} · 조건 2가지(숫자 + 기사 / 숫자만) · 채점 ${p.horizons.join("·")}거래일 뒤 (${p.bench} 대비)`,
    p.auto ? "주 1회 자동으로 남겨요." : "자동 기록은 꺼져 있어요. 버튼을 눌러야 남겨요.",
  ];
  const status = [el("ul", { class: "judge-facts" }, ...lines.map((t) => el("li", {}, t)))];
  if (st.running) status.push(el("p", { class: "judge-run" }, `남기는 중… ${st.done} / ${st.total}건 · 토큰 ${st.tokens.toLocaleString()}`));
  else if (st.finished && st.total) status.push(el("p", { class: "muted" }, `마지막 실행: ${st.done} / ${st.total}건 · 토큰 ${st.tokens.toLocaleString()}`
    + (st.errors.length ? ` · 실패 ${st.errors.length}건 (${st.errors.slice(0, 3).join(" / ")})` : "")));
  $("#judge-status").replaceChildren(...status);

  // 성적표: 주된 판정은 20거래일·고정 100종목
  const pc = (v) => (v == null ? "–" : `${v.toFixed(1)}%`);
  const sg = (v) => (v == null ? "–" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%p`);
  const table = (rows, caption) => {
    if (!rows.length) return [];
    const body = rows.map((r) => el("tr", { class: r.primary ? "primary" : "" },
      el("td", {}, `${r.model} · ${ARM_NAMES[r.arm]}`), el("td", {}, `${r.horizon}일`),
      el("td", {}, `${r.scored} / ${r.recorded}`), el("td", {}, r.calls ? `${pc(r.hit_rate_pct)} (${r.calls}건)` : "–"),
      el("td", {}, sg(r.mean_signed_excess_pct)), el("td", {}, r.scored ? `${pc(r.always_up_hit_rate_pct)} · ${sg(r.always_up_mean_excess_pct)}` : "–")));
    return [el("div", { class: "brief-sub" }, caption), el("div", { class: "judge-scroll" }, el("table", { class: "judge-table" },
      el("tr", {}, ...["모델 · 조건", "기간", "채점 / 기록", "맞힌 비율", "방향대로 초과수익", "전부 '강함'으로 찍으면"].map((h) => el("th", {}, h))), ...body))];
  };
  const anyScored = j.fixed.some((r) => r.scored);
  $("#judge-board").replaceChildren(
    ...table(j.fixed, "고정 100종목 성적 (주된 판정은 20일)"),
    ...table(j.paired || [], "모델 비교: 두 모델이 모두 판단한 종목·주만"),
    ...table(j.mine, "내 종목 성적 (참고)"),
    j.fixed.length && !anyScored ? el("p", { class: "muted hint" }, "아직 채점된 판단이 없어요. 5거래일 뒤에 첫 채점이, 20거래일 뒤에 주된 채점이 나와요.") : "",
    j.weeks.length ? el("p", { class: "muted hint" }, "쓴 토큰: " + j.weeks.map((w) => `${w.week} ${w.model} ${w.records}건 ${w.tokens.toLocaleString()}`).join(" · ")) : "");

  // 지금 보는 종목의 판단: 접어 둔다 (채점 전 의견이 매매 판단에 섞이지 않게)
  const recs = j.symbol_records || [];
  const box = $("#judge-symbol");
  if (!recs.length) return box.replaceChildren();
  const d = el("details", {}, el("summary", {}, `${state.data.name}에 대한 판단 ${recs.length}건 보기 (채점 전에는 근거 없는 의견이에요)`),
    ...recs.slice().reverse().map((r) => {
      const res = (h) => { const s = r.scored[h]; return s ? ` → 초과 ${sg(s.excess)} ${s.hit == null ? "" : s.hit ? "✓" : "✗"}` : " → 채점 대기"; };
      return el("div", { class: "judge-rec" },
        el("div", { class: "judge-rec-head" }, `${r.week} · ${r.model} · ${ARM_NAMES[r.arm]} · 기준 ${r.as_of} ${fmt(r.price)}`),
        el("div", {}, `5일: ${DIR_NAMES[r.d5]} (확신 ${r.c5})${res(5)}`), el("div", {}, `20일: ${DIR_NAMES[r.d20]} (확신 ${r.c20})${res(20)}`),
        el("div", { class: "muted" }, r.reason), el("div", { class: "muted" }, `틀렸다고 볼 조건: ${r.invalid_if}`));
    }));
  box.replaceChildren(d);
}

$("#judge-run").addEventListener("click", async () => {
  $("#judge-run").hidden = true;
  const res = await fetch("/api/judgments/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (!res.ok) toast({ type: "info", title: "판단 남기기 실패", body: (await res.json()).detail || "" });
  setTimeout(loadJudgments, 1500);
});

// ── 내 규칙 (차트 아래 탭) + 질문 (오른쪽 탭) ──
// 내 규칙: 내가 정한 조건(지지 근처·채널 하단에서 매수, 저항이 목표)에 지금 가격이 해당하는지. 서버가 계산만 한다 (AI 아님, 토큰 0)
// 질문: 물어볼 때만 AI 가 차트·뉴스·시장·보유 종목을 찾아보고 답한다. 주문 기능은 없다
async function loadRulesCheck() {
  const d = state.data;
  if (!d || state.stockTab !== "ask") return;
  const box = $("#rules-card");
  if (d.macro) return box.replaceChildren(el("p", { class: "muted" }, "지수·금리 같은 지표에는 매매 규칙을 대조하지 않아요."));
  if (state.rulesCheck?.symbol !== d.symbol) box.replaceChildren(el("p", { class: "muted" }, "규칙에 대조하는 중…"));
  // 화면에 떠 있는 실시간 가격으로 대조한다 (서버의 일봉은 몇 분씩 캐시돼서 헤더 가격과 어긋났다)
  const live = state.lastShown ? `?price=${state.lastShown}` : "";
  const res = await fetch(`/api/rules/check/${encodeURIComponent(d.symbol)}${live}`);
  if (state.data?.symbol !== d.symbol) return;
  const body = await res.json();
  if (!res.ok) return box.replaceChildren(el("p", { class: "muted" }, body.detail || "규칙을 대조하지 못했어요"));
  state.rulesCheck = body;
  renderRulesCard();
}

// 손절 ─ 지지 ─ 현재가 ─ 목표 를 한 줄 위에. 목표나 손절을 못 정했으면 그리지 않는다
function ruleLadder(r) {
  if (!r.target || !r.stop) return "";
  const pts = [["stop", "손절", r.stop.price], ...(r.support ? [["support", "지지", r.support.price]] : []),
               ["now", "현재가", r.price], ["target", "목표", r.target.price]];
  const lo = Math.min(...pts.map((p) => p[2])), hi = Math.max(...pts.map((p) => p[2]));
  const at = (v) => (hi === lo ? 50 : ((v - lo) / (hi - lo)) * 100);
  return el("div", { class: "ladder" },
    el("div", { class: "ladder-track" },
      el("div", { class: "ladder-risk", style: `left:${at(r.stop.price)}%;width:${Math.max(0, at(r.price) - at(r.stop.price))}%` }),
      el("div", { class: "ladder-gain", style: `left:${at(r.price)}%;width:${Math.max(0, at(r.target.price) - at(r.price))}%` }),
      ...pts.map(([kind, , v]) => el("i", { class: `ladder-dot ${kind}`, style: `left:${at(v)}%` }))),
    el("div", { class: "ladder-labels" }, ...pts.map(([kind, label, v]) =>
      el("span", { class: kind, style: `left:${at(v)}%` }, label))));
}

function renderRulesCard() {
  const r = state.rulesCheck;
  const sp = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
  const tile = (label, value, sub, kind = "") => el("div", { class: `rule-tile ${kind}` },
    el("span", { class: "rule-label" }, label), el("strong", {}, value), el("span", { class: "rule-sub" }, sub || ""));
  const tiles = [
    r.target ? tile(`매도 목표 · ${r.target.name}`, r.target.text, sp(r.target.pct), "target") : tile("매도 목표", "못 정함", "위에 저항선이 없어요"),
    r.stop ? tile("손절 기준", r.stop.text, sp(r.stop.pct), "stop") : tile("손절 기준", "못 정함", ""),
    tile("손익비", r.rr == null ? "-" : r.rr.toFixed(2), `내 기준 ${r.rules.min_rr} 이상`, r.rr != null && r.rr >= r.rules.min_rr ? "ok" : ""),
  ];
  const h = r.holding;
  const mine = h ? el("div", { class: "rule-mine" },
    el("span", {}, "내 평단 ", el("b", {}, h.avg_text)),
    el("span", { class: cls(h.pl_rate) }, `지금 ${sp(h.pl_rate)}`),
    h.target_from_avg_pct != null ? el("span", {}, `목표가면 ${sp(h.target_from_avg_pct)}`) : "",
    h.stop_from_avg_pct != null ? el("span", {}, `손절이면 ${sp(h.stop_from_avg_pct)}`) : "") : "";
  $("#rules-card").replaceChildren(
    el("div", { class: "rule-head" },
      el("div", {}, el("div", { class: "rule-name" }, r.name), el("div", { class: "rule-price" }, r.price_text)),
      el("span", { class: `rule-verdict ${r.tone}` }, r.verdict)),
    ruleLadder(r),
    el("div", { class: "rule-tiles" }, ...tiles),
    mine,
    el("ul", { class: "rule-checks" }, ...r.checks.map((c) => el("li", { class: c.ok ? "ok" : "" },
      el("i", {}, c.ok ? "✓" : "–"), el("b", {}, c.label), el("span", {}, c.text)))),
    el("p", { class: "rule-caution" }, r.caution));
}

const RULE_FIELDS = [
  ["buy_support_atr", "지지 근처: 지지선에서 ATR 몇 배 이내", 0.1],
  ["buy_channel_pct", "채널 하단: 채널 아래에서 몇 % 이내", 1],
  ["target_near_atr", "목표 근처: 저항선에서 ATR 몇 배 이내", 0.1],
  ["stop_atr", "손절 기준: 지지선 아래로 ATR 몇 배", 0.1],
  ["min_rr", "최소 손익비 (목표 폭 ÷ 손절 폭)", 0.1],
  ["min_risk_atr", "손익비의 최소 위험: 손절이 너무 가까우면 ATR 몇 배로 볼지 (0 = 끔)", 0.1],
];
$("#rules-edit-btn").addEventListener("click", async () => {
  const box = $("#rules-form");
  if (!box.hidden) return (box.hidden = true);
  const { rules, defaults } = await (await fetch("/api/rules")).json();
  const inputs = {};
  const fields = RULE_FIELDS.map(([key, label, step]) => {
    inputs[key] = el("input", { type: "number", step, value: rules[key] });
    return el("label", {}, el("span", {}, label), inputs[key]);
  });
  const note = el("textarea", { rows: 2, placeholder: "말로 적어두는 내 원칙 (예: 실적 발표 전 주에는 새로 안 산다). 질문할 때 AI 가 같이 읽어요." });
  note.value = rules.note;
  const save = async (values) => {
    const res = await fetch("/api/rules", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rules: values }) });
    if (!res.ok) return;
    box.hidden = true;
    loadRulesCheck();
  };
  const ok = el("button", { class: "small" }, "저장");
  ok.addEventListener("click", () => save({ ...Object.fromEntries(RULE_FIELDS.map(([k]) => [k, inputs[k].value])), note: note.value }));
  const reset = el("button", { class: "ghost small" }, "기본값으로");
  reset.addEventListener("click", () => save({ ...defaults, note: note.value }));
  box.replaceChildren(
    el("p", { class: "muted hint" }, "ATR = 이 종목이 하루에 평균적으로 움직이는 폭. 종목마다 출렁임이 달라서 % 대신 ATR 배수로 정해요."),
    ...fields, el("label", {}, el("span", {}, "내 원칙 메모"), note), el("div", { class: "rule-actions" }, ok, reset));
  box.hidden = false;
});

// AI 답은 **굵게** 와 '- 목록' 정도만 쓴다 → 그것만 안전하게 그린다 (HTML 로 넣지 않는다)
function richText(text) {
  return text.split("\n").filter((l) => l.trim()).map((line) => {
    const item = /^\s*[-•]\s+/.test(line);
    const node = el("div", { class: item ? "chat-li" : "" });
    line.replace(/^\s*[-•]\s+/, "").split("**").forEach((part, i) => part && node.append(i % 2 ? el("b", {}, part) : part));
    return node;
  });
}

state.chat = [];
const CHAT_STARTERS = ["지금 내 규칙으로 어때?", "관심종목 중 내 규칙에 맞는 건?", "내 규칙이 이 종목 과거엔 어땠어?", "내 계좌 비중 정리해줘",
  "오늘 왜 움직였어?", "목표가에 알림 걸어줘"];
function renderChat(pending) {
  const log = $("#chat-log");
  log.hidden = !state.chat.length && !pending;
  log.replaceChildren(...state.chat.map((m) => el("div", { class: `chat-msg ${m.role}` },
    ...(m.role === "user" ? [m.content] : richText(m.content)),
    ...(m.actions || []).map(actionCard),
    m.meta ? el("div", { class: "chat-meta" }, m.meta) : "")),
    pending ? el("div", { class: "chat-msg assistant pending" }, el("i", {}), el("i", {}), el("i", {})) : "");
  $("#chat-starters").hidden = state.chat.length > 0;          // 대화가 시작되면 추천 질문은 접는다
  $("#chat-send").disabled = !!pending;
  log.lastElementChild?.scrollIntoView({ block: "nearest" });
}
// AI 가 '대신 해줄 일'은 여기 버튼으로만 실행된다 (관심종목·수평선 알림·내 규칙). 누르기 전에는 아무것도 바뀌지 않는다
function actionCard(a) {
  if (a.state === "done") return el("div", { class: "chat-action done" }, `✓ ${a.label}`);
  if (a.state === "cancelled") return el("div", { class: "chat-action cancelled" }, `취소함 · ${a.label}`);
  const ok = el("button", { type: "button", class: "small" }, a.state === "busy" ? "실행 중…" : "확인");
  const no = el("button", { type: "button", class: "ghost small" }, "취소");
  ok.disabled = no.disabled = a.state === "busy";
  ok.addEventListener("click", () => runAction(a));
  no.addEventListener("click", () => { a.state = "cancelled"; renderChat(); });
  return el("div", { class: "chat-action" }, el("div", { class: "chat-action-label" }, a.label),
    a.error ? el("div", { class: "chat-action-error" }, a.error) : "", el("div", { class: "chat-action-btns" }, ok, no));
}

async function runAction(a) {
  a.state = "busy"; a.error = "";
  renderChat();
  try {
    const res = await fetch("/api/agent/act", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: a.kind, symbol: a.symbol, price: a.price, changes: a.changes }) });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "실행하지 못했어요");
    a.state = "done";
    // 바뀐 것을 화면에 반영: 관심종목 / 지금 보는 종목의 선 / 내 규칙 카드
    if (a.kind !== "rules_update") {
      state.watchlist = (await (await fetch("/api/watchlist")).json()).symbols;
      renderWatchlist();
      loadWatchSummary();
      updateLiveSubscription();
    }
    if (a.kind === "alert_line" && a.symbol === state.data?.symbol) {
      state.drawings = (await (await fetch(`/api/drawings/${encodeURIComponent(a.symbol)}`)).json()).items;
      if (!on("drawings")) toggleLayer("drawings");
      renderDrawings();
    }
    if (a.kind === "rules_update") loadRulesCheck();
  } catch (error) {
    a.state = ""; a.error = error.message;
  }
  renderChat();
}

// 종목을 말하지 않고 물으면 '지금 보고 있는 종목' 얘기로 알아듣는다 → 그게 뭔지 보여준다
function renderChatContext() {
  const d = state.data;
  $("#chat-context").replaceChildren(...(d && !d.macro
    ? [el("span", { class: "muted" }, "지금 보는 종목"), el("b", {}, `${d.name} (${d.symbol})`)]
    : [el("span", { class: "muted" }, "지금은 종목 없이 물어봐요 (종목 이름을 같이 말해주세요)")]));
}
function openChat() {
  setTab("ask");
  if (!isPhone()) $("#chat-input").focus();
}
$("#ask-open").addEventListener("click", openChat);
$("#chat-clear").addEventListener("click", () => { state.chat = []; renderChat(); });
$("#chat-starters").replaceChildren(...CHAT_STARTERS.map((q) => {
  const b = el("button", { type: "button", class: "chip" }, q);
  b.addEventListener("click", () => askAgent(q));
  return b;
}));
renderChat();

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  input.value = "";
  askAgent(text);
});

async function askAgent(text) {
  if (!text || state.chatBusy) return;
  state.chatBusy = true;
  state.chat.push({ role: "user", content: text });
  renderChat(true);
  try {
    const res = await fetch("/api/agent/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: state.chat.map(({ role, content }) => ({ role, content })), symbol: state.data?.macro ? null : state.data?.symbol }) });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "답을 받지 못했어요");
    state.chat.push({ role: "assistant", content: body.answer, actions: body.actions || [],
      meta:`${body.steps.length ? `찾아본 것: ${body.steps.join(" → ")} · ` : ""}토큰 ${body.tokens.toLocaleString()}` });
  } catch (error) {
    state.chat.push({ role: "assistant", content: error.message, meta: "오류" });
  } finally {
    state.chatBusy = false;
    renderChat();
  }
}

function showNews(payload) {
  state.newsPayload = payload;
  state.news = payload.items;
  renderNews(payload);
  renderMarkers();
}

// AI 로 무관한 기사를 거르고 요약하는 건 토큰이 든다 → 차트만 볼 땐 안 하고, 뉴스를 실제로 볼 때만 한다
// (뉴스 탭을 열었거나, 차트에 뉴스 마커를 켜서 호재/악재 색이 필요할 때)
async function refineNews() {
  const symbol = state.data?.symbol;
  if (!state.newsPayload?.ai_pending || state.newsRefining === symbol) return;
  if (state.stockTab !== "news" && !on("news")) return;
  state.newsRefining = symbol;
  renderNews(state.newsPayload);                           // 'AI 가 정리하는 중…' 표시
  try {
    const res = await fetch(`/api/news/${encodeURIComponent(symbol)}?ai=1`);
    if (res.ok && state.data?.symbol === symbol) showNews(await res.json());
  } finally {
    state.newsRefining = null;
    if (state.data?.symbol === symbol && state.newsPayload?.ai_pending) renderNews(state.newsPayload);   // 실패: 안내 문구로 되돌림
  }
}

// ── 관심종목 ──
async function saveWatchlist(symbols) {
  const res = await fetch("/api/watchlist", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbols }) });
  state.watchlist = (await res.json()).symbols;
  renderWatchlist();
  loadWatchSummary();
  updateLiveSubscription();
}

let summaryReq = 0;
async function loadWatchSummary() {
  const req = ++summaryReq;
  const res = await fetch("/api/watchlist/summary");
  if (!res.ok || req !== summaryReq) return;
  state.watchSummary = Object.fromEntries((await res.json()).items.map((i) => [i.symbol, i]));
  renderWatchlist();
}

function renderWatchlist() {
  const cur = state.data?.symbol;
  $("#watch-count").textContent = state.watchlist.length || "";
  const list = $("#watch-list");
  if (!state.watchlist.length) {
    list.replaceChildren(el("li", { class: "muted" }, "아직 관심종목이 없어요"));
  } else list.replaceChildren(...state.watchlist.map((s) => {
    const info = state.watchSummary?.[s];
    const ok = info && !info.error;
    const x = el("span", { class: "x", title: "관심종목에서 빼기" }, "×");
    x.addEventListener("click", (e) => { e.stopPropagation(); saveWatchlist(state.watchlist.filter((w) => w !== s)); });
    const dist = ok ? [
      info.support_pct != null ? `지지 ${info.support_pct.toFixed(1)}%` : null,
      info.resistance_pct != null ? `저항 +${info.resistance_pct.toFixed(1)}%` : null,
    ].filter(Boolean).join(" · ") : "";
    const li = el("li", { class: `row2${s === cur ? " active" : ""}` },
      logo(s, info?.name), el("span", { class: "nm" }, info?.name || s),
      el("span", { class: "amt" }, ok ? money(info.price, info.currency) : info?.error ? "불러오기 실패" : "…", x),
      el("span", { class: "sub" }, `${s}${dist ? ` · ${dist}` : ""}`),
      el("span", { class: `sub r ${ok ? cls(info.change_pct) : ""}` }, ok ? pct(info.change_pct) : ""));
    li.addEventListener("click", () => load(s));
    return li;
  }));
  const starred = state.watchlist.includes(cur);
  $("#watch-toggle").className = `ghost${starred ? " on" : ""}`;
  $("#watch-toggle").textContent = starred ? "★ 관심종목" : "☆ 관심종목";
}

$("#watch-toggle").addEventListener("click", () => {
  const s = state.data?.symbol;
  if (!s) return;
  saveWatchlist(state.watchlist.includes(s) ? state.watchlist.filter((w) => w !== s) : [...state.watchlist, s]);
});

// ── 알림 ──
const timeOf = (iso) => iso.slice(5, 16).replace("T", " ");

// 급등락 알림 등에 붙는 근거 기사 링크 (클릭해도 종목 이동은 안 함)
function sourceLinks(sources, label = "근거 기사") {
  if (!sources?.length) return "";
  return el("div", { class: "srcs" }, el("span", { class: "muted" }, `${label} `),
    ...sources.map((s, i) => {
      const a = el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", title: `${s.date} · ${s.source}` }, `[${i + 1}] ${s.title}`);
      a.addEventListener("click", (e) => e.stopPropagation());
      return a;
    }));
}

function alertItem(ev) {
  const li = el("li", { class: `ev-${ev.type}` },
    el("b", {}, ev.title, el("span", { class: "t" }, timeOf(ev.at))),
    el("p", {}, ev.body), sourceLinks(ev.sources, ev.found === false ? "참고 기사" : "근거 기사"));
  li.addEventListener("click", () => load(ev.symbol));
  return li;
}

function toast(ev) {
  const card = el("div", { class: `card ev-${ev.type}` }, el("b", {}, ev.title), el("p", {}, ev.body),
    sourceLinks(ev.sources, ev.found === false ? "참고 기사" : "근거 기사"));
  card.addEventListener("click", () => { if (ev.symbol) load(ev.symbol); card.remove(); });
  $("#toasts").prepend(card);
  setTimeout(() => card.remove(), 12000);
  if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
    const n = new Notification(ev.title, { body: ev.body, tag: ev.key });
    n.onclick = () => { window.focus(); load(ev.symbol); };
  }
}

function onAlert(ev) {
  $("#alerts").querySelector(".muted")?.remove();
  $("#alerts").prepend(alertItem(ev));
  if (state.tab !== "alerts" || $("#app").classList.contains("collapsed")) $("#alert-badge").hidden = false;
  toast(ev);
  if (ev.symbol === state.data?.symbol) $("#chart").animate([{ outline: "2px solid var(--resistance)" }, { outline: "none" }], 1500);
}

async function initAlerts() {
  const status = await (await fetch("/api/status")).json();
  const a = status.alert;
  $("#alert-status").textContent = status.toss
    ? `관심종목을 ${a.poll_sec}초마다 확인해요. 지지/저항선·추세선·내가 그은 선까지 ${a.near_pct}% 이내로 오거나 넘으면, `
      + "그리고 장 마감 후 차트 신호가 확정되면 알려줘요."
      + (status.telegram ? " 텔레그램 전송 켜짐." : "")
    : "토스 API 키가 있어야 알림이 동작해요.";
  const items = (await (await fetch("/api/alerts?limit=30")).json()).items;
  $("#alerts").replaceChildren(...(items.length ? items.map(alertItem) : [el("li", { class: "muted" }, "아직 알림이 없어요")]));

  const es = new EventSource("/api/alerts/stream");
  es.onmessage = (m) => onAlert(JSON.parse(m.data));

  const btn = $("#notify-btn");
  if (!("Notification" in window) || Notification.permission === "granted") btn.hidden = true;
  btn.addEventListener("click", async () => { if ((await Notification.requestPermission()) === "granted") btn.hidden = true; });
  $("#check-btn").addEventListener("click", async () => {
    const res = await fetch("/api/alerts/check", { method: "POST" });
    const body = await res.json();
    if (!res.ok) return banner(body.detail, "error");
    if (!body.new.length) toast({ type: "info", title: "새 알림 없음", body: "관심종목 모두 선 근처가 아니거나, 오늘 이미 알린 선이에요.", key: "none" });
  });
}

// ── 실시간 시세 (서버가 토스 WebSocket 을 중계한 SSE) ──
let liveSource = null, liveKey = "", listRenderQueued = false;

function updateLiveSubscription() {
  if (!state.toss) return;
  const cur = state.data?.macro ? null : state.data?.symbol;      // 지표(야후)는 토스 실시간이 없음
  const symbols = [...new Set([cur, ...state.watchlist, ...state.holdings.map((h) => h.symbol), ...marketLiveSymbols()].filter(Boolean))].slice(0, 50);
  const key = symbols.slice().sort().join(",");
  if (key === liveKey) return;
  liveKey = key;
  liveSource?.close();
  if (!symbols.length) return;
  liveSource = new EventSource(`/api/live?symbols=${encodeURIComponent(symbols.join(","))}`);
  liveSource.onmessage = (m) => Object.entries(JSON.parse(m.data)).forEach(([s, tick]) => onTick(s, tick));
  liveSource.onopen = () => $("#live-dot").classList.add("on");
  liveSource.onerror = () => $("#live-dot").classList.remove("on");
}

const marketTz = (market) => (market === "US" ? "America/New_York" : "Asia/Seoul");
const dayIn = (tz, epochSec) => new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date(epochSec * 1000));
const mondayOf = (day) => {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
};
const samePeriod = (a, b, iv) => (iv === "1w" ? mondayOf(a) === mondayOf(b) : iv === "1M" ? a.slice(0, 7) === b.slice(0, 7) : iv === "1y" ? a.slice(0, 4) === b.slice(0, 4) : a === b);
const MINUTES = { "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "60m": 60 };

// 체결 하나로 차트 마지막 봉을 갱신하거나 새 봉을 연다
function updateLastBar(price, tsIso) {
  const d = state.data;
  const bars = d.candles, last = bars.at(-1);
  const epoch = Math.floor(Date.parse(tsIso) / 1000);
  let time, same;
  if (d.intraday) {
    const step = MINUTES[d.interval] * 60, t = epoch + 9 * 3600;   // 서버와 같은 한국 시간 축
    time = t - (t % step);
    if (time < last.time) return;
    same = time === last.time;
  } else {
    time = dayIn(marketTz(d.market), epoch);
    if (time < last.time) return;
    same = samePeriod(last.time, time, d.interval);
  }
  if (same) {
    last.high = Math.max(last.high, price);
    last.low = Math.min(last.low, price);
    last.close = price;
    candles.update({ ...last });
  } else {
    const bar = { time, open: price, high: price, low: price, close: price, volume: 0 };
    bars.push(bar);
    candles.update({ ...bar });
    volume.update({ time, value: 0 });
  }
  refreshIndicatorsLive();
}

// 가격이 움직이면 '내 규칙' 카드도 따라간다. 체결마다 다시 부르지 않고 5초에 한 번만 (탭이 열려 있을 때)
let rulesRefresh = null;
function queueRulesRefresh() {
  if (rulesRefresh || state.stockTab !== "ask") return;
  rulesRefresh = setTimeout(() => { rulesRefresh = null; loadRulesCheck(); }, 5000);
}

function onTick(symbol, tick) {
  if (symbol === state.data?.symbol) {
    renderPrice(tick.price);
    updateLastBar(tick.price, tick.ts);
    queueRulesRefresh();
  }
  const w = state.watchSummary[symbol];
  if (w && !w.error) {
    w.prev ??= w.price / (1 + w.change_pct / 100);
    w.price = tick.price;
    w.change_pct = (tick.price / w.prev - 1) * 100;
  }
  // 시장 탭의 실시간 ETF
  for (const g of state.market) {
    if (!g.live) continue;
    const it = g.items.find((i) => i.symbol === symbol && !i.error);
    if (!it) continue;
    it.last = tick.price;
    it.change = tick.price - it.prev;
    it.change_pct = (tick.price / it.prev - 1) * 100;
    if (!marketRenderQueued && state.tab === "market") {
      marketRenderQueued = true;
      requestAnimationFrame(() => { marketRenderQueued = false; renderMarket(); });
    }
  }
  const h = heldOf(symbol);
  if (h) {
    const fx = h.currency === "USD" ? state.usdKrw || 0 : 1;
    h.last_price = tick.price;
    h.market_value = h.quantity * tick.price;
    h.market_value_krw = h.market_value * fx;
    h.pl_amount_krw = (tick.price - h.avg_price) * h.quantity * fx;
    h.pl_rate = (tick.price / h.avg_price - 1) * 100;
  }
  if ((w || h) && !listRenderQueued) {          // 목록은 한 프레임에 한 번만 다시 그림
    listRenderQueued = true;
    requestAnimationFrame(() => { listRenderQueued = false; renderWatchlist(); renderHoldings(); });
  }
}

// ── 시작 ──
$("#search").addEventListener("submit", (e) => { e.preventDefault(); submitSearch(); });
buildIndicatorMenu();
renderIndicatorChips();
volume.applyOptions({ visible: on("volume") });
(async () => {
  state.watchlist = (await (await fetch("/api/watchlist")).json()).symbols;
  renderWatchlist();
  initAlerts();
  loadSignalSettings();
  state.toss = (await (await fetch("/api/status")).json()).toss;
  renderIntervalBar();
  await loadHoldings();
  setTab(store.load("stocking.tab", state.holdings.length ? "holdings" : "watch"));
  setCollapsed(isPhone() ? true : store.load("stocking.sideCollapsed", false));   // 폰은 차트부터
  loadWatchSummary();
  setInterval(loadWatchSummary, 60000);    // 실시간이 못 주는 등락률 기준값·지지/저항 거리 보정
  load(new URLSearchParams(location.search).get("s") || state.holdings[0]?.symbol || state.watchlist[0] || "AAPL");
})();
