const API = "http://127.0.0.1:7821";
const cs = typeof CSInterface !== "undefined" ? new CSInterface() : null;

// Composição da timeline detectada (clipes de narração enviados ao backend).
let _composition = [];
let _timelineBrollPaths = [];   // B-rolls já na timeline (V2+) — pra não repetir (#2a)

// ── Config ────────────────────────────────────────────────────────────────────

// id do campo no painel  ->  chave salva no backend (~/.vsl_config.json)
const _CFG_MAP = {
    brollFolder:   "broll_folder",
    anthropicKey:  "anthropic_api_key",
    groqKey:       "groq_api_key",
    pexelsKey:     "pexels_api_key",
    generatedDir:  "generated_dir",
    videoPath:     "video_path",
    llmModel:      "llm_backend",
    brollDensity:  "broll_density",
    brollVertical: "vertical",
    edFolder:      "ed_folder",
};

// ── Chaves Gemini: uma box por chave, 6 primeiros visíveis, "+" adiciona ────────
function _gkPreview(v) {
    return (v && v.length > 6) ? v.slice(0, 6) + "•".repeat(Math.min(v.length - 6, 14)) : (v || "");
}
function addGeminiKeyRow(value = "", focusIt = false) {
    const box = document.getElementById("geminiKeys");
    if (!box) return null;
    const row = document.createElement("div");
    row.className = "gkey-row";
    row.style.cssText = "display:flex;gap:5px;align-items:center;margin-bottom:4px";
    const inp = document.createElement("input");
    inp.type = "text"; inp.className = "gkey"; inp.placeholder = "AIza...";
    inp.style.flex = "1";
    inp.dataset.full = value || "";
    inp.value = _gkPreview(value);
    inp.addEventListener("focus", () => { inp.value = inp.dataset.full || ""; });
    inp.addEventListener("input", () => { inp.dataset.full = inp.value; });
    inp.addEventListener("blur", () => {
        const k = (inp.dataset.full || "").trim();
        // tira espaços invisíveis (causa comum de "chave inválida")
        if (k !== (inp.dataset.full || "")) inp.dataset.full = k;
        inp.value = _gkPreview(k);
        inp.style.borderColor = "";
        // Não bloqueia por prefixo — se a chave falhar no uso, o sistema de alertas avisa.
        saveConfig();
    });
    const del = document.createElement("button");
    del.type = "button"; del.className = "btn-sm"; del.textContent = "✕";
    del.title = "Remover esta chave";
    del.onclick = () => { row.remove(); saveConfig(); };
    row.appendChild(inp); row.appendChild(del); box.appendChild(row);
    if (focusIt) inp.focus();
    return inp;
}
function _collectGeminiKeys() {
    return Array.from(document.querySelectorAll("#geminiKeys .gkey"))
        .map(i => (i.dataset.full || "").trim()).filter(Boolean);
}

function setLlm(v) {
    const inp = document.getElementById("llmModel");
    if (inp) inp.value = v;
    saveConfig();
    updateLlmHint();
}

function updateLlmHint() {
    const el = document.getElementById("llmHint");
    const v = (document.getElementById("llmModel") || {}).value || "auto";
    // destaca o botão ativo (substitui o <select>, que não abre no CEP)
    document.querySelectorAll("#llmButtons .llmbtn").forEach(b => {
        const on = b.dataset.v === v;
        b.style.background = on ? "#2b6cb0" : "";
        b.style.color = on ? "#fff" : "";
        b.style.fontWeight = on ? "700" : "";
    });
    if (!el) return;
    const hints = {
        auto:      "Groq/Gemini nas tarefas + local de reserva (recomendado).",
        ollama:    "Tudo no modelo local — grátis e ilimitado, porém lento.",
        groq:      "Tudo no Groq — rápido e grátis (cota separada do Google).",
        gemini:    "Tudo no Gemini — rápido, mas consome a cota grátis mais rápido.",
        anthropic: "⚠️ Claude é PAGO (cobra por uso). Melhor qualidade. Cole a chave Anthropic acima.",
    };
    el.textContent = hints[v] || "";
    el.style.color = v === "anthropic" ? "#f0a000" : "#52525b";
}

function setDensity(v) {
    const inp = document.getElementById("brollDensity");
    if (inp) inp.value = v;
    saveConfig();
    updateDensityHint();
}

function updateDensityHint() {
    const v = (document.getElementById("brollDensity") || {}).value || "normal";
    document.querySelectorAll("#densityButtons .densbtn").forEach(b => {
        const on = b.dataset.v === v;
        b.style.background = on ? "#2b6cb0" : "";
        b.style.color = on ? "#fff" : "";
        b.style.fontWeight = on ? "700" : "";
    });
    const el = document.getElementById("densityHint");
    if (!el) return;
    const hints = {
        calm:    "Menos cortes — trechos longos recebem poucos B-rolls.",
        normal:  "Equilíbrio entre cobertura e respiro (recomendado).",
        intense: "Mais cortes — fatia trechos longos em vários B-rolls (mais cobertura).",
    };
    el.textContent = hints[v] || "";
}

function setVertical(v) {
    const inp = document.getElementById("brollVertical");
    if (inp) inp.value = v;
    saveConfig();
    updateVerticalHint();
}

function updateVerticalHint() {
    const v = (document.getElementById("brollVertical") || {}).value || "";
    document.querySelectorAll("#verticalButtons .vertbtn").forEach(b => {
        const on = (b.dataset.v || "") === v;
        b.style.background = on ? "#2b6cb0" : "";
        b.style.color = on ? "#fff" : "";
        b.style.fontWeight = on ? "700" : "";
    });
    const el = document.getElementById("verticalHint");
    if (el) {
        const names = {
            "":   "Detecta sozinho pelo doc da VSL ou pelo nome da pasta de clipes.",
            WL: "Weight Loss (emagrecimento)", ED: "Erectile/Libido",
            NR: "Neuro/memória/foco", PT: "Próstata/bexiga",
            VS: "Visão/olhos", JT: "Articulações/joelho/artrite", FG: "Fungo/unha",
        };
        el.textContent = names[v] || "";
    }
    // Box ED+ visível só quando vertical = ED
    const edBox = document.getElementById("edBox");
    if (edBox) edBox.style.display = v === "ED" ? "block" : "none";
}

async function tagEdAssets() {
    const folder = (document.getElementById("edFolder") || {}).value || "";
    if (!folder) { setStatus("Informe a pasta ED+ antes de taggear.", "error"); return; }
    saveConfig();   // persiste edFolder no config ANTES de processar (essencial p/ /process usar)
    const btn = document.getElementById("btnTagEd");
    const info = document.getElementById("edInfo");
    if (btn) { btn.disabled = true; btn.textContent = "🏷️ Tagueando…"; }
    if (info) info.textContent = "⏳ Iniciando…";
    try {
        await xhrPost(API + "/tag_assets", { folder, enrich: true });
    } catch (e) {
        if (info) info.textContent = "❌ Erro ao iniciar: " + e.message;
        setStatus("Erro ao taggear ED+: " + e.message, "error");
        if (btn) { btn.disabled = false; btn.textContent = "🏷️ Tags IA"; }
        return;
    }
    // Servidor retorna imediatamente — polling até o step virar "done"
    if (info) info.textContent = "⏳ Rodando em background…";
    const _edPoll = setInterval(async () => {
        try {
            const d = await xhrGet(API + "/progress");
            if (d.step === "tagging") {
                const cur = d.current || 0, tot = d.total || 0;
                const pct = tot > 0 ? Math.round(cur / tot * 100) : 0;
                if (info) info.textContent = `⏳ ${cur}/${tot} (${pct}%) — ${d.detail || ""}`;
            } else if (d.step === "done") {
                clearInterval(_edPoll);
                const msg = d.detail || "Concluído";
                if (info) info.textContent = "✅ " + msg;
                setStatus("ED+: " + msg, "success");
                if (btn) { btn.disabled = false; btn.textContent = "🏷️ Tags IA"; }
            }
        } catch {}
    }, 1500);
}

async function testGeminiKeys() {
    const keys = _collectGeminiKeys();
    if (!keys.length) { setStatus("Adicione ao menos uma chave Gemini pra testar.", "error"); return; }
    setStatus("🔌 Testando " + keys.length + " chave(s) no Gemini...", "info");
    let ok = 0, msgs = [];
    for (const k of keys) {
        try {
            const r = await xhrPost(API + "/test_gemini", { key: k });
            const res = (r.results && r.results[0]) || {};
            if (res.ok) { ok++; msgs.push(`✅ ${res.prefix}… funciona`); }
            else msgs.push(`❌ ${res.prefix || k.slice(0,4)}… ${res.code || ""} ${(res.error||"").slice(0,60)}`);
        } catch (e) { msgs.push("❌ erro: " + e.message); }
    }
    setStatus(`Teste: ${ok}/${keys.length} OK — ` + msgs.join(" · "), ok ? "success" : "error");
}

function saveConfig() {
    const payload = {};
    Object.keys(_CFG_MAP).forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const v = el.value;
        if (v) {                                  // só salva o que está preenchido
            localStorage.setItem(id, v);
            payload[_CFG_MAP[id]] = v;
        }
    });
    // Chaves Gemini (multi-box) → lista
    const gkeys = _collectGeminiKeys();
    payload.gemini_api_keys = gkeys;                 // envia sempre (permite limpar)
    xhrPost(API + "/config", payload).catch(() => {});
}

