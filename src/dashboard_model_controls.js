// ============================================================
// 채점 모델 선택 + Spark 온도 (python main.py serve 로 열었을 때만 API 동작)
// ============================================================
(function () {
  const providerSel = document.getElementById("providerSelect");
  const modelSel = document.getElementById("modelSelect");
  const sparkTemp = document.getElementById("sparkTemp");
  const modelStatus = document.getElementById("modelStatus");
  const applyBtn = document.getElementById("applyModelBtn");
  const reanalyzeBtn = document.getElementById("reanalyzeBtn");
  const modelBar = document.getElementById("modelBar");
  if (!providerSel || !modelBar) return;

  let pollTimer = null;
  let tempTimer = null;
  let lastStatus = null;
  const fileInput = document.getElementById("csvFileInput");
  const uploadBtn = document.getElementById("uploadCsvBtn");
  const uploadStatus = document.getElementById("uploadStatus");
  const sparkKeyBar = document.getElementById("sparkKeyBar");
  const sparkKeyInput = document.getElementById("sparkKeyInput");
  const saveSparkKeyBtn = document.getElementById("saveSparkKeyBtn");

  function setStatus(text, kind) {
    if (!modelStatus) return;
    modelStatus.textContent = text || "";
    modelStatus.className = "model-status" + (kind ? " " + kind : "");
  }

  function setUploadStatus(text, kind) {
    if (!uploadStatus) return;
    uploadStatus.textContent = text || "";
    uploadStatus.className = "upload-status" + (kind ? " " + kind : "");
  }

  function fillModels(models, selected) {
    modelSel.innerHTML = "";
    (models || []).forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      modelSel.appendChild(opt);
    });
    if (selected && [...modelSel.options].some((o) => o.value === selected)) {
      modelSel.value = selected;
    }
  }

  function updateSparkKeyBar(provider, keySet) {
    if (!sparkKeyBar) return;
    const needKey = provider === "spark" && !keySet;
    const wasHidden = !sparkKeyBar.classList.contains("visible");
    sparkKeyBar.classList.toggle("visible", needKey);
    if (needKey && wasHidden && sparkKeyInput) sparkKeyInput.focus();
  }

  function updateSparkTemp(spark, provider) {
    if (!sparkTemp) return;
    if (provider !== "spark") {
      sparkTemp.hidden = true;
      return;
    }
    sparkTemp.hidden = false;
    sparkTemp.removeAttribute("title");
    if (spark && spark.ok && spark.temp_c != null) {
      const t = Number(spark.temp_c);
      let kind = "ok";
      let label = "연결됨";
      if (t >= 90) {
        kind = "error";
        label = "심각한 오류발생";
      } else if (t >= 75) {
        kind = "warn";
        label = "이상있음";
      }
      sparkTemp.textContent = `● ${label} ${t}°C`;
      sparkTemp.className = "spark-temp " + kind;
    } else if (spark && spark.ok) {
      sparkTemp.textContent = "● 이상있음";
      sparkTemp.className = "spark-temp warn";
      if (spark.error) sparkTemp.title = spark.error;
    } else {
      sparkTemp.textContent = "● 접속끊김";
      sparkTemp.className = "spark-temp offline";
      if (spark && spark.error) sparkTemp.title = spark.error;
    }
  }

  function applyStatus(data) {
    lastStatus = data;
    const providers = data.providers || [];
    const prev = providerSel.value;
    providerSel.innerHTML = "";
    providers.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.label;
      providerSel.appendChild(opt);
    });
    providerSel.value = data.provider || prev || "spark";
    fillModels(data.models, data.sentiment_model);
    modelSel.disabled = providerSel.value === "fallback";
    updateSparkTemp(data.spark, providerSel.value);
    updateSparkKeyBar(providerSel.value, !!data.spark_key_set);

    const hints = [];
    if (providerSel.value === "spark" && !data.spark_key_set) {
      hints.push("SPARK_API_KEY 없음 — 아래 칸에 입력하세요");
    }
    if (data.provider === "spark" && data.spark_tunnel_ok === false) {
      hints.push("vLLM 터널 끊김 (ssh -L 8000:127.0.0.1:8000 …)");
    }
    if (data.provider === "anthropic" && !data.anthropic_key_set) {
      hints.push("ANTHROPIC_API_KEY 없음");
    }
    if (hints.length) setStatus(hints.join(" · "), "warn");
    else setStatus(`현재: ${data.provider} / ${data.sentiment_model || "-"}`, "ok");
  }

  async function fetchStatus() {
    const res = await fetch("/api/status", { cache: "no-store" });
    if (!res.ok) throw new Error("status " + res.status);
    return res.json();
  }

  async function refresh() {
    try {
      const data = await fetchStatus();
      applyStatus(data);
      modelBar.classList.remove("offline");
    } catch (e) {
      modelBar.classList.add("offline");
      setStatus("모델 설정은 `python main.py serve` 로 열어야 동작합니다", "warn");
      sparkTemp.hidden = true;
    }
  }

  providerSel.addEventListener("change", async () => {
    modelSel.disabled = providerSel.value === "fallback";
    updateSparkTemp(lastStatus && lastStatus.spark, providerSel.value);
    updateSparkKeyBar(providerSel.value, !!(lastStatus && lastStatus.spark_key_set));
    // provider 바꾸면 모델 목록을 서버에 물어보기 위해 임시 적용 없이 UI만 조정
    if (providerSel.value === "fallback") {
      fillModels(["규칙 기반"], "규칙 기반");
    } else if (providerSel.value === "spark") {
      setStatus("Spark 모델 목록 불러오는 중…");
      try {
        // 설정 저장 전에 목록을 보려면 status의 spark 목록이 현재 provider 기준이라
        // 일단 apply 후 다시 읽어온다
        await postConfig(providerSel.value, modelSel.value === "규칙 기반" ? "qwen" : modelSel.value);
      } catch (e) {
        setStatus(String(e.message || e), "warn");
      }
    } else if (providerSel.value === "anthropic") {
      try {
        await postConfig("anthropic", modelSel.value === "규칙 기반" ? null : modelSel.value);
      } catch (e) {
        setStatus(String(e.message || e), "warn");
      }
    }
  });

  async function postConfig(provider, model) {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "설정 저장 실패");
    applyStatus(data);
    return data;
  }

  saveSparkKeyBtn && saveSparkKeyBtn.addEventListener("click", async () => {
    const key = (sparkKeyInput && sparkKeyInput.value || "").trim();
    if (!key) {
      setStatus("Spark API 키를 입력하세요.", "warn");
      return;
    }
    try {
      saveSparkKeyBtn.disabled = true;
      setStatus("키 저장 중…", "busy");
      const res = await fetch("/api/spark-key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "키 저장 실패");
      if (sparkKeyInput) sparkKeyInput.value = "";
      applyStatus(data);
      if (providerSel.value === "spark") {
        await postConfig("spark", modelSel.value === "규칙 기반" ? "qwen" : modelSel.value);
      }
      setStatus("Spark API 키를 .env에 저장했습니다.", "ok");
    } catch (e) {
      setStatus(String(e.message || e), "warn");
    } finally {
      saveSparkKeyBtn.disabled = false;
    }
  });

  sparkKeyInput && sparkKeyInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveSparkKeyBtn && saveSparkKeyBtn.click();
  });

  applyBtn && applyBtn.addEventListener("click", async () => {
    try {
      applyBtn.disabled = true;
      setStatus("설정 저장 중…");
      await postConfig(providerSel.value, modelSel.value);
      setStatus("설정 저장됨", "ok");
    } catch (e) {
      setStatus(String(e.message || e), "warn");
    } finally {
      applyBtn.disabled = false;
    }
  });

  async function pollJob(label) {
    const res = await fetch("/api/job", { cache: "no-store" });
    const job = await res.json();
    const prefix = label || (job.kind === "upload" ? "업로드" : "재분석");
    if (job.running) {
      const extra = job.kind === "upload" && job.imported
        ? ` · 가져오기 ${job.imported}`
        : "";
      setStatus(`${prefix} 중… ${job.message || ""} (성공 ${job.success}/실패 ${job.failed})${extra}`, "busy");
      setUploadStatus(job.message || `${prefix} 진행 중…`, "busy");
      return false;
    }
    if (job.done) {
      if (job.error) {
        setStatus(`${prefix} 실패: ` + job.error, "warn");
        setUploadStatus(`${prefix} 실패: ` + job.error, "warn");
        return true;
      }
      setStatus(`${prefix} 완료 (성공 ${job.success}/실패 ${job.failed}) — 새로고침합니다`, "ok");
      setUploadStatus(job.message || "완료 — 새로고침합니다", "ok");
      setTimeout(() => location.reload(), 800);
      return true;
    }
    return true;
  }

  function startJobPoll(label) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const finished = await pollJob(label);
        if (finished) {
          clearInterval(pollTimer);
          pollTimer = null;
          reanalyzeBtn && (reanalyzeBtn.disabled = false);
          applyBtn && (applyBtn.disabled = false);
          uploadBtn && (uploadBtn.disabled = false);
        }
      } catch (e) {
        clearInterval(pollTimer);
        pollTimer = null;
        setStatus(String(e.message || e), "warn");
        setUploadStatus(String(e.message || e), "warn");
        reanalyzeBtn && (reanalyzeBtn.disabled = false);
        applyBtn && (applyBtn.disabled = false);
        uploadBtn && (uploadBtn.disabled = false);
      }
    }, 1500);
  }

  reanalyzeBtn && reanalyzeBtn.addEventListener("click", async () => {
    if (!confirm("선택한 모델로 전체 리뷰를 다시 채점할까요? (시간이 걸릴 수 있습니다)")) return;
    try {
      reanalyzeBtn.disabled = true;
      applyBtn && (applyBtn.disabled = true);
      uploadBtn && (uploadBtn.disabled = true);
      const res = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: providerSel.value, model: modelSel.value }),
      });
      const data = await res.json();
      if (!res.ok && res.status !== 202) throw new Error(data.error || "재분석 시작 실패");
      setStatus("재분석 시작…", "busy");
      startJobPoll("재분석");
    } catch (e) {
      setStatus(String(e.message || e), "warn");
      reanalyzeBtn.disabled = false;
      applyBtn && (applyBtn.disabled = false);
      uploadBtn && (uploadBtn.disabled = false);
    }
  });

  uploadBtn && uploadBtn.addEventListener("click", async () => {
    if (!fileInput || !fileInput.files || !fileInput.files[0]) {
      setUploadStatus("CSV/Excel 파일을 먼저 선택하세요.", "warn");
      return;
    }
    const file = fileInput.files[0];
    if (!confirm(`「${file.name}」을(를) 가져와 정제·분석할까요?\n(현재 채점 엔진: ${providerSel.value})`)) return;
    try {
      uploadBtn.disabled = true;
      reanalyzeBtn && (reanalyzeBtn.disabled = true);
      applyBtn && (applyBtn.disabled = true);
      setUploadStatus("업로드 중…", "busy");
      setStatus("CSV 업로드 중…", "busy");
      const fd = new FormData();
      fd.append("file", file, file.name);
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok && res.status !== 202) throw new Error(data.error || "업로드 실패");
      setUploadStatus(`접수됨: ${data.filename || file.name} — 처리 시작`, "busy");
      startJobPoll("업로드");
    } catch (e) {
      setUploadStatus(String(e.message || e), "warn");
      setStatus(String(e.message || e), "warn");
      uploadBtn.disabled = false;
      reanalyzeBtn && (reanalyzeBtn.disabled = false);
      applyBtn && (applyBtn.disabled = false);
    }
  });

  refresh();
  tempTimer = setInterval(() => {
    if (providerSel.value === "spark") refresh();
  }, 10000);
})();
