/**
 * ExtendScript — roda dentro do Premiere Pro.
 * Monta um corte de highlights: pega UM vídeo de origem e enfileira os trechos
 * selecionados (in/out) na V1 da sequência ativa, na ordem recebida.
 *
 * Não-destrutivo: os clips são ANEXADOS ao final do que já existe na V1.
 * Crie uma sequência nova e vazia antes de montar, se quiser começar do zero.
 */

var TICKS = 254016000000;

function getActiveSequence() {
    if (!app || !app.project) return null;
    return app.project.activeSequence || null;
}

/** Caminho da mídia do 1º clip da V1 — pra detectar a origem direto da timeline. */
function getActiveVideoPath() {
    try {
        var seq = getActiveSequence();
        if (!seq) return "";
        var v1 = seq.videoTracks[0];
        if (!v1 || v1.clips.numItems === 0) return "";
        return v1.clips[0].projectItem.getMediaPath();
    } catch (e) { return ""; }
}

function getActiveSequenceName() {
    try {
        var seq = getActiveSequence();
        return seq ? (seq.name || "") : "";
    } catch (e) { return ""; }
}

/** Acha um projectItem já importado pelo caminho de mídia. */
function findInProject(item, path) {
    if (!item) return null;
    try {
        if (item.type === ProjectItemType.CLIP && item.getMediaPath() === path)
            return item;
    } catch (e) {}
    try {
        if (item.children) {
            for (var i = 0; i < item.children.numItems; i++) {
                var found = findInProject(item.children[i], path);
                if (found) return found;
            }
        }
    } catch (e) {}
    return null;
}

function timeFromSec(sec) {
    var t = new Time();
    t.ticks = String(Math.round(sec * TICKS));
    return t;
}

/**
 * Define in/out de origem no projectItem antes do overwrite.
 * A assinatura de setInPoint(time, mediaType) varia entre versões do Premiere —
 * por isso tentamos alguns mediaType e ignoramos os que estouram.
 */
function setSourceInOut(item, inSec, outSec) {
    var tin = timeFromSec(inSec);
    var tout = timeFromSec(outSec);
    var ok = false;
    var types = [4, 1, 2];   // 4 = vídeo+áudio (mais comum), 1 = vídeo, 2 = áudio
    for (var k = 0; k < types.length; k++) {
        try {
            item.setInPoint(tin, types[k]);
            item.setOutPoint(tout, types[k]);
            ok = true;
        } catch (e) { /* mediaType inválido nesta versão — tenta o próximo */ }
    }
    return ok;
}

/** Maior ponto final (em ticks) entre os clips de uma faixa — pra anexar ao fim. */
function trackEndTicks(track) {
    var max = 0;
    try {
        for (var c = 0; c < track.clips.numItems; c++) {
            var endT = parseFloat(track.clips[c].end.ticks);
            if (endT > max) max = endT;
        }
    } catch (e) {}
    return max;
}

var TYPE_COLOR = { humor: 1, debate: 3, nerdola: 4, reacao: 8, momento: 2, insight: 5 };

/** Remove todos os clips de todas as faixas (deixa a sequência limpa). */
function clearAllClips(seq) {
    var groups = [seq.videoTracks, seq.audioTracks];
    for (var g = 0; g < groups.length; g++) {
        var tracks = groups[g];
        for (var t = 0; t < tracks.numTracks; t++) {
            var trk = tracks[t];
            for (var c = trk.clips.numItems - 1; c >= 0; c--) {
                try { trk.clips[c].remove(false, false); } catch (e) {}
            }
        }
    }
}

/**
 * Cria uma sequência nova já com as configurações da origem (resolução/fps),
 * limpa e pronta pra receber o corte. Deixa ela ativa/visível no Premiere.
 */