async function loadConfig() {
    // 1. Backend é a fonte primária (persistente)
    let cfg = {};
    try { cfg = await xhrGet(API + "/config"); } catch (e) {}

    Object.keys(_CFG_MAP).forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const v = cfg[_CFG_MAP[id]] || localStorage.getItem(id) || "";
        if (v) el.value = v;
    });

    // Chaves Gemini → boxes (lista do config, ou a chave única antiga separada por vírgula)
    const gbox = document.getElementById("geminiKeys");
    if (gbox) {
        gbox.innerHTML = "";
        let gkeys = cfg.gemini_api_keys;
        if (!gkeys || !gkeys.length) {
            const single = cfg.gemini_api_key || "";
            gkeys = single ? single.split(",").map(s => s.trim()).filter(Boolean) : [];
        }
        if (!gkeys.length) gkeys = [""];             // ao menos 1 box vazia
        gkeys.forEach(k => addGeminiKeyRow(k));
    }

    updateLlmHint();
    updateDensityHint();
    updateVerticalHint();

    // 2. Defaults
    if (!document.getElementById("generatedDir").value) {
        document.getElementById("generatedDir").value =
            "";
    }
    if (!document.getElementById("brollFolder").value) {
        document.getElementById("brollFolder").value =
            "";
    }
}

function toggleConfig() {
    const s = document.getElementById("configSection");
    s.style.display = s.style.display === "none" ? "block" : "none";
}

// Abre o seletor de pasta nativo (Finder) e preenche o campo indicado.
function pickFolder(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    try {
        const initial = input.value || "";
        const fs = (typeof cep !== "undefined" && cep.fs) ? cep.fs : null;
        if (!fs || !(fs.showOpenDialogEx || fs.showOpenDialog)) {
            setStatus("Seletor indisponível aqui — cole o caminho manualmente.", "error");
            return;
        }
        const res = fs.showOpenDialogEx
            ? fs.showOpenDialogEx(false, true, "Escolha a pasta", initial)
            : fs.showOpenDialog(false, true, "Escolha a pasta", initial);
        const paths = res && res.data ? res.data : [];
        if (!paths.length) return;                       // cancelou
        let p = paths[0];
        if (p.indexOf("file://") === 0) p = decodeURIComponent(p.slice(7));
        input.value = p;
        saveConfig();                                    // persiste na hora
        setStatus("Pasta selecionada: " + p, "success");
    } catch (e) {
        setStatus("Erro ao abrir o Finder: " + (e.message || e), "error");
    }
}

// Doc da VSL (texto) carregado para contexto
let _vslDoc = "";

function pickVslDoc() {
    try {
        const fs = (typeof cep !== "undefined" && cep.fs) ? cep.fs : null;
        if (!fs || !(fs.showOpenDialogEx || fs.showOpenDialog)) {
            setStatus("Seletor indisponível aqui.", "error");
            return;
        }
        const res = fs.showOpenDialogEx
            ? fs.showOpenDialogEx(false, false, "Escolha o doc da VSL (.txt/.md)", "")
            : fs.showOpenDialog(false, false, "Escolha o doc da VSL (.txt/.md)", "", ["txt", "md", "text", "rtf"]);
        const paths = res && res.data ? res.data : [];
        if (!paths.length) return;
        let p = paths[0];
        if (p.indexOf("file://") === 0) p = decodeURIComponent(p.slice(7));

        let content = "";
        // 1) Node fs (confiável — node habilitado)
        try {
            const req = (typeof cep_node !== "undefined" && cep_node.require)
                ? cep_node.require : (typeof require !== "undefined" ? require : null);
            if (req) content = req("fs").readFileSync(p, "utf8");
        } catch (e) {}
        // 2) Fallback cep.fs.readFile
        if (!content) {
            try { const r = fs.readFile(p); if (r && typeof r.data === "string") content = r.data; } catch (e) {}
        }
        content = (content || "").replace(/^﻿/, "").trim();   // remove BOM
        if (!content) {
            setStatus("Arquivo vazio ou ilegível (use .txt/.md em UTF-8): " + p, "error");
            return;
        }
        _vslDoc = content;
        const name = p.replace(/^.*[\\/]/, "");
        document.getElementById("vslDocInfo").textContent =
            `📄 ${name} — ${content.length} caracteres`;
        setStatus("Doc da VSL carregado: " + name, "success");
    } catch (e) {
        setStatus("Erro ao ler o doc: " + (e.message || e), "error");
    }
}

// ── Transcrição do Premiere (.srt) ──────────────────────────────────────────────
let _transcriptSrt = "";

function loadTranscript() {
    try {
        const fs = (typeof cep !== "undefined" && cep.fs) ? cep.fs : null;
        if (!fs || !(fs.showOpenDialogEx || fs.showOpenDialog)) {
            setStatus("Seletor indisponível aqui.", "error");
            return;
        }
        const res = fs.showOpenDialogEx
            ? fs.showOpenDialogEx(false, false, "Transcrição do Premiere (.srt/.vtt)", "")
            : fs.showOpenDialog(false, false, "Transcrição do Premiere (.srt/.vtt)", "", ["srt", "vtt"]);
        const paths = res && res.data ? res.data : [];
        if (!paths.length) return;
        let p = paths[0];
        if (p.indexOf("file://") === 0) p = decodeURIComponent(p.slice(7));

        let content = "";
        try {
            const req = (typeof cep_node !== "undefined" && cep_node.require)
                ? cep_node.require : (typeof require !== "undefined" ? require : null);
            if (req) content = req("fs").readFileSync(p, "utf8");
        } catch (e) {}
        if (!content) {
            try { const r = fs.readFile(p); if (r && typeof r.data === "string") content = r.data; } catch (e) {}
        }
        content = (content || "").replace(/^﻿/, "").trim();
        if (!content || content.indexOf("-->") === -1) {
            setStatus("SRT vazio ou sem timecodes (exporte legendas .srt do Premiere): " + p, "error");
            return;
        }
        _transcriptSrt = content;
        const name = p.replace(/^.*[\\/]/, "");
        const blocks = (content.match(/-->/g) || []).length;
        const info = document.getElementById("transcriptInfo");
        if (info) info.textContent = `📝 ${name} — ${blocks} blocos`;
        setStatus("Transcrição do Premiere carregada: " + name, "success");
    } catch (e) {
        setStatus("Erro ao ler a transcrição: " + (e.message || e), "error");
    }
}

// Traduz uma legenda .srt em inglês → português mantendo os tempos exatos e
// importa o resultado como faixa de legenda na sequência ativa (tradução
// simultânea encaixada embaixo). Saída gravada ao lado: ingles.srt → ingles.pt.srt
async function translateSrt() {
    try {
        const fs = (typeof cep !== "undefined" && cep.fs) ? cep.fs : null;
        if (!fs || !(fs.showOpenDialogEx || fs.showOpenDialog)) {
            setStatus("Seletor indisponível aqui.", "error");
            return;
        }
        const res = fs.showOpenDialogEx
            ? fs.showOpenDialogEx(false, false, "Legenda em inglês (.srt)", "")
            : fs.showOpenDialog(false, false, "Legenda em inglês (.srt)", "", ["srt", "vtt"]);
        const paths = res && res.data ? res.data : [];
        if (!paths.length) return;
        let p = paths[0];
        if (p.indexOf("file://") === 0) p = decodeURIComponent(p.slice(7));

        setStatus("🌐 Traduzindo legenda (mantendo os tempos)...", "info");
        let data;
        try {
            data = await xhrPost(API + "/translate_srt", { srt_path: p, target_lang: "pt" });
        } catch (e) {
            setStatus("Erro ao traduzir: " + (e.message || e), "error");
            return;
        }
        if (!data || !data.ok) {
            setStatus("Falha na tradução: " + ((data && data.error) || "?"), "error");
            return;
        }
        const out = data.out_path || "";
        const warn = data.warning ? "  ⚠️ " + data.warning : "";

        // Sem Premiere (ou sem caminho de saída) — só informa onde salvou.
        if (!cs || !out) {
            setStatus("✅ Legenda traduzida (" + (data.blocks || "?") + " blocos): " + out + warn, warn ? "info" : "success");
            return;
        }

        // Importa e tenta colocar na faixa de legenda da sequência ativa.
        cs.evalScript(`importCaptionSrt(${JSON.stringify(out)})`, r => {
            let info = null;
            try { info = JSON.parse(r); } catch (e) {}
            const kind = warn ? "info" : "success";
            if (info && info.ok && info.placed) {
                setStatus("✅ Legenda PT colocada na sequência (" + (data.blocks || "?") + " blocos)." + warn, kind);
            } else if (info && info.ok) {
                setStatus("✅ Traduzida e importada no projeto — arraste pra uma faixa de legenda: " + out + warn, kind);
            } else {
                setStatus("✅ Traduzida: " + out + " — importe manualmente no Premiere." + warn, kind);
            }
        });
    } catch (e) {
        setStatus("Erro: " + (e.message || e), "error");
    }
}

// #L1 — aprende com o projeto aberto: lê a composição + transcrição e envia os
// pares (texto → B-roll escolhido) pro backend guardar na memória de estilo.
function learnProject() {
    if (!cs) { setStatus("Fora do Premiere — abra um projeto finalizado.", "error"); return; }
    const videoPath = (document.getElementById("videoPath") || {}).value || "";
    if (!_transcriptSrt) autoFindSrt(videoPath.trim());   // usa .srt se houver (atalho)
    setStatus("📚 Lendo o projeto para aprender (transcreve sozinho se precisar)...", "info");
    cs.evalScript("getSequenceComposition()", async (result) => {
        let data = null;
        try { data = JSON.parse(result); } catch (e) {}
        if (!data || !data.ok) { setStatus("Não consegui ler a composição da timeline.", "error"); return; }
        try {
            const r = await xhrPost(API + "/learn_project", {
                video_clips: data.video_clips || [],
                narration_clips: data.narration_clips || [],   // Whisper se não tiver .srt
                transcript_srt: _transcriptSrt || undefined,
                project_name: data.sequence_name || undefined,
            });
            const skip = r.skipped ? ` (pulei ${r.skipped} efeito/curto)` : "";
            setStatus(`📚 Aprendi ${r.learned} escolha(s)${skip}. Memória: ${r.total} exemplos de ${r.projects} projeto(s).`, "success");
        } catch (e) {
            setStatus("Erro ao aprender: " + e.message, "error");
        }
    });
}

