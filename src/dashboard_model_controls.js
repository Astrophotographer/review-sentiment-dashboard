// ============================================================
// 채점 모델 선택 + provider API 키 + Spark 온도
// (python main.py serve 로 열었을 때만 API 동작)
// ============================================================
(function () {
  const providerSel = document.getElementById("providerSelect");
  const modelSel = document.getElementById("modelSelect");
  const sparkTemp = document.getElementById("sparkTemp");
  const modelStatus = document.getElementById("modelStatus");
  const reanalyzeBtn = document.getElementById("reanalyzeBtn");
  const modelBar = document.getElementById("modelBar");
  if (!providerSel || !modelBar) return;

  let pollTimer = null;
  let tempTimer = null;
  let lastStatus = null;
  const fileInput = document.getElementById("csvFileInput");
  const uploadBtn = document.getElementById("uploadCsvBtn");
  const uploadStatus = document.getElementById("uploadStatus");
  const providerKeyBar = document.getElementById("providerKeyBar") || document.getElementById("sparkKeyBar");
  const providerKeyLabel = document.getElementById("providerKeyLabel");
  const providerKeyInput = document.getElementById("providerKeyInput") || document.getElementById("sparkKeyInput");
  const saveProviderKeyBtn = document.getElementById("saveProviderKeyBtn") || document.getElementById("saveSparkKeyBtn");
  const providerKeyHint = document.getElementById("providerKeyHint");

  const KEY_META = {
    spark: { env: "SPARK_API_KEY", placeholder: "Spark API 키 입력", flag: "spark_key_set", editable: false },
    openai: { env: "OPENAI_API_KEY", placeholder: "새 OpenAI API 키 입력", flag: "openai_key_set", editable: true },
    gemini: { env: "GEMINI_API_KEY", placeholder: "새 Gemini API 키 입력", flag: "gemini_key_set", editable: true },
    anthropic: { env: "ANTHROPIC_API_KEY", placeholder: "새 Anthropic API 키 입력", flag: "anthropic_key_set", editable: true },
  };
  const deleteProviderKeyBtn = document.getElementById("deleteProviderKeyBtn");
  const providerKeyStatus = document.getElementById("providerKeyStatus");

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

  function keySetFor(provider, data) {
    const meta = KEY_META[provider];
    if (!meta || !data) return true;
    return !!data[meta.flag];
  }

  function fillModels(models, selected) {
    const provider = providerSel.value;
    const filtered = (models || []).filter((m) => modelFitsProvider(provider, m));
    if (!filtered.length) {
      const fallback = defaultModelFor(provider);
      if (fallback) filtered.push(fallback);
    }
    modelSel.innerHTML = "";
    filtered.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      modelSel.appendChild(opt);
    });
    if (selected && modelFitsProvider(provider, selected) && [...modelSel.options].some((o) => o.value === selected)) {
      modelSel.value = selected;
    }
  }

  function updateProviderKeyBar(provider, data) {
    if (!providerKeyBar) return;
    const meta = KEY_META[provider];
    const hasKey = !!meta && keySetFor(provider, data);
    // Spark: 키가 없을 때만 표시. 그 외 editable provider: 선택 시 항상 표시(수정/삭제).
    let show = false;
    if (meta) {
      if (provider === "spark") show = !hasKey;
      else if (meta.editable) show = true;
    }
    const wasHidden = !providerKeyBar.classList.contains("visible");
    providerKeyBar.classList.toggle("visible", show);
    if (meta) {
      if (providerKeyLabel) providerKeyLabel.textContent = meta.env;
      if (providerKeyInput) {
        providerKeyInput.placeholder = hasKey && meta.editable
          ? `등록됨 — 새 ${meta.env} 로 교체`
          : meta.placeholder;
      }
      if (providerKeyHint) {
        if (provider === "spark") {
          providerKeyHint.textContent = `.env에 저장되며, 저장 전까지 ${meta.env} 분석은 폴백됩니다.`;
        } else if (hasKey) {
          providerKeyHint.textContent = `등록됨 · 새 키를 넣고 저장하면 교체됩니다.`;
        } else {
          providerKeyHint.textContent = `.env에 저장되며, 저장 전까지 ${meta.env} 분석은 폴백됩니다.`;
        }
      }
      if (providerKeyStatus) {
        providerKeyStatus.textContent = hasKey ? "등록됨" : "미등록";
        providerKeyStatus.className = "key-status" + (hasKey ? " set" : "");
        providerKeyStatus.hidden = provider === "spark";
      }
      if (deleteProviderKeyBtn) {
        const canDelete = meta.editable && hasKey;
        deleteProviderKeyBtn.hidden = !canDelete;
        deleteProviderKeyBtn.disabled = !canDelete;
      }
      if (saveProviderKeyBtn) {
        saveProviderKeyBtn.textContent = hasKey && meta.editable ? "키 수정" : "키 저장";
      }
    }
    if (show && wasHidden && providerKeyInput && !hasKey) providerKeyInput.focus();
  }

  function modelFitsProvider(provider, model) {
    const m = String(model || "").trim().toLowerCase();
    if (!m) return false;
    if (provider === "fallback") return m === "규칙 기반";
    if (provider === "anthropic") return m.startsWith("claude");
    if (provider === "openai") return m.startsWith("gpt-") || m.startsWith("o1") || m.startsWith("o3") || m.startsWith("o4") || m.startsWith("chatgpt");
    if (provider === "gemini") return m.includes("gemini");
    if (provider === "spark") {
      if (m.startsWith("claude") || m.startsWith("gpt-") || m.startsWith("o1") || m.startsWith("o3") || m.startsWith("o4") || m.startsWith("chatgpt") || m.includes("gemini")) {
        return false;
      }
      return true;
    }
    return true;
  }

  function defaultModelFor(provider) {
    if (provider === "spark") return "qwen";
    if (provider === "openai") return "gpt-4o-mini";
    if (provider === "gemini") return "gemini-2.0-flash";
    if (provider === "anthropic") return "claude-sonnet-5";
    return null;
  }

  function readTempC(spark) {
    if (!spark) return null;
    const raw = spark.temp_c != null ? spark.temp_c
      : (spark.temperature != null ? spark.temperature
        : (spark.raw && spark.raw.temp_c != null ? spark.raw.temp_c : null));
    if (raw == null || raw === "") return null;
    const t = Number(raw);
    return Number.isFinite(t) ? t : null;
  }

  function updateSparkTemp(spark, provider) {
    if (!sparkTemp) return;
    if (provider !== "spark") {
      sparkTemp.hidden = true;
      return;
    }
    sparkTemp.hidden = false;
    sparkTemp.removeAttribute("title");
    const t = readTempC(spark);
    if (spark && spark.ok && t != null) {
      let kind = "ok";
      let label = "연결됨";
      if (t >= 90) {
        kind = "error";
        label = "심각한 오류발생";
      } else if (t >= 75) {
        kind = "warn";
        label = "이상있음";
      }
      sparkTemp.textContent = `● ${label} ${Math.round(t * 10) / 10}°C`;
      sparkTemp.className = "spark-temp " + kind;
    } else if (spark && spark.ok) {
      // health는 왔지만 온도 없음 → 연결은 된 것으로 초록 표시 (온도 생략)
      sparkTemp.textContent = "● 연결됨";
      sparkTemp.className = "spark-temp ok";
      if (spark.error) sparkTemp.title = spark.error;
    } else {
      // 아직 health 확인 전·실패 → 연결된 것처럼 보이지 않게
      sparkTemp.textContent = "● 연결 중";
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
    updateProviderKeyBar(providerSel.value, data);

    const hints = [];
    const p = providerSel.value;
    if (KEY_META[p] && !keySetFor(p, data)) {
      hints.push(`${KEY_META[p].env} 없음 — 아래 칸에 입력하세요`);
    }
    if (data.provider === "spark" && data.spark_tunnel_ok === false) {
      hints.push("vLLM 터널 끊김 (ssh -L 8000:127.0.0.1:8000 …)");
    }
    const shown = data.display_model || data.sentiment_model || "-";
    if (hints.length) setStatus(hints.join(" · "), "warn");
    else setStatus(`현재: ${data.provider} / ${shown}`, "ok");
  }

  async function fetchStatus() {
    const res = await fetch("/api/status", { cache: "no-store" });
    if (!res.ok) throw new Error("status " + res.status);
    return res.json();
  }

  async function refresh() {
    try {
      if (providerSel.value === "spark" && sparkTemp) {
        sparkTemp.hidden = false;
        sparkTemp.textContent = "● 연결 중";
        sparkTemp.className = "spark-temp offline";
      }
      const data = await fetchStatus();
      applyStatus(data);
      modelBar.classList.remove("offline");
    } catch (e) {
      modelBar.classList.add("offline");
      setStatus("모델 설정은 `python main.py serve` 로 열어야 동작합니다", "warn");
      if (providerSel.value === "spark" && sparkTemp) {
        sparkTemp.hidden = false;
        sparkTemp.textContent = "● 연결 중";
        sparkTemp.className = "spark-temp offline";
      } else if (sparkTemp) {
        sparkTemp.hidden = true;
      }
    }
  }

  providerSel.addEventListener("change", async () => {
    modelSel.disabled = providerSel.value === "fallback";
    updateSparkTemp(lastStatus && lastStatus.spark, providerSel.value);
    updateProviderKeyBar(providerSel.value, lastStatus);
    if (providerSel.value === "fallback") {
      fillModels(["규칙 기반"], "규칙 기반");
      return;
    }
    const fallback = defaultModelFor(providerSel.value);
    const current = modelSel.value;
    const model = modelFitsProvider(providerSel.value, current) ? current : fallback;
    setStatus("모델 목록 불러오는 중…");
    try {
      await postConfig(providerSel.value, model);
    } catch (e) {
      setStatus(String(e.message || e), "warn");
    }
  });

  modelSel.addEventListener("change", async () => {
    if (providerSel.value === "fallback") return;
    try {
      setStatus("설정 저장 중…", "busy");
      await postConfig(providerSel.value, modelSel.value);
      setStatus("설정 저장됨", "ok");
    } catch (e) {
      setStatus(String(e.message || e), "warn");
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

  saveProviderKeyBtn && saveProviderKeyBtn.addEventListener("click", async () => {
    const provider = providerSel.value;
    const meta = KEY_META[provider];
    const key = (providerKeyInput && providerKeyInput.value || "").trim();
    if (!meta) {
      setStatus("이 엔진은 API 키가 필요 없습니다.", "warn");
      return;
    }
    if (!key) {
      setStatus(`${meta.env} 를 입력하세요.`, "warn");
      return;
    }
    try {
      saveProviderKeyBtn.disabled = true;
      if (deleteProviderKeyBtn) deleteProviderKeyBtn.disabled = true;
      setStatus(keySetFor(provider, lastStatus) ? "키 수정 중…" : "키 저장 중…", "busy");
      const res = await fetch("/api/provider-key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, key }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "키 저장 실패");
      if (providerKeyInput) providerKeyInput.value = "";
      applyStatus(data);
      if (KEY_META[provider]) {
        const fallback = defaultModelFor(provider);
        await postConfig(provider, modelSel.value === "규칙 기반" ? fallback : modelSel.value);
      }
      setStatus(`${meta.env} 를 .env에 저장했습니다.`, "ok");
    } catch (e) {
      setStatus(String(e.message || e), "warn");
    } finally {
      saveProviderKeyBtn.disabled = false;
      updateProviderKeyBar(providerSel.value, lastStatus);
    }
  });

  deleteProviderKeyBtn && deleteProviderKeyBtn.addEventListener("click", async () => {
    const provider = providerSel.value;
    const meta = KEY_META[provider];
    if (!meta || !meta.editable) {
      setStatus("이 엔진의 키는 삭제할 수 없습니다.", "warn");
      return;
    }
    if (!keySetFor(provider, lastStatus)) {
      setStatus("삭제할 등록 키가 없습니다.", "warn");
      return;
    }
    if (!confirm(`${meta.env} 를 .env에서 삭제할까요?`)) return;
    try {
      deleteProviderKeyBtn.disabled = true;
      saveProviderKeyBtn && (saveProviderKeyBtn.disabled = true);
      setStatus("키 삭제 중…", "busy");
      const res = await fetch("/api/provider-key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, delete: true }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "키 삭제 실패");
      if (providerKeyInput) providerKeyInput.value = "";
      applyStatus(data);
      setStatus(`${meta.env} 를 삭제했습니다.`, "ok");
    } catch (e) {
      setStatus(String(e.message || e), "warn");
    } finally {
      saveProviderKeyBtn && (saveProviderKeyBtn.disabled = false);
      updateProviderKeyBar(providerSel.value, lastStatus);
    }
  });

  providerKeyInput && providerKeyInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveProviderKeyBtn && saveProviderKeyBtn.click();
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
          uploadBtn && (uploadBtn.disabled = false);
        }
      } catch (e) {
        clearInterval(pollTimer);
        pollTimer = null;
        setStatus(String(e.message || e), "warn");
        setUploadStatus(String(e.message || e), "warn");
        reanalyzeBtn && (reanalyzeBtn.disabled = false);
        uploadBtn && (uploadBtn.disabled = false);
      }
    }, 1500);
  }

  reanalyzeBtn && reanalyzeBtn.addEventListener("click", async () => {
    if (!confirm("선택한 모델로 전체 리뷰를 다시 채점할까요? (시간이 걸릴 수 있습니다)")) return;
    try {
      reanalyzeBtn.disabled = true;
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
    }
  });

  refresh();
  tempTimer = setInterval(() => {
    if (providerSel.value === "spark") refresh();
  }, 10000);
})();
