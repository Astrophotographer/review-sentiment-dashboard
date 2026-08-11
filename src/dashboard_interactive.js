// ============================================================
// 대화형 HTML 대시보드 — 카테고리/제품 필터 + Chart.js 렌더링
// (Python이 __ALL_REVIEWS_JSON__ / __THRESHOLD__ 자리를 실제 값으로 치환해서 삽입한다)
// ============================================================
const THRESHOLD = __THRESHOLD__;
const ALL_REVIEWS = __ALL_REVIEWS_JSON__;

const GRADE_INFO = [
  { score: 1, label: "아주 나쁨", color: "#C4474A" },
  { score: 2, label: "나쁨", color: "#E56B6F" },
  { score: 3, label: "보통", color: "#A8B0BF" },
  { score: 4, label: "좋음", color: "#5CB88A" },
  { score: 5, label: "아주 좋음", color: "#2A9B6A" },
];
const SENT_COLORS = { positive: "#2A9B6A", neutral: "#A8B0BF", negative: "#E56B6F" };
const SENT_LABEL = { positive: "긍정", neutral: "중립", negative: "부정" };
const ASPECT_META = [
  { id: "product", label: "상품" },
  { id: "delivery", label: "배송" },
  { id: "service", label: "응대" },
];
const LANG_LABEL = { ko: "한국어", en: "영어" };
const CHART_INK = "#12172B";
const CHART_MUTED = "#8A8F98";
const CHART_GRID = "rgba(228,231,237,.9)";
const CHART_FONT = "'Pretendard',-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif";