// #L1 lote — aprende de uma PASTA de projetos .prproj (offline, sem abrir cada um).
async function learnFolder() {
    const fs = (typeof cep !== "undefined" && cep.fs) ? cep.fs : null;
    if (!fs || !(fs.showOpenDialogEx || fs.showOpenDialog)) {
        setStatus("Seletor de pasta indisponível aqui.", "error"); return;
    }
    const res = fs.showOpenDialogEx
        ? fs.showOpenDialogEx(false, true, "Pasta com projetos .prproj", "")
        : fs.showOpenDialog(false, true, "Pasta com projetos .prproj", "");
    const paths = res && res.data ? res.data : [];
    if (!paths.length) return;
    let p = paths[0];
    if (p.indexOf("file://") === 0) p = decodeURIComponent(p.slice(7));
    setStatus("📂 Lendo projetos da pasta — pode demorar (transcreve se faltar .srt)...", "info");
    try {
        const r = await xhrPost(API + "/learn_folder", { folder: p });
        const skipped = (r.results || []).filter(x => x.error);
        let msg = `📂 ${r.projects_processed} projeto(s): +${r.total_learned} exemplos. `
                + `Memória: ${r.memory.examples} de ${r.memory.projects} projeto(s).`;
        if (skipped.length) msg += ` ⚠️ ${skipped.length} pulado(s): ${skipped[0].error}`;
        setStatus(msg, skipped.length && !r.total_learned ? "error" : "success");
    } catch (e) {
        setStatus("Erro ao aprender da pasta: " + e.message, "error");
    }
}

async function resetStyleMemory() {
    if (!confirm("Limpar TODA a memória de estilo aprendida? (recomeça do zero)")) return;
    try {
        const r = await xhrPost(API + "/style_reset", {});
        const c = r.cleared || {};
        setStatus(`🗑️ Memória limpa (apaguei ${c.examples || 0} exemplos de ${c.projects || 0} projeto(s)).`, "success");
    } catch (e) {
        setStatus("Erro ao limpar memória: " + e.message, "error");
    }
}

function _nodeRequire() {
    try {
        if (typeof cep_node !== "undefined" && cep_node.require) return cep_node.require;
        if (typeof require !== "undefined") return require;
    } catch (e) {}
    return null;
}

// Acha um .srt/.vtt no disco pro vídeo: irmão exato primeiro, senão o mais recente
// da pasta. Premiere não expõe a transcrição via script — então o usuário exporta
// uma vez e isto pega sozinho. Não sobrescreve um SRT carregado à mão.
function autoFindSrt(videoPath) {
    if (_transcriptSrt) return true;                 // já tem (manual) — respeita
    const req = _nodeRequire();
    if (!req || !videoPath) return false;
    let fs, path;
    try { fs = req("fs"); path = req("path"); } catch (e) { return false; }
    try {
        const dir = path.dirname(videoPath);
        const base = path.basename(videoPath, path.extname(videoPath));
        const cands = [];
        for (const ext of [".srt", ".vtt"]) {
            const sib = path.join(dir, base + ext);
            try { if (fs.existsSync(sib)) cands.push({ p: sib, sibling: 1, mtime: 0 }); } catch (e) {}
        }
        if (!cands.length) {
            let files = [];
            try { files = fs.readdirSync(dir); } catch (e) { files = []; }
            for (const f of files) {
                const low = f.toLowerCase();
                if (low.endsWith(".srt") || low.endsWith(".vtt")) {
                    const full = path.join(dir, f);
                    let mt = 0;
                    try { mt = fs.statSync(full).mtimeMs; } catch (e) {}
                    cands.push({ p: full, sibling: 0, mtime: mt });
                }
            }
        }
        if (!cands.length) return false;
        cands.sort((a, b) => (b.sibling - a.sibling) || (b.mtime - a.mtime));
        const chosen = cands[0].p;
        let content = "";
        try { content = fs.readFileSync(chosen, "utf8"); } catch (e) { return false; }
        content = (content || "").replace(/^﻿/, "").trim();
        if (!content || content.indexOf("-->") === -1) return false;
        _transcriptSrt = content;
        const name = chosen.replace(/^.*[\\/]/, "");
        const blocks = (content.match(/-->/g) || []).length;
        const info = document.getElementById("transcriptInfo");
        if (info) info.textContent = `📝 ${name} — ${blocks} blocos (auto)`;
        return true;
    } catch (e) {
        return false;
    }
}

function showContext(ctx) {
    const box = document.getElementById("contextBox");
    if (!box) return;
    if (!ctx || !Object.keys(ctx).length) { box.style.display = "none"; return; }
    const expert = ctx.expert || {}, product = ctx.product || {};
    const lines = [];
    if (expert.name) lines.push(`👤 Expert: ${expert.name}`);
    if (product.name) lines.push(`📦 Produto: ${product.name}`);
    if (ctx.niche) lines.push(`🎯 Nicho: ${ctx.niche}`);
    if (ctx.avatar) lines.push(`🧑 Avatar: ${ctx.avatar}`);
    const donts = ctx.visual_donts || [];
    if (donts.length) lines.push(`🚫 Evitar: ${donts.join("; ")}`);
    box.textContent = lines.join("\n");
    box.style.display = lines.length ? "block" : "none";
}

// ── Status & progress ─────────────────────────────────────────────────────────

function setStatus(msg, type = "") {
    const bar = document.getElementById("statusBar");
    if (!bar) { console.warn("[setStatus]", msg); return; }
    bar.textContent = msg;
    bar.className = "status-bar" + (type ? " " + type : "");
}

const STEP_ORDER = ["transcribing", "analyzing", "indexing", "matching"];
const STEP_LABELS = {
    transcribing:   "Transcrevendo áudio",
    understanding:  "Entendendo a VSL",
    analyzing:      "Analisando arco narrativo",
    analyzing_copy: "PHOENIX revisando copy",
    indexing:       "Indexando B-rolls",
    tagging:        "Tagueando assets",
    matching:       "Escolhendo B-rolls",
    done:           "Concluído",
    error:          "Erro",
};

function setProgress(pct, label = "") {
    const card      = document.getElementById("processingCard");
    const btn       = document.getElementById("btnProcess");
    const btnLbl    = document.getElementById("btnProcessLabel");
    const wrap      = document.getElementById("progressWrap");
    const topBar    = wrap && wrap.querySelector(".progress-bar");
    const topLabel  = document.getElementById("progressLabel");

    if (pct === null) {
        if (card)     card.classList.remove("visible");
        if (btn)      { btn.classList.remove("processing"); btn.disabled = false; }
        if (btnLbl)   btnLbl.textContent = "Processar VSL";
        if (wrap)     wrap.classList.remove("active");
        if (topLabel) topLabel.classList.remove("active");
    } else {
        if (card)    card.classList.add("visible");
        if (btn)     { btn.classList.add("processing"); btn.disabled = true; }
        if (btnLbl)  btnLbl.textContent = "Processando...";
        if (wrap)    wrap.classList.add("active");
        if (topBar)  topBar.style.width = Math.min(pct, 100) + "%";
        if (topLabel) { topLabel.classList.add("active"); if (label) topLabel.textContent = label; }
        // barra dentro do card
        const bar = document.getElementById("procBar");
        if (bar) bar.style.width = Math.min(pct, 100) + "%";
    }
}

function updateProcessingCard(step, detail, pct) {
    const stepLabel = document.getElementById("procStepLabel");
    const detailEl  = document.getElementById("procDetail");
    const bar       = document.getElementById("procBar");

    if (stepLabel) stepLabel.textContent = STEP_LABELS[step] || step;
    if (detailEl)  detailEl.textContent  = detail || "";
    if (bar && pct != null) bar.style.width = Math.min(pct, 100) + "%";

    // Dots — pinta os anteriores de verde, o atual de laranja
    STEP_ORDER.forEach((s, i) => {
        const dot = document.getElementById("dot-" + s);
        if (!dot) return;
        const idx = STEP_ORDER.indexOf(step);
        if (i < idx)     { dot.className = "proc-dot done"; }
        else if (i === idx) { dot.className = "proc-dot active"; }
        else               { dot.className = "proc-dot"; }
    });
}

// ── XHR helper ────────────────────────────────────────────────────────────────

function xhrGet(url) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("GET", url, true);
        xhr.timeout = 8000;
        xhr.onload = () => {
            try { resolve(JSON.parse(xhr.responseText)); }
            catch { reject(new Error("JSON parse error")); }
        };
        xhr.onerror   = () => reject(new Error("network error"));
        xhr.ontimeout = () => reject(new Error("timeout"));
        xhr.send();
    });
}

function xhrPost(url, body) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", url, true);
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.timeout = 2400000; // 40 min — visão local (11B) é lenta em VSLs longas
        xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
                try { resolve(JSON.parse(xhr.responseText)); }
                catch { reject(new Error("JSON parse error")); }
            } else {
                try {
                    const e = JSON.parse(xhr.responseText);
                    reject(new Error(e.detail || xhr.statusText));
                } catch { reject(new Error(xhr.statusText)); }
            }
        };
        xhr.onerror   = () => reject(new Error("network error"));
        xhr.ontimeout = () => reject(new Error("timeout"));
        xhr.send(JSON.stringify(body));
    });
}

