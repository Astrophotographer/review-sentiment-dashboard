// ============================================================
// 대화형 HTML 대시보드 — 카테고리/제품 필터 + Chart.js 렌더링
// (Python이 __ALL_REVIEWS_JSON__ / __THRESHOLD__ 자리를 실제 값으로 치환해서 삽입한다)
// ============================================================
const THRESHOLD = __THRESHOLD__;
const ALL_REVIEWS = __ALL_REVIEWS_JSON__;

const GRADE_INFO = [
  { score: 1, label: "아주 나쁨", color: "#C0392B" },
  { score: 2, label: "나쁨", color: "#E5484D" },
  { score: 3, label: "보통", color: "#9BA3B4" },
  { score: 4, label: "좋음", color: "#5FBF8F" },
  { score: 5, label: "아주 좋음", color: "#1FAF6B" },
];
const SENT_COLORS = { positive: "#1FAF6B", neutral: "#9BA3B4", negative: "#E5484D" };
const SENT_LABEL = { positive: "긍정", neutral: "중립", negative: "부정" };
const LANG_LABEL = { ko: "한국어", en: "영어" };

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
  const pos = rows.filter((r) => r.sentiment === "positive").length;
  const neu = rows.filter((r) => r.sentiment === "neutral").length;
  const neg = rows.filter((r) => r.sentiment === "negative").length;
  charts.donut = new Chart(document.getElementById("chartDonut"), {
    type: "doughnut",
    data: {
      labels: ["긍정", "중립", "부정"],
      datasets: [{ data: [pos, neu, neg], backgroundColor: [SENT_COLORS.positive, SENT_COLORS.neutral, SENT_COLORS.negative], borderWidth: 3, borderColor: "#fff" }],
    },
    options: { cutout: "62%", plugins: { legend: { position: "bottom" } }, animation: { duration: 300 } },
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
      datasets: ["negative", "neutral", "positive"].map((k) => ({
        label: SENT_LABEL[k], data: ratings.map((r) => matrix[r][k]), backgroundColor: SENT_COLORS[k],
      })),
    },
    options: { plugins: { legend: { position: "top" } }, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } }, animation: { duration: 300 } },
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
      datasets: [{ data: GRADE_INFO.map((g) => counts[g.score]), backgroundColor: GRADE_INFO.map((g) => g.color) }],
    },
    options: { indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, ticks: { precision: 0 } } }, animation: { duration: 300 } },
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
    data: { labels: langs.map((l) => LANG_LABEL[l] || l), datasets: [{ label: "리뷰 수", data: langs.map((l) => byLang[l].count), backgroundColor: "#1B2340" }] },
    options: { indexAxis: "y", plugins: { legend: { position: "top" } }, scales: { x: { beginAtZero: true, ticks: { precision: 0 } } }, animation: { duration: 300 } },
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
    data: { labels: products, datasets: [{ label: "긍정비율(%)", data: posRatios, backgroundColor: posRatios.map((v) => (v >= 40 ? SENT_COLORS.positive : v >= 25 ? "#F2A93B" : SENT_COLORS.negative)) }] },
    options: { indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, max: 100 } }, animation: { duration: 300 } },
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
      datasets: ["negative", "neutral", "positive"].map((k) => ({ label: SENT_LABEL[k], data: products.map((p) => byProd[p][k]), backgroundColor: SENT_COLORS[k] })),
    },
    options: { indexAxis: "y", plugins: { legend: { position: "top" } }, scales: { x: { stacked: true, beginAtZero: true, ticks: { precision: 0 } }, y: { stacked: true } }, animation: { duration: 300 } },
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