function hexAlpha(hex, a) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r},${g},${b},${a})`;
}

function baseLegend(position = "bottom") {
  return {
    position,
    labels: {
      boxWidth: 10,
      boxHeight: 10,
      usePointStyle: true,
      pointStyle: "circle",
      padding: 16,
      color: CHART_MUTED,
      font: { family: CHART_FONT, size: 12, weight: "500" },
    },
  };
}

function baseScales(stacked = false, horizontal = false) {
  const axis = {
    grid: { color: CHART_GRID, drawBorder: false, tickLength: 0 },
    border: { display: false },
    ticks: {
      color: CHART_MUTED,
      font: { family: CHART_FONT, size: 11 },
      padding: 8,
    },
  };
  const valueAxis = {
    ...axis,
    beginAtZero: true,
    stacked,
    ticks: { ...axis.ticks, precision: 0 },
  };
  const categoryAxis = {
    ...axis,
    stacked,
    grid: { display: false, drawBorder: false },
  };
  if (horizontal) return { x: valueAxis, y: categoryAxis };
  return { x: categoryAxis, y: valueAxis };
}

function barDataset(extra = {}) {
  return {
    borderWidth: 0,
    borderRadius: 8,
    borderSkipped: false,
    maxBarThickness: 28,
    ...extra,
  };
}

/** 누적 막대: 스택 맨 위만 둥글게 (아래는 축에 붙게 유지 — 알약/원형 왜곡 방지) */
function stackedBarRadius(radius = 10) {
  return (ctx) => {
    const { chart, dataIndex, datasetIndex } = ctx;
    const datasets = chart.data.datasets;
    let last = -1;
    datasets.forEach((ds, i) => {
      const v = Number(ds.data[dataIndex]) || 0;
      if (v > 0) last = i;
    });
    if (last < 0 || datasetIndex !== last) return 0;
    return {
      topLeft: radius,
      topRight: radius,
      bottomLeft: 0,
      bottomRight: 0,
    };
  };
}

// Python의 utils.sentiment_grade() 와 동일한 로직 (감정+신뢰도 -> 1~5점 등급)
function sentimentGrade(sentiment, confidence) {
  if (!sentiment || sentiment === "neutral") return GRADE_INFO[2];
  const c = confidence === null || confidence === undefined ? 0.5 : confidence;
  if (sentiment === "positive") return c >= THRESHOLD ? GRADE_INFO[4] : GRADE_INFO[3];
  if (sentiment === "negative") return c >= THRESHOLD ? GRADE_INFO[0] : GRADE_INFO[1];
  return GRADE_INFO[2];
}

// 카테고리 -> 그 안에 있는 제품 목록
const CATEGORY_PRODUCTS = {};
ALL_REVIEWS.forEach((r) => {
  if (!r.category) return;
  if (!CATEGORY_PRODUCTS[r.category]) CATEGORY_PRODUCTS[r.category] = new Set();
  if (r.product) CATEGORY_PRODUCTS[r.category].add(r.product);
});

let charts = {};

function populateFilters() {
  const catSel = document.getElementById("catFilter");
  Object.keys(CATEGORY_PRODUCTS).sort().forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    catSel.appendChild(opt);
  });
  updateProductOptions();
}

function updateProductOptions() {
  const cat = document.getElementById("catFilter").value;
  const prodSel = document.getElementById("prodFilter");
  const prevValue = prodSel.value;
  prodSel.innerHTML = '<option value="__all__">전체 제품</option>';
  let products;
  if (cat === "__all__") {
    products = [...new Set(ALL_REVIEWS.map((r) => r.product).filter(Boolean))];
  } else {
    products = [...(CATEGORY_PRODUCTS[cat] || [])];
  }
  products.sort().forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p;
    prodSel.appendChild(opt);
  });
  // 이전에 고른 제품이 새 카테고리 안에도 있으면 유지, 없으면 "전체 제품"으로
  if (products.includes(prevValue)) prodSel.value = prevValue;
}

function getFiltered() {
  const cat = document.getElementById("catFilter").value;
  const prod = document.getElementById("prodFilter").value;
  return ALL_REVIEWS.filter((r) => {
    if (cat !== "__all__" && r.category !== cat) return false;
    if (prod !== "__all__" && r.product !== prod) return false;
    return true;
  });
}

function destroyChart(id) {
  if (charts[id]) {
    charts[id].destroy();
    delete charts[id];
  }
}

function renderAll() {
  const rows = getFiltered();
  const prodSelected = document.getElementById("prodFilter").value !== "__all__";

  renderKPIs(rows);
  renderDonut(rows);
  renderTrend(rows);
  renderRatingMatrix(rows);
  renderGrade(rows);
  renderLanguage(rows);
  toggleComparisonCharts(!prodSelected);
  if (!prodSelected) {
    renderProductComparison(rows);
    renderProductBreakdown(rows);
  }
  updateFilterLabel(rows.length);
  updateEmptyNote(rows.length);
}

function toggleComparisonCharts(show) {
  document.getElementById("cardProductComparison").style.display = show ? "" : "none";
  document.getElementById("cardProductBreakdown").style.display = show ? "" : "none";
  document.getElementById("compareHiddenNote").style.display = show ? "none" : "block";
}

function updateFilterLabel(count) {
  const cat = document.getElementById("catFilter").value;
  const prod = document.getElementById("prodFilter").value;
  let label = "전체";
  if (cat !== "__all__") label = cat;
  if (prod !== "__all__") label = prod;
  document.getElementById("filterLabel").textContent = `${label} (${count}건)`;
}

function updateEmptyNote(count) {
  document.getElementById("emptyNote").style.display = count === 0 ? "block" : "none";
}

function resetFilters() {
  document.getElementById("catFilter").value = "__all__";
  updateProductOptions();
  document.getElementById("prodFilter").value = "__all__";
  renderAll();
}

function renderKPIs(rows) {
  const total = rows.length;
  const analyzed = rows.filter((r) => r.sentiment).length;
  const positive = rows.filter((r) => r.sentiment === "positive").length;
  const posRatio = analyzed ? (positive / analyzed) * 100 : 0;
  const completion = total ? (analyzed / total) * 100 : 0;
  const ratings = rows.filter((r) => r.rating).map((r) => r.rating);
  const avgRating = ratings.length ? ratings.reduce((a, b) => a + b, 0) / ratings.length : 0;
  const confidences = rows.filter((r) => r.sentiment && r.confidence != null).map((r) => r.confidence);
  const avgConf = confidences.length ? confidences.reduce((a, b) => a + b, 0) / confidences.length : 0;
  const grades = rows.filter((r) => r.sentiment).map((r) => sentimentGrade(r.sentiment, r.confidence).score);
  const avgGrade = grades.length ? grades.reduce((a, b) => a + b, 0) / grades.length : 0;

  document.getElementById("kpiTotal").textContent = total + "건";
  document.getElementById("kpiRate").textContent = completion.toFixed(1) + "%";
  document.getElementById("kpiPos").textContent = posRatio.toFixed(1) + "%";
  document.getElementById("kpiRating").textContent = avgRating.toFixed(2);
  document.getElementById("kpiGrade").textContent = avgGrade.toFixed(2);
  document.getElementById("kpiConf").textContent = avgConf.toFixed(2);
}

function renderDonut(rows) {
  destroyChart("donut");
  // 만족도 측면(상품/배송/응대)별 긍정·중립·부정 건수 — 언급된 측면만 집계
  const counts = {};
  ASPECT_META.forEach((a) => {
    counts[a.id] = { positive: 0, neutral: 0, negative: 0 };
  });
  rows.forEach((r) => {
    const aspects = r.aspects || {};
    ASPECT_META.forEach((a) => {
      const v = aspects[a.id];
      if (v === "positive" || v === "neutral" || v === "negative") {
        counts[a.id][v] += 1;
      }
    });
  });
  const labels = ASPECT_META.map((a) => a.label);
  charts.donut = new Chart(document.getElementById("chartDonut"), {
    type: "bar",
    data: {
      labels,
      datasets: ["positive", "neutral", "negative"].map((k) => barDataset({
        label: SENT_LABEL[k],
        data: ASPECT_META.map((a) => counts[a.id][k]),
        backgroundColor: SENT_COLORS[k],
        hoverBackgroundColor: hexAlpha(SENT_COLORS[k], 0.85),
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: baseLegend("bottom"),
        tooltip: {
          backgroundColor: CHART_INK,
          titleFont: { family: CHART_FONT, size: 12 },
          bodyFont: { family: CHART_FONT, size: 12 },
          padding: 10,
          cornerRadius: 8,
          displayColors: true,
        },
      },
      scales: baseScales(false, false),
      animation: { duration: 450, easing: "easeOutQuart" },
    },
  });
}

function renderTrend(rows) {
  destroyChart("trend");
  const byDate = {};
  rows.forEach((r) => {
    if (!r.date || !r.sentiment) return;
    if (!byDate[r.date]) byDate[r.date] = { positive: 0, neutral: 0, negative: 0 };
    byDate[r.date][r.sentiment]++;
  });
  const dates = Object.keys(byDate).sort();
  charts.trend = new Chart(document.getElementById("chartTrend"), {
    type: "line",
    data: {
      labels: dates,
      datasets: ["positive", "neutral", "negative"].map((k) => ({
        label: SENT_LABEL[k], data: dates.map((d) => byDate[d][k]), borderColor: SENT_COLORS[k],
        backgroundColor: SENT_COLORS[k], tension: 0.3, pointRadius: 3,
      })),
    },
    options: { plugins: { legend: { position: "top" } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }, animation: { duration: 300 } },
  });
}

function renderRatingMatrix(rows) {
  destroyChart("rating");
  const ratings = ["1", "2", "3", "4", "5"];
  const matrix = {};
  ratings.forEach((r) => (matrix[r] = { positive: 0, neutral: 0, negative: 0 }));
  rows.forEach((r) => { if (r.rating && r.sentiment) matrix[String(r.rating)][r.sentiment]++; });
  charts.rating = new Chart(document.getElementById("chartRating"), {
    type: "bar",
    data: {
      labels: ratings.map((r) => r + "점"),
      datasets: ["negative", "neutral", "positive"].map((k) => barDataset({
        label: SENT_LABEL[k],
        data: ratings.map((r) => matrix[r][k]),
        backgroundColor: SENT_COLORS[k],
        hoverBackgroundColor: hexAlpha(SENT_COLORS[k], 0.85),
        borderRadius: stackedBarRadius(10),
        borderSkipped: false,
        maxBarThickness: 42,
        categoryPercentage: 0.55,
        barPercentage: 0.85,
      })),
    },
    options: {
      plugins: { legend: baseLegend("top") },
      scales: baseScales(true, false),
      animation: { duration: 450, easing: "easeOutQuart" },
    },
  });
}

function renderGrade(rows) {
  destroyChart("grade");
  const counts = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  rows.forEach((r) => { if (r.sentiment) counts[sentimentGrade(r.sentiment, r.confidence).score]++; });
  charts.grade = new Chart(document.getElementById("chartGrade"), {
    type: "bar",
    data: {
      labels: GRADE_INFO.map((g) => g.score + "점 " + g.label),
      datasets: [barDataset({
        data: GRADE_INFO.map((g) => counts[g.score]),
        backgroundColor: GRADE_INFO.map((g) => g.color),
        hoverBackgroundColor: GRADE_INFO.map((g) => hexAlpha(g.color, 0.85)),
        maxBarThickness: 22,
      })],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: baseScales(false, true),
      animation: { duration: 450, easing: "easeOutQuart" },
    },
  });
}

function renderLanguage(rows) {
  destroyChart("lang");
  const byLang = {};
  rows.forEach((r) => {
    if (!r.language) return;
    if (!byLang[r.language]) byLang[r.language] = { count: 0, positive: 0, analyzed: 0 };
    byLang[r.language].count++;
    if (r.sentiment) {
      byLang[r.language].analyzed++;
      if (r.sentiment === "positive") byLang[r.language].positive++;
    }
  });
  const langs = Object.keys(byLang).sort((a, b) => byLang[b].count - byLang[a].count);
  charts.lang = new Chart(document.getElementById("chartLanguage"), {
    type: "bar",
    data: {
      labels: langs.map((l) => LANG_LABEL[l] || l),
      datasets: [barDataset({
        label: "리뷰 수",
        data: langs.map((l) => byLang[l].count),
        backgroundColor: "#2C3658",
        hoverBackgroundColor: "#1B2340",
        maxBarThickness: 26,
      })],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: baseScales(false, true),
      animation: { duration: 450, easing: "easeOutQuart" },
    },
  });
}

function renderProductComparison(rows) {
  destroyChart("prodComp");
  const byProd = {};
  rows.forEach((r) => {
    if (!r.product) return;
    if (!byProd[r.product]) byProd[r.product] = { positive: 0, neutral: 0, negative: 0, ratings: [] };
    if (r.sentiment) byProd[r.product][r.sentiment]++;
    if (r.rating) byProd[r.product].ratings.push(r.rating);
  });
  const products = Object.keys(byProd);
  const posRatio = (p) => {
    const d = byProd[p];
    const t = d.positive + d.neutral + d.negative;
    return t ? (d.positive / t) * 100 : 0;
  };
  products.sort((a, b) => posRatio(a) - posRatio(b));
  const posRatios = products.map(posRatio);
  charts.prodComp = new Chart(document.getElementById("chartProductComparison"), {
    type: "bar",
    data: {
      labels: products,
      datasets: [barDataset({
        label: "긍정비율(%)",
        data: posRatios,
        backgroundColor: posRatios.map((v) => (v >= 40 ? SENT_COLORS.positive : v >= 25 ? "#E0A45A" : SENT_COLORS.negative)),
        maxBarThickness: 18,
      })],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: {
        ...baseScales(false, true),
        x: { ...baseScales(false, true).x, max: 100 },
      },
      animation: { duration: 450, easing: "easeOutQuart" },
    },
  });
}

function renderProductBreakdown(rows) {
  destroyChart("prodBreak");
  const byProd = {};
  rows.forEach((r) => {
    if (!r.product || !r.sentiment) return;
    if (!byProd[r.product]) byProd[r.product] = { positive: 0, neutral: 0, negative: 0 };
    byProd[r.product][r.sentiment]++;
  });
  const products = Object.keys(byProd);
  const total = (p) => byProd[p].positive + byProd[p].neutral + byProd[p].negative;
  products.sort((a, b) => byProd[a].positive / (total(a) || 1) - byProd[b].positive / (total(b) || 1));
  charts.prodBreak = new Chart(document.getElementById("chartProductBreakdown"), {
    type: "bar",
    data: {
      labels: products,
      datasets: ["negative", "neutral", "positive"].map((k) => barDataset({
        label: SENT_LABEL[k],
        data: products.map((p) => byProd[p][k]),
        backgroundColor: SENT_COLORS[k],
        maxBarThickness: 18,
      })),
    },
    options: {
      indexAxis: "y",
      plugins: { legend: baseLegend("top") },
      scales: baseScales(true, true),
      animation: { duration: 450, easing: "easeOutQuart" },
    },
  });
}

document.getElementById("catFilter").addEventListener("change", () => {
  updateProductOptions();
  renderAll();
});
document.getElementById("prodFilter").addEventListener("change", renderAll);
document.getElementById("resetFilterBtn").addEventListener("click", resetFilters);

populateFilters();
renderAll();
