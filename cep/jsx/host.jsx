/**
 * ExtendScript — roda dentro do Premiere Pro.
 * Insere B-rolls na faixa V2 com trimming preciso e sem áudio.
 */

var TICKS = 254016000000;

function getActiveSequence() {
    if (!app || !app.project) return null;
    return app.project.activeSequence || null;
}

function getActiveVideoPath() {
    try {
        var seq = getActiveSequence();
        if (!seq) return "";
        var v1 = seq.videoTracks[0];
        if (!v1 || v1.clips.numItems === 0) return "";
        return v1.clips[0].projectItem.getMediaPath();
    } catch (e) { return ""; }
}

/**
 * Lê TODA a composição da sequência ativa: cada clipe de cada faixa de vídeo,
 * com caminho de origem, tempo de sequência (start/end) e in/out de origem.
 * Os clipes da V1 são marcados como narração (base da transcrição).
 */
function getSequenceComposition() {
    try {
        var seq = getActiveSequence();
        if (!seq) return JSON.stringify({ ok: false, error: "Nenhuma sequência ativa." });

        function clipInfo(clip, trackIndex) {
            var path = "";
            try { path = clip.projectItem ? clip.projectItem.getMediaPath() : ""; } catch (e) {}
            function sec(t) { try { return parseFloat(t.ticks) / TICKS; } catch (e) { return 0; } }
            return {
                track:     trackIndex,
                name:      clip.name || "",
                path:      path,
                seq_start: sec(clip.start),
                seq_end:   sec(clip.end),
                in_point:  sec(clip.inPoint),
                out_point: sec(clip.outPoint)
            };
        }

        var videoClips  = [];
        var narration   = [];
        var vtracks = seq.videoTracks;
        for (var t = 0; t < vtracks.numTracks; t++) {
            var track = vtracks[t];
            for (var c = 0; c < track.clips.numItems; c++) {
                var info = clipInfo(track.clips[c], t);
                if (!info.path) continue;            // ignora títulos/gráficos sem mídia
                videoClips.push(info);
                if (t === 0) narration.push(info);   // V1 = narração principal
            }
        }

        // Áudio (faixas) — útil quando a narração está numa faixa de áudio separada
        var audioClips = [];
        try {
            var atracks = seq.audioTracks;
            for (var at = 0; at < atracks.numTracks; at++) {
                var atrack = atracks[at];
                for (var ac = 0; ac < atrack.clips.numItems; ac++) {
                    var ainfo = clipInfo(atrack.clips[ac], at);
                    if (ainfo.path) audioClips.push(ainfo);
                }
            }
        } catch (e) {}

        // Fallback: nenhuma narração na V1 → usa os clipes de áudio
        if (narration.length === 0 && audioClips.length > 0) {
            narration = audioClips;
        }

        return JSON.stringify({
            ok:              true,
            sequence_name:   seq.name || "",
            video_clips:     videoClips,
            audio_clips:     audioClips,
            narration_clips: narration
        });
    } catch (e) {
        return JSON.stringify({ ok: false, error: e.toString() });
    }
}

/**
 * Aplica uma transição (cross dissolve) no início do clip de B-roll na V2.
 * Usa o QE DOM, que não existe em todas as versões — por isso tudo é
 * protegido: se falhar, vira corte seco e a inserção segue normal.
 * Retorna true se aplicou.
 */
function applyDissolve(startSec) {
    try {
        if (typeof app.enableQE !== "function") return false;
        app.enableQE();
        if (typeof qe === "undefined" || !qe.project) return false;
        var qeSeq = qe.project.getActiveSequence();
        if (!qeSeq) return false;
        var qeTrack = qeSeq.getVideoTrackAt(1);   // V2
        if (!qeTrack) return false;
        for (var k = 0; k < qeTrack.numItems; k++) {
            var it = qeTrack.getItemAt(k);
            if (!it || it.type !== "Clip") continue;
            var its = it.start ? it.start.secs : -1;
            if (Math.abs(its - startSec) < 0.2) {
                it.addDefaultTransition();   // cross dissolve padrão
                return true;
            }
        }
    } catch (e) { /* QE indisponível — segue com corte seco */ }
    return false;
}

