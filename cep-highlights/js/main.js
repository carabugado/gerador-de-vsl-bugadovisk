const API = "http://127.0.0.1:7821";

let cs = null;
try { cs = (typeof CSInterface !== "undefined") ? new CSInterface() : null; }
catch (e) { cs = null; }   // fora do Premiere o construtor pode estourar

let mapped = null;          // resposta do /highlights (com clips já normalizados)
let targetDuration = 180;
let engine = "auto";        // auto | gemini_audio | transcript

// ── HTTP (XHR, CEP-safe) ──────────────────────────────────────────────────────
function xhrPost(url, body) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", url, true);
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.timeout = 2400000;   // 40 min — Whisper em vídeo longo é lento
        xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
                try { resolve(JSON.parse(xhr.responseText)); }
                catch { reject(new Error("JSON parse error")); }
            } else {
                try { reject(new Error(JSON.parse(xhr.responseText).detail || xhr.statusText)); }
                catch { reject(new Error(xhr.statusText || ("HTTP " + xhr.status))); }
            }
        };
        xhr.onerror   = () => reject(new Error("backend offline — rode ./start_server.sh"));
        xhr.ontimeout = () => reject(new Error("timeout"));
        xhr.send(JSON.stringify(body));
    });
}

function xhrGet(url) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("GET", url, true);
        xhr.timeout = 8000;
        xhr.onload = () => { try { resolve(JSON.parse(xhr.responseText)); } catch { reject(new Error("parse")); } };
        xhr.onerror   = () => reject(new Error("backend offline"));
        xhr.ontimeout = () => reject(new Error("timeout"));
        xhr.send();
    });
}

