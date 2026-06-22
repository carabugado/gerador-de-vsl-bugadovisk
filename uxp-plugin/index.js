/**
 * UXP entry point — carregado pelo Premiere Pro automaticamente.
 * Registra o painel e expõe a função de inserção de B-rolls.
 */
const { entrypoints } = require("uxp");
const ppro = require("ppro");

entrypoints.setup({
  panels: {
    "vsl-panel": {
      show() {},
      hide() {}
    }
  }
});

// ─── Funções de timeline expostas ao painel HTML ─────────────────────────────

const TICKS = 254016000000; // ticks por segundo no Premiere

function secondsToTicks(s) {
  return String(Math.round(s * TICKS));
}

/**
 * Detecta o caminho do primeiro clip na faixa V1 da sequência ativa.
 */
async function getActiveVideoPath() {
  try {
    const seq = ppro.project.activeSequence;
    if (!seq) return "";
    const v1 = seq.videoTracks[0];
    if (!v1 || v1.clips.numItems === 0) return "";
    return v1.clips[0].projectItem.getMediaPath();
  } catch (e) {
    return "";
  }
}

/**
 * Insere B-rolls na faixa V2 com trimming preciso ao frame.
 * @param {Array} matches - lista de {broll_path, start, end}
 * @returns {Object} {inserted, errors}
 */
async function insertBRolls(matches) {
  const project = ppro.project;
  const seq = project.activeSequence;

  if (!seq) return { ok: false, error: "Nenhuma sequência ativa." };

  // Garante que a faixa V2 existe
  while (seq.videoTracks.numTracks < 2) {
    seq.videoTracks.addTrack();
  }
  const videoTrack = seq.videoTracks[1];

  // Garante que a faixa A2 existe mas não vamos usar (sem áudio dos B-rolls)
  while (seq.audioTracks.numTracks < 2) {
    seq.audioTracks.addTrack();
  }

  let inserted = 0;
  const errors = [];

  for (let i = 0; i < matches.length; i++) {
    const m = matches[i];
    if (!m.broll_path) continue;

    try {
      // Importa o clip (ignora duplicatas automaticamente)
      const importResult = await project.importFiles(
        [m.broll_path],
        true,             // suppressUI
        project.rootItem, // destino no painel
        false             // importAsNumberedStills
      );

      let projectItem = importResult && importResult[0]
        ? importResult[0]
        : findInProject(project.rootItem, m.broll_path);

      if (!projectItem) {
        errors.push(`Não importou: ${m.broll_filename || m.broll_path}`);
        continue;
      }

      // Calcula duração do segmento e do clip
      const segDuration = m.end - m.start;
      const clipDuration = projectItem.getOutPoint
        ? projectItem.getOutPoint().seconds
        : segDuration;

      const useDuration = Math.min(segDuration, clipDuration);

      // Posição na timeline (onde o B-roll começa)
      const position = new ppro.Time();
      position.ticks = secondsToTicks(m.start);

      // In/Out points do clip de B-roll
      const inPoint = new ppro.Time();
      inPoint.ticks = "0";

      const outPoint = new ppro.Time();
      outPoint.ticks = secondsToTicks(useDuration);

      // Insere na V2 SEM deslocar a V1 (overwrite)
      videoTrack.overwriteClip(projectItem, position, inPoint, outPoint);

      // Desabilita o áudio do clip inserido na V2
      // Percorre os clips na faixa e silencia o que acabou de entrar
      for (let c = 0; c < videoTrack.clips.numItems; c++) {
        const tc = videoTrack.clips[c];
        const tcStartSec = parseFloat(tc.start.ticks) / TICKS;
        if (Math.abs(tcStartSec - m.start) < 0.1) {
          try {
            // Desabilita o canal de áudio do clip
            if (tc.components) {
              for (let k = 0; k < tc.components.numItems; k++) {
                const comp = tc.components[k];
                if (comp.matchName === "AE.ADBE Audio Group") {
                  comp.enabled = false;
                }
              }
            }
          } catch (_) {}
          break;
        }
      }

      inserted++;
    } catch (e) {
      errors.push(`${m.broll_filename || "item " + i}: ${e.message || e}`);
    }
  }

  return { ok: true, inserted, errors };
}

/**
 * Busca um projectItem pelo caminho de mídia, recursivamente.
 */
function findInProject(item, targetPath) {
  if (!item) return null;
  try {
    if (item.type === 1 /* CLIP */) {
      if (item.getMediaPath() === targetPath) return item;
    }
  } catch (_) {}
  try {
    if (item.children) {
      for (let i = 0; i < item.children.numItems; i++) {
        const found = findInProject(item.children[i], targetPath);
        if (found) return found;
      }
    }
  } catch (_) {}
  return null;
}

// Expõe as funções para o painel HTML via window
window.vslPlugin = { getActiveVideoPath, insertBRolls };