function insertBRolls(matchesJSON) {
    var matches;
    try { matches = JSON.parse(matchesJSON); }
    catch (e) { return JSON.stringify({ ok: false, error: "JSON inválido" }); }

    var seq = getActiveSequence();
    if (!seq) return JSON.stringify({ ok: false, error: "Nenhuma sequência ativa." });

    // Garante que V2 existe
    while (seq.videoTracks.numTracks < 2) {
        seq.videoTracks.addTrack();
    }
    var vTrack = seq.videoTracks[1];

    var inserted = 0;
    var dissolves = 0;
    var errors = [];

    for (var i = 0; i < matches.length; i++) {
        var m = matches[i];
        if (!m.broll_path) continue;

        try {
            // Importa o clip para o projeto
            var item = findInProject(app.project.rootItem, m.broll_path);
            if (!item) {
                var imported = app.project.importFiles(
                    [m.broll_path], true, app.project.rootItem, false
                );
                if (imported && imported.numItems > 0) {
                    item = imported[0];
                } else if (imported && imported.length > 0) {
                    item = imported[0];
                }
            }
            if (!item) {
                errors.push("Não importou: " + m.broll_filename);
                continue;
            }

            // Posição de início na timeline
            var pos = new Time();
            pos.ticks = String(Math.round(m.start * TICKS));

            // Duração desejada do segmento
            var segDuration = m.end - m.start;

            // Insere na V2 (overwrite não desloca V1)
            vTrack.overwriteClip(item, pos);

            // Encontra o clip recém inserido e trimma para a duração certa
            var found = null;
            for (var c = 0; c < vTrack.clips.numItems; c++) {
                var tc = vTrack.clips[c];
                var tcStart = parseFloat(tc.start.ticks) / TICKS;
                if (Math.abs(tcStart - m.start) < 0.15) {
                    found = tc;
                    break;
                }
            }

            if (found) {
                // Calcula duração real do clip fonte
                var clipDurSec = parseFloat(found.end.ticks) / TICKS
                                 - parseFloat(found.start.ticks) / TICKS;
                var useDur = Math.min(segDuration, clipDurSec);

                // Seta o fim do clip na timeline para trimmar
                var newEnd = new Time();
                newEnd.ticks = String(Math.round((m.start + useDur) * TICKS));
                found.end = newEnd;

                // Desativa o áudio vinculado ao clip de B-roll
                // Remove o áudio da faixa A2 que pode ter sido criado
                if (seq.audioTracks.numTracks >= 2) {
                    var aTrack = seq.audioTracks[1];
                    for (var a = 0; a < aTrack.clips.numItems; a++) {
                        var ac = aTrack.clips[a];
                        var acStart = parseFloat(ac.start.ticks) / TICKS;
                        if (Math.abs(acStart - m.start) < 0.15) {
                            ac.remove(false, false);
                            break;
                        }
                    }
                }
            }

            // Transição sugerida pelo backend (ritmo): dissolve em mecanismo/prova
            if (m.transition === "dissolve") {
                if (applyDissolve(m.start)) dissolves++;
            }

            inserted++;
        } catch (e) {
            errors.push((m.broll_filename || "item " + i) + ": " + e.toString());
        }
    }

    return JSON.stringify({ ok: true, inserted: inserted, dissolves: dissolves, errors: errors });
}

function insertLetteringMarkers(markersJSON) {
    var markers;
    try { markers = JSON.parse(markersJSON); }
    catch (e) { return JSON.stringify({ ok: false, error: "JSON inválido" }); }

    var seq = getActiveSequence();
    if (!seq) return JSON.stringify({ ok: false, error: "Nenhuma sequência ativa." });

    var colors = { stat: 1, cta: 2, benefit: 3, emotion: 5, title: 6 };
    var inserted = 0;

    for (var i = 0; i < markers.length; i++) {
        var m = markers[i];
        try {
            var t = new Time();
            t.ticks = String(Math.round(m.start * TICKS));
            var marker = seq.markers.createMarker(t);
            marker.name = "LETTERING";
            marker.comments = m.text || "";
            // Cor pelo tipo: 1=vermelho, 2=laranja, 3=amarelo, 5=ciano, 6=azul
            var colorCode = colors[m.type] || 3;
            marker.colorByIndex = colorCode;
            inserted++;
        } catch (e) {}
    }

    return JSON.stringify({ ok: true, inserted: inserted });
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