function makeSequenceFromSource(item, name) {
    // 1) createNewSequenceFromClips: herda os settings da footage automaticamente
    try {
        if (typeof app.project.createNewSequenceFromClips === "function") {
            var s = app.project.createNewSequenceFromClips(name, [item], app.project.rootItem);
            if (s) {
                clearAllClips(s);   // tira o clip cheio que ele coloca por padrão
                try { app.project.activeSequence = s; } catch (e) {}
                return s;
            }
        }
    } catch (e) { /* versão sem essa API — cai no fallback */ }

    // 2) Fallback: sequência com preset padrão (pode não bater 100% com a footage)
    try {
        var s2 = app.project.createNewSequence(name, "");
        if (s2) { try { app.project.activeSequence = s2; } catch (e) {} return s2; }
    } catch (e) {}

    return null;
}

/**
 * matchesJSON: { "source": "/path/video.mp4", "clips": [ {in, out, titulo, tipo} ... ] }
 * 'in'/'out' em segundos, na ordem desejada. Retorna JSON com resultado.
 */
function buildHighlightCut(matchesJSON) {
    var data;
    try { data = JSON.parse(matchesJSON); }
    catch (e) { return JSON.stringify({ ok: false, error: "JSON inválido: " + e.toString() }); }

    var clips = data.clips || [];
    if (!clips.length) return JSON.stringify({ ok: false, error: "Nenhum clip na lista." });

    var srcPath = data.source || "";
    if (!srcPath) return JSON.stringify({ ok: false, error: "Caminho do vídeo de origem não informado." });

    // Importa (ou reusa) a origem
    var item = findInProject(app.project.rootItem, srcPath);
    if (!item) {
        try {
            var imported = app.project.importFiles([srcPath], true, app.project.rootItem, false);
            if (imported && imported.numItems > 0) item = imported[0];
            else if (imported && imported.length > 0) item = imported[0];
        } catch (e) {
            return JSON.stringify({ ok: false, error: "Falha ao importar origem: " + e.toString() });
        }
    }
    if (!item) return JSON.stringify({ ok: false, error: "Não consegui importar o vídeo de origem: " + srcPath });

    // Sequência de destino: nova (settings da origem) ou a ativa
    var seq, createdNew = false;
    if (data.create_sequence) {
        seq = makeSequenceFromSource(item, data.sequence_name || "HIGHLIGHTS");
        if (!seq) return JSON.stringify({ ok: false, error: "Não consegui criar uma sequência nova automaticamente." });
        createdNew = true;
    } else {
        seq = getActiveSequence();
        if (!seq) return JSON.stringify({ ok: false, error: "Nenhuma sequência ativa. Crie uma ou use 'Cortar em nova sequência'." });
    }

    var vTrack = seq.videoTracks[0];   // V1
    var posTicks = trackEndTicks(vTrack);   // anexa ao fim do que já existe

    var inserted = 0;
    var markers = 0;
    var errors = [];

    for (var i = 0; i < clips.length; i++) {
        var c = clips[i];
        var inSec = parseFloat(c["in"]);
        var outSec = parseFloat(c["out"]);
        if (isNaN(inSec) || isNaN(outSec) || outSec <= inSec) {
            errors.push("Clip " + (i + 1) + ": in/out inválido");
            continue;
        }
        try {
            setSourceInOut(item, inSec, outSec);

            var pos = new Time();
            pos.ticks = String(Math.round(posTicks));
            vTrack.overwriteClip(item, pos);

            // Marcador com o título, colorido pelo tipo
            try {
                var startSec = posTicks / TICKS;
                var mk = seq.markers.createMarker(startSec);
                mk.name = c.titulo || ("Highlight " + (i + 1));
                mk.colorByIndex = TYPE_COLOR[c.tipo] || 0;
                markers++;
            } catch (em) { /* marcador é opcional */ }

            posTicks += (outSec - inSec) * TICKS;
            inserted++;
        } catch (e) {
            errors.push("Clip " + (i + 1) + " (" + (c.titulo || "") + "): " + e.toString());
        }
    }

    // Limpa in/out da origem pra não deixar o projectItem "marcado"
    try { item.clearInPoint(4); item.clearOutPoint(4); } catch (e) {}

    return JSON.stringify({
        ok: true,
        inserted: inserted,
        markers: markers,
        created_new: createdNew,
        sequence_name: seq.name || "",
        total_seconds: Math.round(posTicks / TICKS),
        errors: errors
    });
}
