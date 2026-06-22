const API = "http://127.0.0.1:7821";

// ── Config ────────────────────────────────────────────────────────────────────
function saveConfig() {
  ["brollFolder","runwayKey","anthropicKey","generatedDir"].forEach(id => {
    const v = document.getElementById(id).value;
    if (v) localStorage.setItem(id, v);
  });
}
function loadConfig() {
  ["brollFolder","runwayKey","anthropicKey","generatedDir"].forEach(id => {
    const v = localStorage.getItem(id);
    if (v) document.getElementById(id).value = v;
  });
  if (!document.getElementById("generatedDir").value)
    document.getElementById("generatedDir").value =
      "/Users/rene/Downloads/GERADOR DE VSL/generated_clips";
  if (!document.getElementById("brollFolder").value)
    document.getElementById("brollFolder").value =
      "/Volumes/portatil/ASSETS/brolls vsl";
}
function toggleConfig() {
  const s = document.getElementById("configSection");
  s.style.display = s.style.display === "none" ? "block" : "none";
}

// ── Status / progress ─────────────────────────────────────────────────────────
function setStatus(msg, type = "") {
  const b = document.getElementById("statusBar");
  b.textContent = msg;
  b.className = "status-bar" + (type ? " " + type : "");
}
function setProgress(pct, label = "") {
  const wrap = document.getElementById("progressWrap");
  const bar  = document.getElementById("progressBar");
  const lbl  = document.getElementById("progressLabel");
  if (pct === null) { wrap.style.display = "none"; bar.style.width = "0%"; lbl.textContent = ""; }
  else { wrap.style.display = "block"; bar.style.width = Math.min(pct,100)+"%"; lbl.textContent = label; }
}

// ── SSE progress ──────────────────────────────────────────────────────────────
let _sse = null;
function startProgressStream() {
  if (_sse) { _sse.close(); _sse = null; }
  _sse = new EventSource(API + "/progress");
  _sse.onmessage = (e) => {
    const d = JSON.parse(e.data);
    const pct = d.total > 0 ? Math.round(d.current / d.total * 100) : null;
    const labels = {
      transcribing: "Whisper transcrevendo áudio...",
      indexing:     `Indexando B-rolls ${d.current}/${d.total}`,
      matching:     "Calculando matching semântico...",
      generating:   `Gerando clips Runway ${d.current}/${d.total}`,
    };
    if (d.step && labels[d.step]) {
      setProgress(pct || 30, labels[d.step] + (d.detail ? ` — ${d.detail}` : ""));
      setStatus(labels[d.step], "info");
    }
    if (d.step === "done" || d.step === "error") {
      if (_sse) { _sse.close(); _sse = null; }
    }
  };
  _sse.onerror = () => { if (_sse) { _sse.close(); _sse = null; } };
}

// ── Detect video ──────────────────────────────────────────────────────────────
async function detectVideo() {
  if (!window.vslPlugin) { setStatus("Plugin UXP não carregado.", "error"); return; }
  const path = await window.vslPlugin.getActiveVideoPath();
  if (path) {
    document.getElementById("videoPath").value = path;
    setStatus("Vídeo detectado da faixa V1.", "success");
  } else {
    setStatus("Nenhum clipe na V1. Cole o caminho manualmente.", "error");
  }
}

// ── Process ───────────────────────────────────────────────────────────────────
async function startProcess() {
  saveConfig();
  const videoPath    = document.getElementById("videoPath").value.trim();
  const brollFolder  = document.getElementById("brollFolder").value.trim();
  const runwayKey    = document.getElementById("runwayKey").value.trim();
  const anthropicKey = document.getElementById("anthropicKey").value.trim();
  const generatedDir = document.getElementById("generatedDir").value.trim();

  if (!videoPath)   { setStatus("Informe o caminho do vídeo principal.", "error"); return; }
  if (!brollFolder) { setStatus("Informe a pasta de B-rolls.", "error"); return; }

  document.getElementById("btnProcess").disabled = true;
  document.getElementById("statsRow").style.display      = "none";
  document.getElementById("reviewSection").style.display = "none";
  document.getElementById("insertSection").style.display = "none";

  // Verifica servidor
  try {
    const ping = await fetch(API + "/status");
    if (!ping.ok) throw new Error();
  } catch {
    setStatus("Servidor Python não está rodando. Execute: ./start_server.sh", "error");
    setProgress(null);
    document.getElementById("btnProcess").disabled = false;
    return;
  }

  startProgressStream();
  setStatus("Iniciando pipeline...", "info");
  setProgress(5, "Enviando para o servidor...");

  try {
    const resp = await fetch(API + "/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_path:        videoPath,
        broll_folder:      brollFolder,
        runway_api_key:    runwayKey    || undefined,
        anthropic_api_key: anthropicKey || undefined,
        generated_dir:     generatedDir || undefined,
        vision_verify:     document.getElementById("visionVerify").checked,
      })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || "Erro no servidor");
    }
    const data = await resp.json();
    setProgress(null);
    showResults(data);
  } catch (e) {
    setStatus("Erro: " + e.message, "error");
    setProgress(null);
    if (_sse) { _sse.close(); _sse = null; }
  }
  document.getElementById("btnProcess").disabled = false;
}