// ── Progress polling ──────────────────────────────────────────────────────────

let _pollTimer = null;

function startPolling() {
    stopPolling();
    _pollTimer = setInterval(async () => {
        try {
            const data = await xhrGet(API + "/progress");
            handleProgress(data);
        } catch {}
    }, 1000);
}

function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

// Faixas de cada etapa na barra GERAL (0→100%) — a barra anda no pipeline inteiro,
// não reseta por etapa.
const STEP_RANGE = {
    transcribing:   [2, 15],
    understanding:  [15, 22],
    analyzing:      [22, 45],
    analyzing_copy: [45, 52],
    indexing:       [52, 66],
    tagging:        [66, 72],
    matching:       [72, 99],
};
let _lastPct = 0;

function _overallPct(step, current, total) {
    const r = STEP_RANGE[step];
    if (!r) return _lastPct || 5;
    const frac = total > 0 ? Math.min(current / total, 1) : 0;
    return Math.round(r[0] + frac * (r[1] - r[0]));
}

function handleProgress(data) {
    const step   = data.step || "";
    const detail = data.detail || "";

    const alerts = data.alerts || [];
    if (step && step !== "done" && step !== "error") {
        // barra GERAL monotônica (nunca volta atrás durante a mesma run)
        const overall = Math.max(_lastPct, _overallPct(step, data.current || 0, data.total || 0));
        _lastPct = overall;
        const label = (STEP_LABELS[step] || step) + (detail ? " — " + detail : "");
        setProgress(overall, label + `  (${overall}%)`);
        updateProcessingCard(step, detail, overall);
        // Alerta de API (cota/erro) tem prioridade no status; senão mostra o progresso.
        if (alerts.length) setStatus("⚠️ " + alerts[0].msg, "error");
        else setStatus(label, "info");
    } else if (alerts.length) {
        setStatus("⚠️ " + alerts[0].msg, "error");
    }

    if (step === "done") { _lastPct = 100; setProgress(100, "Concluído (100%)"); setTimeout(() => setProgress(null), 600); stopPolling(); }
    else if (step === "error") { setProgress(null); stopPolling(); }
}

// ── Main actions ──────────────────────────────────────────────────────────────

function detectVideo() {
    if (!cs) { setStatus("Fora do Premiere — cole o caminho manualmente.", "error"); return; }
    cs.evalScript("getSequenceComposition()", result => {
        let data = null;
        try { data = JSON.parse(result); } catch (e) {}

        if (data && data.ok && (data.narration_clips || []).length > 0) {
            _composition = data.narration_clips;
            const firstPath = _composition[0].path || "";
            document.getElementById("videoPath").value = firstPath;

            // B-rolls já na timeline (faixas ≥ 1) → não repetir na seleção (#2a)
            const narrPaths = new Set(_composition.map(c => c.path));
            _timelineBrollPaths = Array.from(new Set(
                (data.video_clips || [])
                    .filter(c => (c.track || 0) >= 1 && c.path && !narrPaths.has(c.path))
                    .map(c => c.path)
            ));

            const nClips   = data.video_clips ? data.video_clips.length : _composition.length;
            const nNarr    = _composition.length;
            const sources  = {};
            _composition.forEach(c => { if (c.path) sources[c.path] = true; });
            const nSources = Object.keys(sources).length;

            const srt = autoFindSrt(firstPath) ? " · 📝 SRT encontrado" : "";
            setStatus(
                `Composição detectada: ${nClips} clipe(s) na timeline · ` +
                `${nNarr} de narração (${nSources} fonte(s)).${srt}`,
                "success"
            );
            return;
        }

        // Fallback: comportamento antigo (primeiro clipe da V1)
        _composition = [];
        _timelineBrollPaths = [];
        cs.evalScript("getActiveVideoPath()", path => {
            if (path && path !== "EvalScript error." && path !== "") {
                document.getElementById("videoPath").value = path;
                const srt = autoFindSrt(path) ? " · 📝 SRT encontrado" : "";
                setStatus("Vídeo detectado da V1 (composição indisponível)." + srt, "success");
            } else {
                setStatus("Nenhum clipe na timeline. Cole o caminho manualmente.", "error");
            }
        });
    });
}

async function startProcess() {
    saveConfig();
    const videoPath    = document.getElementById("videoPath").value.trim();
    const brollFolder  = document.getElementById("brollFolder").value.trim();
    const anthropicKey = document.getElementById("anthropicKey").value.trim();
    const geminiKey    = _collectGeminiKeys().join(",");
    const generatedDir = document.getElementById("generatedDir").value.trim();

    if (!videoPath)   { setStatus("Informe o caminho do vídeo principal.", "error"); return; }
    if (!brollFolder) { setStatus("Informe a pasta de B-rolls.", "error"); return; }

    // Última tentativa de achar o SRT no disco (caso o caminho tenha sido colado à mão).
    if (!_transcriptSrt) autoFindSrt(videoPath);

    // Aviso: sem transcrição do Premiere, cai na automática (Whisper, qualidade menor).
    if (!_transcriptSrt) {
        const ok = confirm(
            "Nenhuma transcrição do Premiere carregada.\n\n" +
            "Para melhor qualidade, exporte a transcrição/legendas como .srt no Premiere " +
            "(Texto → Transcrição → Exportar) e carregue com o botão 📝 Transcrição.\n\n" +
            "Continuar com a transcrição automática (Whisper)?");
        if (!ok) return;
    }

    // Fecha o config para dar espaço ao progresso e segmentos
    document.getElementById("configSection").style.display = "none";
    document.getElementById("statsRow").style.display  = "none";
    document.getElementById("bottomBar").style.display = "none";
    document.getElementById("segmentList").innerHTML   = "";
    _undoCount = 0;
    updateUndoBtn();

    setStatus("Conectando ao servidor...", "info");
    _lastPct = 0;                              // zera a barra geral pra esta run
    setProgress(2, "Verificando servidor...");
    updateProcessingCard("transcribing", "Verificando servidor...", 2);

    // XMLHttpRequest síncrono — mais compatível com Chromium antigo do CEP
    const ok = await new Promise(resolve => {
        const xhr = new XMLHttpRequest();
        xhr.open("GET", API + "/status", true);
        xhr.timeout = 4000;
        xhr.onload  = () => resolve(true);
        xhr.onerror = () => resolve(false);
        xhr.ontimeout = () => resolve(false);
        try { xhr.send(); } catch { resolve(false); }
    });

    if (!ok) {
        setStatus("Servidor Python não está rodando. Execute: ./start_server.sh", "error");
        setProgress(null);
        return;
    }

    startPolling();

    try {
        // Só envia a composição se o caminho não foi editado à mão depois do detect.
        const useComposition = _composition.length > 0
            && _composition[0].path === videoPath;

        const data = await xhrPost(API + "/process", {
            video_path:        videoPath,
            broll_folder:      brollFolder,
            anthropic_api_key: anthropicKey || undefined,
            gemini_api_key:    geminiKey    || undefined,
            generated_dir:     generatedDir || undefined,
            composition:       useComposition ? _composition : undefined,
            vsl_doc:           _vslDoc || undefined,
            groq_api_key:      (document.getElementById("groqKey") || {}).value || undefined,
            transcript_srt:    _transcriptSrt || undefined,
            timeline_broll_paths: (useComposition && _timelineBrollPaths.length)
                                  ? _timelineBrollPaths : undefined,
            llm_backend:       (document.getElementById("llmModel") || {}).value || undefined,
            vision_verify:     (document.getElementById("visionVerify") || {}).checked || false,
            quality_mode:      (document.getElementById("qualityMode") || {}).checked || false,
            broll_density:     (document.getElementById("brollDensity") || {}).value || undefined,
            vertical:          (document.getElementById("brollVertical") || {}).value || undefined,
            pexels_api_key:    (document.getElementById("pexelsKey") || {}).value || undefined,
            ed_folder:         (document.getElementById("edFolder") || {}).value || undefined,
        });
        stopPolling();
        _lastPct = 100;
        setProgress(100, "Concluído (100%)");    // mostra 100% antes de sumir
        setTimeout(() => setProgress(null), 700);
        showResults(data);

    } catch (e) {
        setStatus("Erro: " + e.message, "error");
        setProgress(null);
        stopPolling();
    }
}

// ── Variantes de seleção (V1/V2/V3) ──────────────────────────────────────────

let _currentVariant = 1;

function initVariants() {
    _currentVariant = 1;
    document.querySelectorAll(".varbtn").forEach(b => {
        b.classList.toggle("var-active", parseInt(b.dataset.v) === 1);
    });
    const row = document.getElementById("variantRow");
    if (row) row.style.display = "flex";
    const hint = document.getElementById("variantHint");
    if (hint) hint.textContent = "V1 = seleção principal";
}

