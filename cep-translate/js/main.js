const API = "http://127.0.0.1:7821";

let cs = null;
try { cs = (typeof CSInterface !== "undefined") ? new CSInterface() : null; }
catch (e) { cs = null; }   // fora do Premiere o construtor pode estourar

let targetLang = "pt";     // pt | es | en | fr

// ── HTTP (XHR, CEP-safe) ──────────────────────────────────────────────────────
function xhrPost(url, body) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", url, true);
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.timeout = 1800000;   // 30 min — VSLs longas com IA local são lentas
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

// ── Config (chave Gemini, compartilhada com os outros painéis) ─────────────────
async function loadConfig() {
    try {
        const c = await xhrGet(API + "/config");
        if (c && c.gemini_api_key) document.getElementById("geminiKey").value = c.gemini_api_key;
    } catch (e) { /* backend pode estar off; o botão Traduzir avisa */ }
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

// Testa ao vivo se a(s) chave(s) Gemini configurada(s) estão funcionando agora.
async function testGemini() {
    setStatus("🔌 Testando chave(s) Gemini…", "info");
    let r;
    try {
        r = await xhrPost(API + "/test_gemini", {});   // sem key = testa as configuradas
    } catch (e) {
        setStatus("Erro ao testar (backend ligado?): " + (e.message || e), "error");
        return;
    }
    const results = (r && r.results) || [];
    if (!results.length) {
        setStatus("Nenhuma chave Gemini configurada — salve uma acima (ou siga no Ollama local).", "error");
        return;
    }
    const ok = results.filter(x => x.ok).length;
    if (ok > 0) {
        setStatus(`✅ Gemini OK (${ok}/${results.length} chave(s)). A tradução vai usar o Gemini.`, "success");
    } else {
        const f = results[0] || {};
        const why = f.code === 429 ? "cota esgotada"
                  : f.code === 503 ? "instável/sobrecarregado (503)"
                  : f.code ? ("erro " + f.code) : (f.error || "falhou");
        setStatus(`❌ Gemini indisponível agora (${why}). A tradução usa o Ollama local até voltar.`, "error");
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function setStatus(msg, kind) {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = kind || "info";
}

function _nodeFs() {
    try {
        const req = (typeof cep_node !== "undefined" && cep_node.require)
            ? cep_node.require : (typeof require !== "undefined" ? require : null);
        return req ? req("fs") : null;
    } catch (e) { return null; }
}

function _setSrt(path) {
    const inp = document.getElementById("srtPath");
    inp.value = path || "";
    const info = document.getElementById("srtInfo");
    if (path) {
        const name = path.replace(/^.*[\\/]/, "");
        info.textContent = "📄 " + name;
        info.className = "file set";
    } else {
        info.textContent = "";
        info.className = "file";
    }
}

// ── Origem da legenda ──────────────────────────────────────────────────────────
function pickSrt() {
    try {
        const fs = (typeof cep !== "undefined" && cep.fs) ? cep.fs : null;
        if (!fs || !(fs.showOpenDialogEx || fs.showOpenDialog)) {
            setStatus("Seletor indisponível aqui.", "error");
            return;
        }
        const res = fs.showOpenDialogEx
            ? fs.showOpenDialogEx(false, false, "Legenda de origem (.srt/.vtt)", "")
            : fs.showOpenDialog(false, false, "Legenda de origem (.srt/.vtt)", "", ["srt", "vtt"]);
        const paths = res && res.data ? res.data : [];
        if (!paths.length) return;
        let p = paths[0];
        if (p.indexOf("file://") === 0) p = decodeURIComponent(p.slice(7));
        _setSrt(p);
        setStatus("Legenda selecionada.", "success");
    } catch (e) {
        setStatus("Erro ao escolher: " + (e.message || e), "error");
    }
}

// Procura um .srt/.vtt com o mesmo nome do vídeo da timeline (V1).
function detectSrt() {
    if (!cs) { setStatus("CSInterface indisponível (rode dentro do Premiere).", "error"); return; }
    cs.evalScript("getActiveVideoPath()", (videoPath) => {
        if (!videoPath || videoPath === "EvalScript error.") {
            setStatus("Nenhum vídeo na V1 da sequência ativa — use 'Escolher .srt…'.", "error");
            return;
        }
        const fs = _nodeFs();
        if (!fs) { setStatus("Sem acesso ao disco — use 'Escolher .srt…'.", "error"); return; }
        const base = videoPath.replace(/\.[^.\\/]+$/, "");
        const candidates = [base + ".srt", base + ".vtt"];
        for (const c of candidates) {
            try { if (fs.existsSync(c)) { _setSrt(c); setStatus("Legenda encontrada ao lado do vídeo.", "success"); return; } }
            catch (e) {}
        }
        setStatus("Não achei .srt com o nome do vídeo — use 'Escolher .srt…'.", "error");
    });
}

// ── Idioma alvo ────────────────────────────────────────────────────────────────
document.getElementById("langRow").addEventListener("click", (e) => {
    const b = e.target.closest("[data-lang]"); if (!b) return;
    document.querySelectorAll("#langRow .pill").forEach(p => p.classList.remove("on"));
    b.classList.add("on");
    targetLang = b.dataset.lang;
});

// Posição do playhead (segundos) via host.jsx — Promise pra usar com await.
function playheadSeconds() {
    return new Promise((resolve) => {
        if (!cs) { resolve(0); return; }
        cs.evalScript("getPlayheadSeconds()", (r) => {
            const n = parseFloat(r);
            resolve(isNaN(n) ? 0 : n);
        });
    });
}
function fmtTime(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const m = Math.floor(sec / 60), s = sec % 60;
    return m + ":" + String(s).padStart(2, "0");
}

// ── Traduzir + colocar na timeline ──────────────────────────────────────────────
async function translateAndPlace() {
    const p = (document.getElementById("srtPath").value || "").trim();
    if (!p) { setStatus("Escolha a legenda de origem (.srt) no passo 1.", "error"); return; }

    const btn = document.getElementById("btnGo");
    btn.disabled = true;

    // Encaixe no playhead: desloca os tempos pra legenda cair onde está o cursor.
    const anchor = document.getElementById("anchorPlayhead");
    let offset = 0;
    if (cs && anchor && anchor.checked) {
        offset = await playheadSeconds();
    }
    const at = offset > 0 ? ` (encaixando em ${fmtTime(offset)})` : "";
    setStatus("🌐 Traduzindo (mantendo os tempos)" + at + "… a 1ª vez pode demorar.", "info");

    let data;
    try {
        data = await xhrPost(API + "/translate_srt", { srt_path: p, target_lang: targetLang, offset_seconds: offset });
    } catch (e) {
        setStatus("Erro ao traduzir: " + (e.message || e), "error");
        btn.disabled = false;
        return;
    }
    if (!data || !data.ok) {
        setStatus("Falha na tradução: " + ((data && data.error) || "?"), "error");
        btn.disabled = false;
        return;
    }
    const out = data.out_path || "";
    const blocks = data.blocks || "?";
    const warn = data.warning ? "  ⚠️ " + data.warning : "";
    const atMsg = offset > 0 ? " em " + fmtTime(offset) : "";

    // Sem Premiere (ou sem caminho de saída) — só informa onde salvou.
    if (!cs || !out) {
        setStatus("✅ Legenda traduzida (" + blocks + " blocos): " + out + warn, warn ? "info" : "success");
        btn.disabled = false;
        return;
    }

    // Importa e tenta colocar na faixa de legenda da sequência ativa.
    cs.evalScript(`importCaptionSrt(${JSON.stringify(out)})`, (r) => {
        btn.disabled = false;
        let info = null;
        try { info = JSON.parse(r); } catch (e) {}
        const kind = warn ? "info" : "success";
        if (info && info.ok && info.placed) {
            setStatus("✅ Legenda colocada na timeline" + atMsg + " (" + blocks + " blocos)." + warn, kind);
        } else if (info && info.ok) {
            setStatus("✅ Traduzida e importada no projeto — arraste pra uma faixa de legenda: " + out + warn, kind);
        } else {
            setStatus("✅ Traduzida: " + out + " — importe manualmente no Premiere." + warn, kind);
        }
    });
}

// Ao abrir o painel: carrega a chave salva e tenta achar o .srt ao lado do vídeo.
loadConfig();
if (cs) {
    cs.evalScript("getActiveVideoPath()", (videoPath) => {
        if (!videoPath || videoPath === "EvalScript error.") return;
        const fs = _nodeFs();
        if (!fs) return;
        const base = videoPath.replace(/\.[^.\\/]+$/, "");
        for (const c of [base + ".srt", base + ".vtt"]) {
            try { if (fs.existsSync(c)) { _setSrt(c); return; } } catch (e) {}
        }
    });
}