// ── Results ───────────────────────────────────────────────────────────────────
function showResults(data) {
  const s = data.stats;
  document.getElementById("statOk").textContent  = `✓ ${s.ok} ok`;
  document.getElementById("statRev").textContent = `⚠ ${s.review} revisão`;
  document.getElementById("statGen").textContent = `✨ ${s.generated} gerados`;
  document.getElementById("statErr").textContent = `✗ ${s.error} erros`;
  document.getElementById("statsRow").style.display = "flex";

  if (data.needs_review?.length > 0) {
    renderReview(data.needs_review);
    document.getElementById("reviewSection").style.display = "block";
    setStatus(`${data.needs_review.length} item(s) precisam de revisão.`, "info");
  } else {
    setStatus(`Pronto! ${s.ok + s.generated} clips prontos para inserir.`, "success");
    document.getElementById("insertSection").style.display = "block";
  }
}

function renderReview(items) {
  const list = document.getElementById("reviewList");
  list.innerHTML = "";
  items.forEach(item => {
    const d = document.createElement("div");
    d.className = "review-item";
    d.id = "rev-" + item.index;
    d.innerHTML = `
      <div class="ts">${fmt(item.start)} → ${fmt(item.end)}</div>
      <div class="txt">"${item.text.substring(0,90)}${item.text.length>90?"…":""}"</div>
      <div class="sug">📁 ${item.broll_filename||"nenhum"} ${item.confidence?`(${(item.confidence*100).toFixed(0)}%)`:""}
      </div>
      <div class="acts">
        <button class="btn-success btn-small" onclick="approve(${item.index})">✓ Usar</button>
        <button class="btn-gen btn-small"     onclick="reject(${item.index})">✨ Gerar</button>
        <button class="btn-skip btn-small"    onclick="skip(${item.index})">— Pular</button>
      </div>`;
    list.appendChild(d);
  });
}

const _dec = {};
function approve(i) { _dec[i]="approve"; dim(i,"#3cb371"); check(); }
function reject(i)  { _dec[i]="reject";  dim(i,"#5a8dee"); check(); }
function skip(i)    { _dec[i]="skip";    document.getElementById("rev-"+i).style.opacity="0.3"; check(); }
function approveAll() {
  document.querySelectorAll(".review-item").forEach(el => {
    const i = parseInt(el.id.replace("rev-",""));
    if (!_dec[i]) { _dec[i] = "approve"; dim(i, "#3cb371"); }
  });
  sendDecisions();   // envia direto — não depende do contador por item
}
function dim(i, color) { const el=document.getElementById("rev-"+i); if(el){el.style.opacity="0.4";el.style.borderLeftColor=color;} }
function check() {
  if (Object.keys(_dec).length >= document.querySelectorAll(".review-item").length) sendDecisions();
}
async function sendDecisions() {
  const approved = Object.entries(_dec).filter(([,v])=>v==="approve").map(([k])=>+k);
  const rejected = Object.entries(_dec).filter(([,v])=>v==="reject").map(([k])=>+k);
  try {
    await fetch(API+"/approve",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({approved_indices:approved,rejected_indices:rejected})});
    setStatus("Decisões registradas. Clique em Inserir.", "success");
    document.getElementById("insertSection").style.display = "block";
  } catch(e) { setStatus("Erro: "+e.message,"error"); }
}

// ── Insert na timeline ────────────────────────────────────────────────────────
async function insertTimeline() {
  if (!window.vslPlugin) { setStatus("Plugin UXP não disponível.", "error"); return; }
  document.getElementById("btnInsert").disabled = true;
  setStatus("Buscando clips aprovados...", "info");

  try {
    const resp = await fetch(API+"/matches");
    const data = await resp.json();
    const insertable = data.insertable;

    if (!insertable?.length) {
      setStatus("Nenhum clip aprovado para inserir.", "error");
      document.getElementById("btnInsert").disabled = false;
      return;
    }

    setStatus(`Inserindo ${insertable.length} clips na V2...`, "info");
    const result = await window.vslPlugin.insertBRolls(insertable);

    if (result.ok) {
      let msg = `✅ ${result.inserted} clips inseridos na faixa V2.`;
      if (result.errors?.length) msg += ` (${result.errors.length} erro(s): ${result.errors[0]})`;
      setStatus(msg, "success");
    } else {
      setStatus("Erro: " + result.error, "error");
    }
  } catch(e) {
    setStatus("Erro: "+e.message, "error");
  }
  document.getElementById("btnInsert").disabled = false;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmt(s) {
  return `${Math.floor(s/60)}:${(s%60).toFixed(1).padStart(4,"0")}`;
}

loadConfig();