async function switchVariant(n) {
    if (n === _currentVariant) return;
    const hint = document.getElementById("variantHint");
    if (hint) hint.textContent = `Carregando V${n}…`;
    document.querySelectorAll(".varbtn").forEach(b => b.disabled = true);
    try {
        const data = await xhrGet(API + "/variants/" + n);
        _currentVariant = n;
        document.querySelectorAll(".varbtn").forEach(b => {
            b.classList.toggle("var-active", parseInt(b.dataset.v) === n);
            b.disabled = false;
        });
        renderSegments(data.segments || []);
        const s = data.stats || {};
        document.getElementById("statOk").textContent     = `✓ ${s.ok || 0} ok`;
        document.getElementById("statReview").textContent = `⚠ ${s.review || 0} revisão`;
        document.getElementById("statGen").textContent    = `✨ ${s.generated || 0} gerados`;
        document.getElementById("statErr").textContent    = `✗ ${s.error || 0} erros`;
        const cc = document.getElementById("statCompliance");
        if (cc) { const cb = s.compliance_blocked || 0; cc.textContent = `⛔ ${cb} compliance`; cc.style.display = cb > 0 ? "" : "none"; }
        if (hint) hint.textContent = `V${n} ativo — inserir usará esta variante`;
        setStatus(`Variante V${n}: ${(s.ok||0)+(s.review||0)} B-rolls selecionados.`, "info");
    } catch (e) {
        document.querySelectorAll(".varbtn").forEach(b => b.disabled = false);
        if (hint) hint.textContent = "";
        setStatus("Erro ao carregar variante: " + e.message, "error");
    }
}

// ── Results ───────────────────────────────────────────────────────────────────

function showResults(data) {
    const card = document.getElementById("processingCard");
    if (card) card.classList.remove("visible");

    showContext(data.context);
    const s = data.stats;
    document.getElementById("statOk").textContent      = `✓ ${s.ok} ok`;
    document.getElementById("statReview").textContent  = `⚠ ${s.review} revisão`;
    document.getElementById("statGen").textContent     = `✨ ${s.generated} gerados`;
    document.getElementById("statErr").textContent     = `✗ ${s.error} erros`;
    document.getElementById("statsRow").style.display  = "flex";

    const compBlocked = (s.compliance_blocked || 0);
    const compChip = document.getElementById("statCompliance");
    if (compChip) {
        compChip.textContent = `⛔ ${compBlocked} compliance`;
        compChip.style.display = compBlocked > 0 ? "inline-block" : "none";
    }

    const lCount = data.lettering_markers ? data.lettering_markers.length : 0;
    if (lCount > 0) {
        document.getElementById("statLettering").textContent = `🔤 ${lCount} lettering`;
        document.getElementById("statLettering").style.display = "inline-block";
    }

    window._letteringMarkers = data.lettering_markers || [];

    if (data.segments && data.segments.length > 0) {
        renderSegments(data.segments);
    } else {
        loadSegments();
    }

    initVariants();
    document.getElementById("bottomBar").style.display = "flex";
    const selMap = {
        "embedding": "busca semântica por embeddings visuais",
        "embedding+vision": "busca semântica + Claude Vision",
        "scoring": `scoring por tags (${s.tagged_assets} tagados)`,
        "clip": "fallback CLIP+LLM",
    };
    if (s.selection === "clip") {
        // a busca semântica falhou e caiu no matcher fraco (CLIP texto→imagem) — não é
        // "sucesso": avisa pra o usuário não achar que a IA escolheu bem.
        setStatus(`⚠️ ${data.segments_total} segmentos · caiu no FALLBACK (CLIP+LLM) — `
            + `a busca semântica falhou; a qualidade do match pode estar baixa. `
            + `Cheque os alertas de IA / reprocesse.`, "error");
    } else {
        setStatus(`${data.segments_total} segmentos · seleção por ${selMap[s.selection] || s.selection}.`, "success");
    }
}

async function loadSegments() {
    try {
        const data = await xhrGet(API + "/segments");
        renderSegments(data.segments || []);
    } catch (e) {
        setStatus("Erro ao carregar segmentos: " + e.message, "error");
    }
}

function renderSegments(segments) {
    const list = document.getElementById("segmentList");
    list.innerHTML = "";
    segments.forEach(seg => list.appendChild(buildCard(seg)));
    updateGenAllBtn(segments);
}

// Reconstrói UM card (após aceitar/rejeitar/trocar/undo) sem re-renderizar a lista toda.
function updateCard(seg) {
    const old = document.getElementById("seg-" + seg.index);
    if (old) old.replaceWith(buildCard(seg));
}

function buildCard(seg) {
    const card = document.createElement("div");
    card.className = "seg-card status-" + (seg.status || "review");
    card.id = "seg-" + seg.index;

    const dotClass = {ok:"dot-ok", review:"dot-review", generated:"dot-generated", error:"dot-error"}[seg.status] || "dot-default";
    const arcBadge = seg.arc_position ? `<span class="seg-arc-badge">${seg.arc_position}</span>` : "";
    const peakBadge = seg.emotional_peak >= 7 ? `<span class="seg-peak-badge">🔥 ${seg.emotional_peak}</span>` : "";
    const phoenixLabel = seg.phoenix
        ? `<div class="seg-phoenix">⚡ ${seg.phoenix.status||''}${seg.phoenix.priority?' · '+seg.phoenix.priority:''}</div>`
        : "";

    const brollName = seg.broll_filename ? seg.broll_filename.replace(/^.*[\\/]/, "") : null;
    const conf = seg.confidence ? Math.round(seg.confidence * 100) : 0;
    const confClass = conf >= 60 ? "good" : conf >= 35 ? "mid" : "";
    const ugcText = seg.ugc_prompt || "—";
    const isGenerated = seg.status === "generated";

    // Bloco B-roll
    let brollBlock;
    if (seg.status === "blocked_compliance") {
        brollBlock = `<div class="seg-compliance">⛔ ${seg.select_reason ? seg.select_reason.replace(/^⛔ Compliance: /,"") : "Compliance bloqueado"}</div>`;
    } else if (seg.status === "blocked") {
        brollBlock = `<div class="seg-blocked">⛔ Bloqueado — ${seg.select_reason || "momento protegido"}</div>`;
    } else if (seg.status === "no_broll") {
        brollBlock = `<div class="seg-nobroll">⚠ Sem B-roll — ${seg.select_reason || "gerar com IA"}</div>`;
    } else if (seg.broll_path) {
        const transLabel = seg.transition === "dissolve" ? `<span class="seg-trans">⤫ dissolve</span>`
                         : seg.transition === "cut"      ? `<span class="seg-trans">✂ corte</span>` : "";
        const pexelsBadge = seg.broll_source === "pexels"
            ? `<span class="pexels-badge">🌐 Pexels</span>` : "";
        brollBlock = `
            <div class="broll-chip">
                <span class="broll-chip-icon">🎞</span>
                <span class="broll-chip-name">${brollName || "clip"}</span>
                ${conf ? `<span class="broll-chip-score ${confClass}">${conf}%</span>` : ""}
                ${transLabel}${pexelsBadge}
            </div>
            ${seg.select_reason ? `<div class="seg-reason">${seg.select_reason}</div>` : ""}`;
    } else {
        brollBlock = `<div class="seg-reason">Sem B-roll selecionado</div>`;
    }

    // Ações editor
    const hasBroll = !!seg.broll_path;
    const accepted = seg.status === "ok";
    const actions = `
        <div class="seg-actions" style="display:flex;gap:4px;margin-top:6px">
            <button class="seg-act act-accept ${accepted?"active":""}" style="width:auto;flex:1;padding:3px 5px;font-size:10px;font-weight:600;background:${accepted?"#052e16":"#1c1c22"};color:${accepted?"#4ade80":"#71717a"};border:1px solid ${accepted?"#166534":"#27272f"};border-radius:4px;cursor:pointer"
                onclick="segmentAction(${seg.index},'accept')" ${hasBroll?"":"disabled"}>✓</button>
            <button class="seg-act act-swap" style="width:auto;flex:1;padding:3px 5px;font-size:10px;font-weight:600;background:#1c1c22;color:#71717a;border:1px solid #27272f;border-radius:4px;cursor:pointer"
                onclick="segmentAction(${seg.index},'swap')">⇄</button>
            <button class="seg-act act-reject" style="width:auto;flex:1;padding:3px 5px;font-size:10px;font-weight:600;background:#1c1c22;color:#71717a;border:1px solid #27272f;border-radius:4px;cursor:pointer"
                onclick="segmentAction(${seg.index},'reject')" ${hasBroll?"":"disabled"}>✕</button>
        </div>`;

    card.innerHTML = `
        <div class="seg-head">
            <span class="seg-num">#${seg.index + 1}</span>
            <span class="seg-tc">${formatTime(seg.start)}</span>
            ${arcBadge}
            ${peakBadge}
            <span class="seg-status-dot ${dotClass}" style="margin-left:auto"></span>
        </div>
        <div class="seg-body">
            <div class="seg-left">
                <div class="seg-text">${seg.text}</div>
                ${phoenixLabel}
                ${brollBlock}
                ${actions}
            </div>
            <div class="seg-right">
                <div class="cm-label">Copymerda</div>
                <div class="cm-prompt" id="ugc-${seg.index}">${ugcText}</div>
                <button class="btn-higgs ${isGenerated?"done":""}" id="genbtn-${seg.index}"
                    onclick="generateSegment(${seg.index})"
                    ${isGenerated?"disabled":""}>
                    ${isGenerated ? "✓ Gerado" : "⚡ Gerar 7s"}
                </button>
                <div class="gen-bar-wrap" id="genbar-${seg.index}">
                    <div class="gen-bar" id="genbarfill-${seg.index}"></div>
                </div>
                <div class="gen-prog" id="genprog-${seg.index}"></div>
            </div>
        </div>`;
    return card;
}

// ── Ações do editor (aceitar / trocar / rejeitar / desfazer) ───────────────────

