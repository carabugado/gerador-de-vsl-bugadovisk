/**
 * ExtendScript — Tradução Simultânea (roda dentro do Premiere Pro).
 * Importa um .srt traduzido e tenta colocá-lo como faixa de legenda na
 * sequência ativa. Self-contained: não depende do host.jsx do painel VSL.
 */

function getActiveSequence() {
    if (!app || !app.project) return null;
    return app.project.activeSequence || null;
}

var TICKS_PER_SEC = 254016000000;

/** Posição do playhead (CTI) da sequência ativa, em segundos (string). */
function getPlayheadSeconds() {
    try {
        var seq = getActiveSequence();
        if (!seq) return "0";
        var pos = seq.getPlayerPosition();
        if (!pos) return "0";
        if (pos.seconds !== undefined && pos.seconds !== null) return String(pos.seconds);
        if (pos.ticks) return String(parseFloat(pos.ticks) / TICKS_PER_SEC);
        return "0";
    } catch (e) { return "0"; }
}

/** Caminho da mídia da V1 da sequência ativa (pra achar o .srt ao lado). */
function getActiveVideoPath() {
    try {
        var seq = getActiveSequence();
        if (!seq) return "";
        var v1 = seq.videoTracks[0];
        if (!v1 || v1.clips.numItems === 0) return "";
        return v1.clips[0].projectItem.getMediaPath();
    } catch (e) { return ""; }
}

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

/**
 * Importa um .srt traduzido pro projeto e tenta colocá-lo como faixa de legenda
 * na sequência ativa. A API de captions do Premiere varia MUITO entre versões;
 * por isso a importação (sempre confiável) fica separada da colocação (best-effort).
 * Se a colocação automática falhar, o item fica no projeto pronto pra arrastar.
 */
function importCaptionSrt(path) {
    try {
        var seq = getActiveSequence();
        if (!seq) return JSON.stringify({ ok: false, error: "Nenhuma sequência ativa." });

        // 1) Importa o .srt pro projeto (vira item de legenda/caption no bin).
        var item = findInProject(app.project.rootItem, path);
        if (!item) {
            app.project.importFiles([path], true, app.project.rootItem, false);
            item = findInProject(app.project.rootItem, path);
        }
        var imported = !!item;

        // 2) Best-effort: insere na timeline como nova faixa de legenda em t=0.
        //    createCaptionTrack existe em Premiere ~2020+; assinatura varia, então
        //    tentamos algumas e ignoramos a falha (o item já está importado).
        var placed = false;
        var placeError = "";
        if (item && typeof seq.createCaptionTrack === "function") {
            var t0 = new Time();
            t0.ticks = "0";
            try {
                seq.createCaptionTrack(item, t0);
                placed = true;
            } catch (e1) {
                try {
                    seq.createCaptionTrack(item, t0, seq.videoTracks.numTracks);
                    placed = true;
                } catch (e2) {
                    placeError = e2.toString();
                }
            }
        } else {
            placeError = "createCaptionTrack indisponível nesta versão do Premiere";
        }

        return JSON.stringify({
            ok: imported,
            imported: imported,
            placed: placed,
            place_error: placeError,
            path: path
        });
    } catch (e) {
        return JSON.stringify({ ok: false, error: e.toString() });
    }
}
