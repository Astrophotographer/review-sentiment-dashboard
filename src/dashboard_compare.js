// 모델 채점 스냅샷 비교 페이지
(function () {
  const runA = document.getElementById("runA");
  const runB = document.getElementById("runB");
  const compareBtn = document.getElementById("compareBtn");
  const statusEl = document.getElementById("compareStatus");
  const emptyEl = document.getElementById("emptyState");
  const resultEl = document.getElementById("resultPanel");
  const snapListEl = document.getElementById("snapList");
  const snapListPanel = document.getElementById("snapListPanel");
  const tableBody = document.querySelector("#disagreeTable tbody");
  let chart;
  let runsById = {};

  const SENT = { positive: "긍정", neutral: "중립", negative: "부정", null: "미분석" };
  const ENGINE = { spark: "Spark", openai: "OpenAI", gemini: "Gemini", anthropic: "Anthropic", fallback: "규칙 기반" };

  function setStatus(msg, kind) {
    if (!statusEl) return;
    statusEl.textContent = msg || "";
    statusEl.className = "status" + (kind ? " " + kind : "");
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
    return el;
  }

  function engineName(provider) {
    return ENGINE[(provider || "").toLowerCase()] || provider || "?";
  }

  function formatModelDisplay(name) {
    if (!name) return "-";
    const parts = String(name).replace(/[_-]/g, " ").split(/\s+/).filter(Boolean);
    const cleaned = parts.filter((p) => !/^20\d{2}$/.test(p) && !/^20\d{6}$/.test(p));
    if (!cleaned.length) return String(name);
    const merged = [];
    for (let i = 0; i < cleaned.length; i++) {
      if (/^\d+$/.test(cleaned[i]) && i + 1 < cleaned.length && /^\d+$/.test(cleaned[i + 1])) {
        const ver = [cleaned[i], cleaned[i + 1]];
        let j = i + 2;
        while (j < cleaned.length && /^\d+$/.test(cleaned[j])) {
          ver.push(cleaned[j]);
          j++;
        }
        if (ver.length <= 3) {
          merged.push(ver.join("."));
          i = j - 1;
          continue;
        }
      }
      merged.push(cleaned[i]);
    }
    return merged.join(" ");
  }

  function modelTitle(r) {
    const eng = engineName(r.provider);
    const model = formatModelDisplay(r.model || "-");
    if ((r.provider || "").toLowerCase() === "fallback") return eng;
    return `${eng} · ${model}`;
  }

  function runOptionLabel(r) {
    const when = (r.created_at || "").replace("T", " ").slice(0, 19);
    const temp = r.temp_c != null ? ` · GPU ${r.temp_c}°C` : "";
    return `#${r.id}  ${modelTitle(r)}  ·  ${when}${temp}`;
  }

  async function loadRuns() {
    const res = await fetch("/api/runs", { cache: "no-store" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "런 목록 실패");
    const runs = data.runs || [];
    runsById = {};
    runs.forEach((r) => { runsById[r.id] = r; });
    runA.innerHTML = "";
    runB.innerHTML = "";
    if (!runs.length) {
      emptyEl.style.display = "block";
      resultEl.style.display = "none";
      compareBtn.disabled = true;
      renderSnapList(runs);
      setStatus("저장된 스냅샷이 없습니다. 대시보드에서 재분석을 먼저 실행하세요.", "warn");
      return runs;
    }
    emptyEl.style.display = "none";
    runs.forEach((r, idx) => {
      const o1 = document.createElement("option");
      o1.value = r.id;
      o1.textContent = runOptionLabel(r);
      runA.appendChild(o1);
      const o2 = document.createElement("option");
      o2.value = r.id;
      o2.textContent = runOptionLabel(r);
      runB.appendChild(o2);
      if (idx === 0) runA.value = r.id;
      if (idx === 1 || (runs.length === 1 && idx === 0)) runB.value = r.id;
    });
    if (runs.length === 1) {
      compareBtn.disabled = true;
      setStatus("비교하려면 스냅샷이 2개 이상 필요합니다. 다른 모델로 한 번 더 재분석하세요.", "warn");
    } else {
      compareBtn.disabled = false;
      runA.value = String(runs[0].id);
      runB.value = String(runs[1].id);
      setStatus(`${runs.length}개 스냅샷 · 드롭다운에서 채점 모델 확인`);
    }
    renderSnapList(runs);
    updateSelectedHints();
    return runs;
  }

  function renderSnapList(runs) {
    if (!snapListEl) return;
    if (snapListPanel) snapListPanel.style.display = runs.length ? "" : "none";
    if (!runs.length) {
      snapListEl.innerHTML = "";
      return;
    }
    snapListEl.innerHTML = "";
    runs.forEach((r) => {
      const row = document.createElement("div");
      row.className = "snap-row";
      const when = (r.created_at || "").replace("T", " ").slice(0, 19);
      const temp = r.temp_c != null ? ` · GPU ${r.temp_c}°C` : "";
      row.innerHTML = `
        <span class="snap-id">#${r.id}</span>
        <span class="snap-title">${modelTitle(r)}</span>
        <span class="snap-meta">${when}${temp}</span>
        <button type="button" class="delete-snap" data-id="${r.id}">삭제</button>`;
      snapListEl.appendChild(row);
    });
  }

  async function deleteRun(runId) {
    const run = runsById[runId];
    const label = run ? `#${runId} ${modelTitle(run)}` : `#${runId}`;
    if (!confirm(`스냅샷 ${label} 을(를) 삭제할까요?\n비교 목록에서 사라지고, 되돌릴 수 없습니다.`)) return;
    setStatus("스냅샷 삭제 중…");
    try {
      const res = await fetch(`/api/runs?id=${encodeURIComponent(runId)}`, { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "삭제 실패");
      resultEl.style.display = "none";
      const runs = await loadRuns();
      setStatus(`스냅샷 #${runId} 삭제됨 · ${runs.length}개 남음`, "ok");
      if (runs && runs.length >= 2) runCompare();
    } catch (e) {
      setStatus(String(e.message || e), "warn");
    }
  }

  function updateSelectedHints() {
    const a = runsById[runA.value];
    const b = runsById[runB.value];
    const hintA = document.getElementById("hintA");
    const hintB = document.getElementById("hintB");
    if (hintA) hintA.textContent = a ? `채점 모델: ${modelTitle(a)}` : "";
    if (hintB) hintB.textContent = b ? `채점 모델: ${modelTitle(b)}` : "";
  }

  function fillModelCard(prefix, run) {
    setText(prefix + "Engine", engineName(run.provider));
    setText(prefix + "Model", formatModelDisplay(run.model || "-"));
    // 구버전 HTML(labelA/labelB) 호환
    setText("label" + prefix.toUpperCase(), modelTitle(run));
    setText(prefix + "When", (run.created_at || "-").replace("T", " "));
    const tempEl = document.getElementById(prefix + "Temp");
    if (tempEl) {
      if (run.temp_c != null) {
        tempEl.style.display = "";
        tempEl.textContent = `GPU ${run.temp_c}°C`;
      } else {
        tempEl.style.display = "none";
      }
    }
  }

  function renderSummary(data) {
    setText("kpiCommon", data.common_review_count + "건");
    setText("kpiAgree", data.agreement_rate == null ? "-" : data.agreement_rate + "%");
    setText("kpiCompared", data.compared_count + "건");
    setText("kpiDisagree", data.disagreement_total + "건");

    const ta = data.run_a.temp_c;
    const tb = data.run_b.temp_c;
    const tempEl = document.getElementById("kpiTemp");
    if (tempEl) {
      const kpi = tempEl.closest(".kpi");
      if (ta == null && tb == null) {
        if (kpi) kpi.style.display = "none";
      } else {
        if (kpi) kpi.style.display = "";
        tempEl.textContent = `A ${ta ?? "-"}°C / B ${tb ?? "-"}°C`;
      }
    }

    fillModelCard("a", data.run_a);
    fillModelCard("b", data.run_b);
    setText("confA", data.dist_a.avg_confidence == null ? "-" : data.dist_a.avg_confidence);
    setText("confB", data.dist_b.avg_confidence == null ? "-" : data.dist_b.avg_confidence);
  }

  function renderChart(data) {
    const labels = ["긍정", "중립", "부정"];
    const keys = ["positive", "neutral", "negative"];
    const nameA = modelTitle(data.run_a);
    const nameB = modelTitle(data.run_b);
    if (chart) chart.destroy();
    chart = new Chart(document.getElementById("chartCompare"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: `A · ${nameA}`,
            data: keys.map((k) => data.dist_a.counts[k] || 0),
            backgroundColor: "#1B2340",
            borderRadius: 8,
            borderSkipped: false,
            maxBarThickness: 36,
          },
          {
            label: `B · ${nameB}`,
            data: keys.map((k) => data.dist_b.counts[k] || 0),
            backgroundColor: "#2A9B6A",
            borderRadius: 8,
            borderSkipped: false,
            maxBarThickness: 36,
          },
        ],
      },
      options: {
        plugins: {
          legend: { position: "top", labels: { usePointStyle: true, padding: 14 } },
        },
        scales: {
          x: { grid: { display: false }, border: { display: false } },
          y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "rgba(228,231,237,.9)" }, border: { display: false } },
        },
        animation: { duration: 350 },
      },
    });
  }

  function renderTable(data) {
    tableBody.innerHTML = "";
    const rows = data.disagreements || [];
    const headA = document.getElementById("thA");
    const headB = document.getElementById("thB");
    if (headA) headA.textContent = `A · ${modelTitle(data.run_a)}`;
    if (headB) headB.textContent = `B · ${modelTitle(data.run_b)}`;
    if (!rows.length) {
      tableBody.innerHTML = '<tr><td colspan="6" class="empty-cell">불일치 항목이 없습니다.</td></tr>';
      return;
    }
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${r.review_id}</td>
        <td>${r.product || "-"}</td>
        <td class="excerpt">${(r.review_excerpt || "").replace(/</g, "&lt;")}</td>
        <td><span class="sent ${r.sentiment_a || "null"}">${SENT[r.sentiment_a] || "미분석"}</span> ${r.confidence_a ?? ""}</td>
        <td><span class="sent ${r.sentiment_b || "null"}">${SENT[r.sentiment_b] || "미분석"}</span> ${r.confidence_b ?? ""}</td>
        <td>${(r.conf_delta || 0).toFixed(2)}</td>`;
      tableBody.appendChild(tr);
    });
  }

  async function runCompare() {
    const a = runA.value;
    const b = runB.value;
    if (!a || !b) return;
    if (a === b) {
      setStatus("서로 다른 스냅샷을 선택하세요.", "warn");
      resultEl.style.display = "none";
      return;
    }
    setStatus("비교 중…");
    compareBtn.disabled = true;
    try {
      const res = await fetch(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "비교 실패");
      resultEl.style.display = "block";
      renderSummary(data);
      renderChart(data);
      renderTable(data);
      setStatus(
        `${modelTitle(data.run_a)}  vs  ${modelTitle(data.run_b)}  ·  일치율 ${data.agreement_rate ?? "-"}%`,
        "ok"
      );
    } catch (e) {
      setStatus(String(e.message || e), "warn");
    } finally {
      compareBtn.disabled = false;
    }
  }

  if (!runA || !runB || !compareBtn) {
    console.error("compare page markup missing required elements");
    return;
  }

  compareBtn.addEventListener("click", runCompare);
  if (snapListEl) {
    snapListEl.addEventListener("click", (e) => {
      const btn = e.target.closest("button.delete-snap");
      if (!btn) return;
      const id = Number(btn.getAttribute("data-id"));
      if (Number.isFinite(id)) deleteRun(id);
    });
  }
  runA.addEventListener("change", () => {
    updateSelectedHints();
    if (runA.value === runB.value) setStatus("서로 다른 스냅샷을 선택하세요.", "warn");
  });
  runB.addEventListener("change", () => {
    updateSelectedHints();
    if (runA.value === runB.value) setStatus("서로 다른 스냅샷을 선택하세요.", "warn");
  });

  loadRuns()
    .then((runs) => {
      if (runs && runs.length >= 2) runCompare();
    })
    .catch((e) => setStatus(String(e.message || e), "warn"));
})();