async function segmentAction(index, action) {
    try {
        const res = await xhrPost(API + "/segment_action", { index, action });
        if (res && res.segment) {
            updateCard(res.segment);
            _undoCount++;
            updateUndoBtn();
            const verb = action === "accept" ? "aceito" : action === "swap" ? "trocado" : "rejeitado";
            setStatus(`Segmento ${index} ${verb}.`, "success");
        }
    } catch (e) {
        setStatus("Ação falhou: " + (e && e.message ? e.message : e), "error");
    }
}

let _undoCount = 0;

async function segmentUndo() {
    if (_undoCount <= 0) return;
    try {
        const res = await xhrPost(API + "/segment_undo", {});
        if (res && res.segment) {
            updateCard(res.segment);
            _undoCount = Math.max(0, _undoCount - 1);
            updateUndoBtn();
            setStatus(`Desfeito (segmento ${res.segment.index}).`, "info");
        }
    } catch (e) {
        setStatus("Nada para desfazer.", "info");
    }
}

function updateUndoBtn() {
    const b = document.getElementById("btnUndo");
    if (!b) return;
    b.style.display = _undoCount > 0 ? "" : "none";
    b.textContent = `↶ Desfazer (${_undoCount})`;
}

// ── Per-segment generation ────────────────────────────────────────────────────

const _genPolls = {};

// Dispara a geração de UM segmento e resolve a Promise quando terminar
// (DONE ou ERROR). Reaproveitado pelo botão individual e pelo "Gerar todas IA".
function _runGeneration(index) {
    return new Promise(resolve => {
        (async () => {
            const ugcEl   = document.getElementById("ugc-" + index);
            const btn     = document.getElementById("genbtn-" + index);
            const prog    = document.getElementById("genprog-" + index);
            const barWrap = document.getElementById("genbar-" + index);
            const barFill = document.getElementById("genbarfill-" + index);

            const prompt = ugcEl ? ugcEl.textContent.trim() : "";
            if (!prompt || prompt === "prompt não gerado" || prompt === "—") {
                if (prog) prog.textContent = "Sem prompt UGC.";
                return resolve({ index, ok: false, reason: "sem prompt" });
            }

            if (btn)     { btn.disabled = true; btn.textContent = "Enviando..."; }
            if (prog)    prog.textContent = "Aguardando Higgsfield...";
            if (barWrap) barWrap.style.display = "block";
            if (barFill) barFill.style.width = "5%";

            try {
                await xhrPost(API + "/generate_segment", { segment_index: index, ugc_prompt: prompt });
            } catch (e) {
                if (prog)    prog.textContent = "Erro: " + e.message;
                if (btn)     { btn.disabled = false; btn.textContent = "⚡ Gerar 7s"; }
                if (barWrap) barWrap.style.display = "none";
                return resolve({ index, ok: false, reason: e.message });
            }

            // Poll gen progress
            if (_genPolls[index]) clearInterval(_genPolls[index]);
            _genPolls[index] = setInterval(async () => {
                try {
                    const d = await xhrGet(API + "/gen_progress/" + index);
                    const state = d.state || "";
                    const pct   = d.pct || 0;

                    if (barFill) barFill.style.width = pct + "%";
                    if (prog)    prog.textContent = state + (pct > 0 ? ` ${pct}%` : "");
                    if (btn)     btn.textContent = state === "DONE" ? "✓ Gerado" : `${state}...`;

                    if (state === "DONE") {
                        clearInterval(_genPolls[index]);
                        if (btn)  { btn.disabled = true; btn.textContent = "✓ Gerado"; }
                        if (prog) prog.textContent = "Pronto!";
                        const card = document.getElementById("seg-" + index);
                        if (card) card.className = card.className.replace(/status-\w+/, "status-generated");
                        refreshStats();
                        resolve({ index, ok: true });
                    } else if (state === "ERROR") {
                        clearInterval(_genPolls[index]);
                        if (btn)     { btn.disabled = false; btn.textContent = "⚡ Gerar 7s"; }
                        if (prog)    prog.textContent = "Erro: " + (d.error || "falhou");
                        if (barWrap) barWrap.style.display = "none";
                        resolve({ index, ok: false, reason: d.error || "falhou" });
                    }
                } catch {}
            }, 3000);
        })();
    });
}

async function generateSegment(index) {
    await _runGeneration(index);
}

// Índices dos segmentos que a IA sugeriu gerar (sem clip de biblioteca + com prompt
// UGC). Exclui momentos bloqueados (compliance/protegidos) e já gerados.
const _AI_SKIP_STATUS = ["generated", "blocked", "blocked_compliance", "skip"];
function _pendingAIIndices(segs) {
    return segs
        .filter(s => _AI_SKIP_STATUS.indexOf(s.status) === -1
                  && !s.broll_path
                  && s.ugc_prompt && s.ugc_prompt.trim() && s.ugc_prompt.trim() !== "—")
        .map(s => s.index);
}

// Mostra/atualiza o botão "Gerar todas IA" conforme o nº de pontos pendentes.
async function updateGenAllBtn(segs) {
    const btn = document.getElementById("btnGenAll");
    if (!btn) return;
    try {
        if (!segs) { const d = await xhrGet(API + "/segments"); segs = d.segments || []; }
    } catch { return; }
    const n = _pendingAIIndices(segs).length;
    btn.textContent = `⚡ Gerar todas IA (${n})`;
    btn.classList.toggle("hidden", n === 0);
}

// Gera TODOS os B-rolls sugeridos por IA de uma vez (concorrência limitada).
async function generateAllAI() {
    const btn = document.getElementById("btnGenAll");
    let segs;
    try {
        const d = await xhrGet(API + "/segments");
        segs = d.segments || [];
    } catch (e) {
        setStatus("Erro ao listar segmentos: " + (e && e.message ? e.message : e), "error");
        return;
    }

    const pending = _pendingAIIndices(segs);
    if (!pending.length) {
        setStatus("Nenhum ponto de IA pendente para gerar.", "info");
        if (btn) btn.classList.add("hidden");
        return;
    }

    if (btn) { btn.disabled = true; btn.textContent = `⚡ Gerando… (0/${pending.length})`; }
    document.getElementById("btnApproveAll").disabled = true;

    const total = pending.length;
    let done = 0, failed = 0;
    setStatus(`Gerando ${total} B-rolls com IA (0/${total})… isso pode levar alguns minutos.`, "info");

    // Pool de concorrência — Higgsfield é pesado/cobrado; 2 por vez.
    const CONCURRENCY = 2;
    const queue = pending.slice();
    async function worker() {
        while (queue.length) {
            const idx = queue.shift();
            const r = await _runGeneration(idx);
            if (r && r.ok) done++; else failed++;
            const n = done + failed;
            setStatus(`Gerando B-rolls com IA (${n}/${total}) — ${done} ok${failed ? `, ${failed} falhas` : ""}…`, "info");
            if (btn) btn.textContent = `⚡ Gerando… (${n}/${total})`;
        }
    }
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, total) }, worker));

    document.getElementById("btnApproveAll").disabled = false;
    if (btn) btn.disabled = false;
    await updateGenAllBtn();
    setStatus(
        `✅ Geração concluída: ${done} ok${failed ? `, ${failed} falhas` : ""}. ` +
        `Agora clique em "Aprovar todos e inserir".`,
        failed ? "error" : "success"
    );
}

async function refreshStats() {
    try {
        const d = await xhrGet(API + "/segments");
        const segs = d.segments || [];
        const ok  = segs.filter(s => s.status === "ok").length;
        const gen = segs.filter(s => s.status === "generated").length;
        const rev = segs.filter(s => s.status === "review").length;
        const err = segs.filter(s => s.status === "error").length;
        const comp = segs.filter(s => s.status === "blocked_compliance").length;
        document.getElementById("statOk").textContent  = `✓ ${ok} ok`;
        document.getElementById("statGen").textContent = `✨ ${gen} gerados`;
        document.getElementById("statReview").textContent = `⚠ ${rev} revisão`;
        document.getElementById("statErr").textContent = `✗ ${err} erros`;
        const compChip = document.getElementById("statCompliance");
        if (compChip) {
            compChip.textContent = `⛔ ${comp} compliance`;
            compChip.style.display = comp > 0 ? "" : "none";
        }
        updateGenAllBtn(segs);
    } catch {}
}

// ── Timeline insertion ────────────────────────────────────────────────────────

async function approveAllAndInsert() {
    document.getElementById("btnApproveAll").disabled = true;
    document.getElementById("btnInsert").disabled = true;
    setStatus("Aprovando todos os segmentos...", "info");
    try {
        const r = await xhrPost(API + "/approve_all", {});
        setStatus(`${r.changed} segmentos aprovados. Inserindo na timeline...`, "info");
        await _doInsert();
    } catch (e) {
        setStatus("Erro: " + e.message, "error");
    }
    document.getElementById("btnApproveAll").disabled = false;
    document.getElementById("btnInsert").disabled = false;
}

async function insertTimeline() {
    if (!cs) { setStatus("Fora do Premiere — não é possível inserir.", "error"); return; }
    document.getElementById("btnInsert").disabled = true;
    document.getElementById("btnApproveAll").disabled = true;
    setStatus("Buscando clips...", "info");
    await _doInsert();
    document.getElementById("btnInsert").disabled = false;
    document.getElementById("btnApproveAll").disabled = false;
}