// ── Config (chave Gemini) ──────────────────────────────────────────────────────
async function loadConfig() {
    try {
        const c = await xhrGet(API + "/config");
        if (c && c.gemini_api_key) document.getElementById("geminiKey").value = c.gemini_api_key;
    } catch (e) { /* backend pode estar off; o botão Mapear avisa */ }
}
async function saveGeminiKey() {
    const k = document.getElementById("geminiKey").value.trim();
    try {
        await xhrPost(API + "/config", { gemini_api_key: k });
        setStatus(k ? "Chave Gemini salva." : "Chave Gemini limpa.", "success");
    } catch (e) {
        setStatus("Erro ao salvar (backend ligado?): " + e.message, "error");
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function setStatus(msg, kind) {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = kind || "info";
}
function fmt(sec) {
    sec = Math.round(sec || 0);
    return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0");
}
function _setBuildEnabled(on) {
    document.getElementById("btnBuild").disabled = !on;
    document.getElementById("btnAppend").disabled = !on;
}

// ── Origem (detecta da timeline) ──────────────────────────────────────────────
function detectSource() {
    if (!cs) { setStatus("CSInterface indisponível (rode dentro do Premiere).", "error"); return; }
    cs.evalScript("getActiveVideoPath()", (path) => {
        if (path && path !== "EvalScript error.") {
            document.getElementById("sourcePath").value = path;
            setStatus("Origem detectada da timeline.", "success");
        } else {
            setStatus("Nenhum vídeo na V1 da sequência ativa — cole o caminho manualmente.", "error");
        }
    });
}
function withSource(cb) {
    const sp = document.getElementById("sourcePath");
    const v = sp.value.trim();
    if (v) { cb(v); return; }
    if (!cs) { cb(""); return; }
    cs.evalScript("getActiveVideoPath()", (path) => {
        if (path && path !== "EvalScript error.") { sp.value = path; cb(path); }
        else cb("");
    });
}

// ── Config: duração + tipos ────────────────────────────────────────────────────
document.getElementById("durRow").addEventListener("click", (e) => {
    const b = e.target.closest("[data-dur]"); if (!b) return;
    document.querySelectorAll("#durRow .pill").forEach(p => p.classList.remove("on"));
    b.classList.add("on");
    targetDuration = parseInt(b.dataset.dur, 10);
});
document.getElementById("chipRow").addEventListener("click", (e) => {
    const b = e.target.closest("[data-val]"); if (!b) return;
    b.classList.toggle("on");
});
document.getElementById("engineRow").addEventListener("click", (e) => {
    const b = e.target.closest("[data-eng]"); if (!b) return;
    document.querySelectorAll("#engineRow .pill").forEach(p => p.classList.remove("on"));
    b.classList.add("on");
    engine = b.dataset.eng;
});
function selectedContexts() {
    return Array.from(document.querySelectorAll("#chipRow .chip.on")).map(c => c.dataset.val);
}

// ── Mapear os melhores momentos ────────────────────────────────────────────────
function mapHighlights() {
    withSource(async (source) => {
        if (!source) {
            setStatus("Não achei o vídeo na timeline. Cole o caminho da origem no passo 1.", "error");
            return;
        }
        document.getElementById("btnMap").disabled = true;
        _setBuildEnabled(false);
        setStatus("Transcrevendo e mapeando… (a 1ª vez é mais lenta)", "info");

        try {
            const r = await xhrPost(API + "/highlights", {
                video_path: source,
                target_seconds: targetDuration,
                contexts: selectedContexts(),
                ep_context: document.getElementById("epContext").value.trim(),
                engine
            });
            mapped = r;
            mapped.clips.forEach(c => { c._on = true; });
            renderResults(r);
            const engLabel = r.engine === "gemini_audio" ? "🎧 áudio→Gemini"
                           : r.engine === "transcript" ? "📝 transcrição local" : r.engine || "";
            const tot = r.clips.reduce((s, c) => s + (c.out - c.in), 0);
            const trimmed = r.dropped_for_budget ? ` · cortei ${r.dropped_for_budget} p/ caber no alvo` : "";
            setStatus(`✅ ${r.clips.length} momentos · ${fmt(tot)} (${engLabel})${trimmed}. Revise e corte.`, "success");
            _setBuildEnabled(true);
        } catch (err) {
            setStatus("Erro ao mapear: " + err.message, "error");
        } finally {
            document.getElementById("btnMap").disabled = false;
        }
    });
}

const TIPO_LABEL = { humor: "😂 humor", debate: "🗣️ debate", nerdola: "🤓 nerdola",
                     reacao: "😱 reação", momento: "⭐ momento", insight: "💡 insight" };

function renderResults(r) {
    document.getElementById("resultsCard").style.display = "block";
    const total = r.clips.reduce((s, c) => s + (c.out - c.in), 0);
    const parts = (r.participantes || []).join(", ");
    document.getElementById("summary").innerHTML =
        `<span class="badge">${r.clips.length} clips</span> · total ${fmt(total)}` +
        (r.target_seconds ? ` · alvo ${fmt(r.target_seconds)}` : "") +
        (parts ? `<br><span style="color:#8a8a96">${parts}</span>` : "");

    const list = document.getElementById("clipList");
    list.innerHTML = "";
    r.clips.forEach((c, idx) => {
        const row = document.createElement("div");
        row.className = "clip" + (c._on ? "" : " off");
        row.innerHTML =
            `<input type="checkbox" class="tog" ${c._on ? "checked" : ""}>` +
            `<div class="body">` +
              `<div class="t">${idx + 1}. ${c.titulo || "(sem título)"}</div>` +
              `<div class="meta"><span class="tipo">${TIPO_LABEL[c.tipo] || c.tipo}</span> · ` +
                `${fmt(c.in)}→${fmt(c.out)} (${c.dur}s) · <span class="score">★ ${c.score_viral}</span></div>` +
            `</div>`;
        row.querySelector(".tog").addEventListener("change", (ev) => {
            c._on = ev.target.checked;
            row.classList.toggle("off", !c._on);
        });
        list.appendChild(row);
    });
}

// ── Cortar no Premiere ──────────────────────────────────────────────────────────
function buildCut(createSeq) {
    if (!cs) { setStatus("CSInterface indisponível (rode dentro do Premiere).", "error"); return; }
    if (!mapped || !mapped.clips.length) { setStatus("Mapeie os momentos primeiro.", "error"); return; }

    const clips = mapped.clips.filter(c => c._on !== false);
    if (!clips.length) { setStatus("Ative ao menos um clip.", "error"); return; }

    withSource((source) => {
        if (!source) { setStatus("Não achei o vídeo na timeline. Cole o caminho no passo 1.", "error"); return; }

        const baseName = (mapped.source_filename || "").replace(/\.[^.]+$/, "");
        const payload = {
            source,
            create_sequence: !!createSeq,
            sequence_name: "HIGHLIGHTS" + (baseName ? " - " + baseName : ""),
            clips: clips.map(c => ({ in: c.in, out: c.out, titulo: c.titulo || "", tipo: c.tipo || "" }))
        };

        _setBuildEnabled(false);
        setStatus(createSeq ? "Criando sequência e cortando…" : "Anexando à sequência ativa…", "info");

        const arg = JSON.stringify(JSON.stringify(payload));
        cs.evalScript(`buildHighlightCut(${arg})`, (result) => {
            _setBuildEnabled(true);
            let res;
            try { res = JSON.parse(result); }
            catch (e) { setStatus("Erro no Premiere (host.jsx não respondeu): " + result, "error"); return; }
            if (!res.ok) { setStatus("Erro: " + res.error, "error"); return; }
            let msg = `✅ ${res.inserted} clips (${fmt(res.total_seconds)})`;
            if (res.created_new) msg += ` na sequência "${res.sequence_name}"`;
            if (res.markers) msg += ` · ${res.markers} marcadores`;
            if (res.errors && res.errors.length) msg += ` · ⚠️ ${res.errors.length} com erro: ${res.errors.join("; ")}`;
            setStatus(msg, res.errors && res.errors.length ? "info" : "success");
        });
    });
}

// Ao abrir o painel: carrega a chave salva e detecta a origem da timeline.
loadConfig();
if (cs) {
    cs.evalScript("getActiveVideoPath()", (path) => {
        const sp = document.getElementById("sourcePath");
        if (path && path !== "EvalScript error." && sp && !sp.value) sp.value = path;
    });
}