// Inserção em lotes de 10 para não estourar o limite de string do evalScript
async function _doInsert() {
    if (!cs) { setStatus("Fora do Premiere — não é possível inserir.", "error"); return; }

    let insertable;
    try {
        const data = await xhrGet(API + "/matches");
        insertable = data.insertable;
    } catch (e) {
        setStatus("Erro ao buscar matches: " + e.message, "error");
        return;
    }

    if (!insertable || insertable.length === 0) {
        setStatus("Nenhum clip para inserir.", "error");
        return;
    }

    const BATCH = 10;
    let totalInserted = 0;
    let totalErrors = [];

    setStatus(`Inserindo ${insertable.length} clips em lotes...`, "info");

    for (let i = 0; i < insertable.length; i += BATCH) {
        const batch = insertable.slice(i, i + BATCH);
        const jsonStr = JSON.stringify(batch);
        const batchNum = Math.floor(i / BATCH) + 1;
        const totalBatches = Math.ceil(insertable.length / BATCH);

        setStatus(`Lote ${batchNum}/${totalBatches} — inserindo clips ${i+1}–${Math.min(i+BATCH, insertable.length)}...`, "info");

        await new Promise(resolve => {
            cs.evalScript(`insertBRolls(${JSON.stringify(jsonStr)})`, result => {
                try {
                    const res = JSON.parse(result);
                    if (res.ok) {
                        totalInserted += res.inserted;
                        if (res.errors) totalErrors = totalErrors.concat(res.errors);
                    } else {
                        // insertBRolls retornou erro estruturado (ex.: sem sequência ativa)
                        totalErrors.push(res.error || "erro desconhecido no JSX");
                    }
                } catch {
                    // evalScript falhou no nível do ExtendScript (host.jsx não carregou,
                    // exceção não tratada, etc.) — surface em vez de engolir.
                    totalErrors.push("JSX: " + String(result || "sem resposta").slice(0, 160));
                }
                resolve();
            });
        });
    }

    let msg;
    if (totalInserted === 0) {
        // Nada inserido = falha real. Mostra o primeiro erro pra diagnosticar.
        const first = totalErrors.length ? ` — ${totalErrors[0]}` : "";
        setStatus(`❌ Nenhum clip inserido (${insertable.length} tentados)${first}`, "error");
        if (totalErrors.length) console.warn("[insert] erros:", totalErrors);
        return;
    }
    msg = `✅ ${totalInserted}/${insertable.length} clips inseridos na faixa V2.`;
    if (totalErrors.length > 0) {
        msg += ` (${totalErrors.length} erros — ${totalErrors[0]})`;
        console.warn("[insert] erros:", totalErrors);
    }

    const markers = window._letteringMarkers || [];
    if (markers.length > 0) {
        await new Promise(resolve => {
            cs.evalScript(`insertLetteringMarkers(${JSON.stringify(JSON.stringify(markers))})`, r => {
                try { const mr = JSON.parse(r); msg += ` + ${mr.inserted} lettering.`; } catch {}
                resolve();
            });
        });
    }

    setStatus(msg, "success");
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTime(sec) {
    const m = Math.floor(sec / 60);
    const s = (sec % 60).toFixed(1).padStart(4, "0");
    return `${m}:${s}`;
}

// ── Reindexação da biblioteca (busca semântica) ───────────────────────────────

async function reindexLibrary(rebuild) {
    const folder = (document.getElementById("brollFolder") || {}).value || "";
    if (!folder) { setStatus("Defina a pasta de B-rolls primeiro.", "error"); return; }
    if (rebuild && !confirm("Reconstruir do ZERO apaga o índice atual e reprocessa TODOS os clips. Pode levar vários minutos. Continuar?")) return;
    if (!(await pingStatus())) { setStatus("Backend offline.", "error"); return; }
    const reindexBtn = document.getElementById("btnReindex");
    const rebuildBtn = document.getElementById("btnRebuild");
    reindexBtn.disabled = true; rebuildBtn.disabled = true;
    const active = rebuild ? rebuildBtn : reindexBtn;
    active.textContent = rebuild ? "♻️ Reconstruindo..." : "🔄 Indexando...";
    setStatus(rebuild ? "Reconstruindo o índice do zero (pode demorar)..."
                      : "Indexando clips novos/modificados...", "info");
    startPolling();
    try {
        const res = await xhrPost(API + "/reindex", { folder, rebuild });
        stopPolling(); setProgress(null);
        setStatus(`✅ Índice ${rebuild ? "reconstruído" : "atualizado"}: ${res.indexed} clips prontos pra busca semântica.`, "success");
    } catch (e) {
        stopPolling(); setProgress(null);
        setStatus("Erro ao indexar: " + (e && e.message ? e.message : e), "error");
    }
    reindexBtn.disabled = false; rebuildBtn.disabled = false;
    reindexBtn.textContent = "🔄 Reindexar Biblioteca";
    rebuildBtn.textContent = "♻️ Reconstruir";
}

// ── Auto-tagging dos assets (Melhoria 2) ──────────────────────────────────────

async function tagAssets() {
    const folder = (document.getElementById("brollFolder") || {}).value || "";
    if (!folder) { setStatus("Defina a pasta de B-rolls primeiro.", "error"); return; }
    const btn  = document.getElementById("btnTag");
    const info = document.getElementById("tagInfo");
    if (!(await pingStatus())) {
        setStatus("Backend offline — abra/aguarde o servidor.", "error"); return;
    }
    btn.disabled = true;
    btn.textContent = "🏷️ Tagueando...";
    if (info) info.textContent = "⏳ Iniciando…";
    setStatus("Gerando tags semânticas dos assets (pode demorar)...", "info");

    // Atualiza o tagInfo em tempo real sem depender do processCard (que fica escondido)
    const _tagPoll = setInterval(async () => {
        try {
            const d = await xhrGet(API + "/progress");
            if (d.step !== "tagging") return;
            const cur = d.current || 0, tot = d.total || 0;
            const pct = tot > 0 ? Math.round(cur / tot * 100) : 0;
            if (info) info.textContent = `⏳ ${cur}/${tot} (${pct}%) — ${d.detail || ""}`;
            setStatus(`Tagueando: ${cur}/${tot} (${pct}%) — ${d.detail || ""}`, "info");
        } catch {}
    }, 1500);

    try {
        const res = await xhrPost(API + "/tag_assets", { folder });
        clearInterval(_tagPoll);
        setProgress(null);
        const capTxt = (res.captioned != null) ? ` · 🖼️ ${res.captioned} com legenda local` : "";
        if (info) info.textContent =
            `✅ ${res.tagged} tagados (${res.vision} por imagem, ${res.needs_manual} p/ revisar) · ` +
            `${res.skipped} já tinham tag${capTxt} · total ${res.total}.`;
        setStatus(`✅ Tags prontas: ${res.tagged}/${res.total}${res.captioned != null ? ` · ${res.captioned} legendados` : ""}. A busca semântica já vai usar.`, "success");
    } catch (e) {
        clearInterval(_tagPoll);
        setProgress(null);
        if (info) info.textContent = "❌ Erro: " + (e && e.message ? e.message : e);
        setStatus("Erro no tagging: " + (e && e.message ? e.message : e), "error");
    }
    btn.disabled = false;
    btn.textContent = "🏷️ Taguear assets (IA)";
}

// ── PHOENIX — Copy Chief (Melhoria 10) ────────────────────────────────────────

let _phoenixMap = [];

async function analyzeCopy() {
    if (!_vslDoc || !_vslDoc.trim()) {
        setStatus("Carregue o doc da VSL antes de analisar.", "error"); return;
    }
    if (!(await pingStatus())) { setStatus("Backend offline.", "error"); return; }
    const btn = document.getElementById("btnPhoenix");
    btn.disabled = true; btn.textContent = "🧠 Analisando...";
    setStatus("PHOENIX revisando a copy (pode levar ~1 min)...", "info");
    try {
        const res = await xhrPost(API + "/analyze_copy", { doc: _vslDoc });
        showPhoenix(res);
        setStatus("PHOENIX concluiu a análise.", "success");
    } catch (e) {
        setStatus("Erro no PHOENIX: " + (e && e.message ? e.message : e), "error");
    }
    btn.disabled = false; btn.textContent = "🧠 Analisar Copy";
}

function showPhoenix(res) {
    _phoenixMap = res.broll_map || [];
    document.getElementById("phoenixScore").textContent =
        res.score ? `Score geral: ${res.score}/10` : "";
    document.getElementById("phoenixAnalysis").textContent = res.analysis || "(sem texto)";

    renderCauseMap(res.cause_map || {});

    const mapEl = document.getElementById("phoenixMap");
    if (_phoenixMap.length) {
        mapEl.innerHTML = "<b>Mapa de B-roll sugerido:</b>" + _phoenixMap.map(e => {
            const pri = (e.priority || "").toLowerCase();
            const st  = (e.status || "").toLowerCase();
            return `<div class="row">
                <span class="pri-${pri}">●</span>
                <span style="flex:1">${(e.block_type||"")} — ${(e.broll_description||"").slice(0,60)}</span>
                <span class="st-${st}">${st}</span></div>`;
        }).join("");
        document.getElementById("btnUsePhoenix").style.display = "";
    } else {
        mapEl.innerHTML = "<i>Sem mapa estruturado nesta resposta.</i>";
        document.getElementById("btnUsePhoenix").style.display = "none";
    }
    document.getElementById("phoenixModal").classList.add("open");
}

function renderCauseMap(cm) {
    const el = document.getElementById("phoenixCause");
    if (!cm || (!cm.problema_aparente && !cm.causa_real && !cm.ingredientes)) {
        el.innerHTML = ""; el.style.display = "none"; return;
    }
    el.style.display = "block";
    const list = a => (Array.isArray(a) ? a : (a ? [a] : []));
    const block = (title, cls, o) => {
        if (!o) return "";
        const sym = list(o.sintomas).length ? `<div class="ph-sym">Sintomas: ${list(o.sintomas).join(" · ")}</div>` : "";
        const vir = o.linguagem_virada ? `<div class="ph-sym">Virada: "${o.linguagem_virada}"</div>` : "";
        const vis = list(o.broll_visual).map(v => `<div class="ph-vis">▸ ${v}</div>`).join("");
        const hig = list(o.higgs_prompts).map(h => `<div class="ph-higgs">Higgs: ${h}</div>`).join("");
        const par = o.paragrafos ? ` <span style="color:#777">(par. ${o.paragrafos})</span>` : "";
        return `<div class="ph-sec"><span class="ph-h ${cls}">${title}</span>${par}
            ${o.descricao ? `<div>${o.descricao}</div>` : ""}${sym}${vir}${vis}${hig}</div>`;
    };
    const ings = list(cm.ingredientes).map(i =>
        `<div class="ph-ing"><b>${i.nome||"?"}</b>${i.dosagem?` ${i.dosagem}`:""} — ${i.claim||""}
         ${i.broll_visual?`<div class="ph-vis">▸ ${i.broll_visual}</div>`:""}
         ${i.higgs_prompt?`<div class="ph-higgs">Higgs: ${i.higgs_prompt}</div>`:""}</div>`).join("");
    el.innerHTML =
        `<b style="color:#fff">🧬 Mapa de Causa Real</b>` +
        block("PROBLEMA APARENTE", "", cm.problema_aparente) +
        block("CAUSA REAL", "real", cm.causa_real) +
        block("MECANISMO", "", cm.mecanismo) +
        (ings ? `<div class="ph-sec"><span class="ph-h">INGREDIENTES</span>${ings}</div>` : "");
}

function closePhoenix() {
    document.getElementById("phoenixModal").classList.remove("open");
}

async function usePhoenixMap() {
    try {
        const res = await xhrPost(API + "/phoenix_map", { broll_map: _phoenixMap });
        closePhoenix();
        setStatus(`Mapa do PHOENIX aplicado (${res.count} pontos). Rode "Processar VSL".`, "success");
    } catch (e) {
        setStatus("Erro ao aplicar mapa: " + (e && e.message ? e.message : e), "error");
    }
}

// ── Biblioteca local ──────────────────────────────────────────────────────────

async function checkLibraryConfig() {
    try {
        const data = await xhrGet(API + "/config");
        if (data.configured && data.library_folder) {
            showLibraryRow(data.library_folder);
        } else {
            // Primeira vez — abre modal automaticamente
            document.getElementById("libraryEmpty").style.display = "block";
            openLibraryModal(true);
        }
    } catch {
        // Servidor offline no init — só mostra botão de configurar
        document.getElementById("libraryEmpty").style.display = "block";
    }
}

function showLibraryRow(folder) {
    document.getElementById("libraryPathLabel").textContent = folder;
    document.getElementById("libraryRow").style.display = "flex";
    document.getElementById("libraryEmpty").style.display = "none";
}

function openLibraryModal(isFirstTime = false) {
    const modal = document.getElementById("libraryModal");
    if (!modal) return;
    const skipBtn = modal.querySelector(".btn-modal-cancel");   // botão "Pular"
    if (skipBtn) skipBtn.style.display = isFirstTime ? "block" : "none";
    modal.classList.add("open");
    const inp = document.getElementById("libraryFolderInput");
    if (inp) inp.focus();
}

function closeLibraryModal() {
    document.getElementById("libraryModal").classList.remove("open");
}

async function saveLibraryFolder() {
    const folder = document.getElementById("libraryFolderInput").value.trim();
    if (!folder) { return; }
    try {
        await xhrPost(API + "/config", { library_folder: folder });
        showLibraryRow(folder);
        closeLibraryModal();
        setStatus("Biblioteca configurada: " + folder, "success");
    } catch (e) {
        setStatus("Erro ao salvar biblioteca: " + e.message, "error");
    }
}

// Salva com Enter no campo
document.getElementById("libraryFolderInput").addEventListener("keydown", e => {
    if (e.key === "Enter") saveLibraryFolder();
    if (e.key === "Escape") closeLibraryModal();
});

// ── Auto-start do backend ───────────────────────────────────────────────────
// Caminho do projeto (onde está start_server.sh + backend). Ajuste se mudar de lugar.
const PROJECT_DIR = "";

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function pingStatus() {
    return new Promise(resolve => {
        const xhr = new XMLHttpRequest();
        xhr.open("GET", API + "/status", true);
        xhr.timeout = 1500;
        xhr.onload  = () => resolve(true);
        xhr.onerror = () => resolve(false);
        xhr.ontimeout = () => resolve(false);
        try { xhr.send(); } catch { resolve(false); }
    });
}

function spawnBackend() {
    try {
        const req = (typeof cep_node !== "undefined" && cep_node.require)
            ? cep_node.require : (typeof require !== "undefined" ? require : null);
        if (!req) return false;
        const cp = req("child_process");
        const child = cp.spawn("/bin/bash", [PROJECT_DIR + "/start_server.sh"], {
            detached: true, stdio: "ignore", cwd: PROJECT_DIR,
        });
        child.unref();
        return true;
    } catch (e) {
        setStatus("Auto-start indisponível: " + (e.message || e), "error");
        return false;
    }
}

async function ensureBackend() {
    if (await pingStatus()) return true;
    setStatus("Iniciando backend (servidor + Ollama)...", "info");
    if (!spawnBackend()) {
        setStatus("Inicie o backend: ./start_server.sh", "error");
        return false;
    }
    for (let i = 0; i < 45; i++) {           // ~70s (Ollama + uvicorn)
        await _sleep(1500);
        if (await pingStatus()) { setStatus("Backend pronto.", "success"); return true; }
    }
    setStatus("Backend demorou a subir. Tente ./start_server.sh", "error");
    return false;
}

async function refreshLlmStatus() {
    try {
        const s = await xhrGet(API + "/llm_status");
        const backends = s.backends || {};
        // Marca/desabilita cada BOTÃO de modelo (.llmbtn) conforme a chave existir.
        // (#llmModel virou <input hidden>; iterar .options era no-op silencioso.)
        // "Auto" sempre habilitado (roteia pro que houver); "Local"/Groq/Gemini/Claude
        // dependem do backend estar disponível.
        document.querySelectorAll("#llmButtons .llmbtn").forEach(btn => {
            const v = btn.dataset.v;
            const ok = v === "auto" || backends[v] === true;
            btn.disabled = !ok;
            btn.title = ok ? "" : "sem chave / indisponível — configure nas Configurações";
            btn.style.opacity = ok ? "" : "0.4";
            btn.style.cursor = ok ? "" : "not-allowed";
        });
        // Se a escolha salva ficou indisponível (ex.: removeu a chave), volta pro Auto.
        const cur = (document.getElementById("llmModel") || {}).value || "auto";
        if (cur !== "auto" && backends[cur] === false && typeof setLlm === "function") {
            setLlm("auto");
        }
    } catch (e) {}
}

async function refreshAIHealth() {
    const el = document.getElementById("aiHealth");
    if (!el) return;
    try {
        const h = await xhrGet(API + "/ai_health");
        const dot = ok => ok ? "🟢" : "⚫";
        const oll = h.ollama || {};
        const rows = [];
        // Alertas de API (cota/erro/chave) em destaque no topo
        (h.alerts || []).forEach(a => {
            rows.push(`<div class="ai-row" style="color:#f06060;font-weight:600">⚠️ ${a.msg}</div>`);
        });
        // Ollama local = base grátis (sem cota); Gemini de reserva.
        rows.push(`<div class="ai-row">${dot(oll.running)} Ollama ${oll.running
            ? `(${(oll.models || []).length} modelos${oll.has_vision ? ", visão ✓" : ", sem visão"})`
            : "não está rodando"}</div>`);
        rows.push(`<div class="ai-row">${dot(h.groq)} Groq ${h.groq ? "conectado (rápido, grátis)" : "sem chave"}</div>`);
        rows.push(`<div class="ai-row">${dot(h.gemini)} Gemini ${h.gemini ? `conectado · ${h.gemini_keys || 1} chave(s)` : "sem chave"}</div>`);
        // resumo do roteamento (nomes curtos)
        const r = h.routing || {};
        const shortModel = t => ((r[t] || ["—"])[0]).replace(/^.*\//, "").replace(":free", "");
        rows.push(`<div class="ai-row" style="color:#777;margin-top:3px">classificador→${shortModel("classifier")} · ugc→${shortModel("ugc_prompt")} · visão→${shortModel("vision_verify")} · phoenix→${shortModel("phoenix")}</div>`);
        // Visão disponível se houver qualquer backend de visão (Ollama local ou Gemini)
        const visionOK = (h.vision_chain || []).length > 0;
        if (!oll.running) {
            rows.push(`<div class="ai-install" onclick="showOllamaHelp()">Ollama não está rodando → rode ./start_server.sh</div>`);
        } else if (!visionOK) {
            rows.push(`<div class="ai-install" onclick="showOllamaHelp()">Sem IA de visão → baixe o modelo local</div>`);
        }
        el.innerHTML = rows.join("");
    } catch (e) { el.innerHTML = ""; }
}

function showOllamaHelp() {
    setStatus("IA de visão local (grátis, sem cota): no terminal rode " +
        "'ollama pull llama3.2-vision:11b' (~8GB, uma vez). Depois reabra o painel.", "info");
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function boot() {
    const up = await ensureBackend();
    await loadConfig();
    checkLibraryConfig();
    if (up) {
        refreshLlmStatus(); refreshAIHealth();
        // Revê saúde + alertas de API a cada 30s (mostra cota estourada mesmo parado)
        setInterval(() => { refreshAIHealth().catch(() => {}); }, 30000);
    }
}
boot();
