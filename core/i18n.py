"""Internacionalizacao do texto que o SERVIDOR gera (log dos jobs).

O idioma da interface (pt/en) chega junto com o job: o formulario de upload
envia `lang` (ver `web/app.js` -> `getLang()`), que fica gravado no job e e
usado para renderizar TODAS as mensagens do log de saida nas duas linguas.

Uso:
    from core import i18n
    store.append_log(job_id, i18n.fmt("job.started", "en", backend="vitpose"))

Regras:
  * cada mensagem tem template PT e EN com os MESMOS placeholders;
  * textos dinamicos (excecoes, nomes de arquivo, numeros) entram por parametro;
  * mensagens aninhadas (ex.: compatibilidade da malha) tem chave na origem.
"""
from __future__ import annotations

LANGS = ("pt", "en")
DEFAULT_LANG = "pt"


def norm_lang(value: str | None) -> str:
    """Normaliza o idioma ('en-US' -> 'en', 'pt-BR' -> 'pt', desconhecido -> pt)."""
    v = (value or "").strip().lower()
    if v.startswith("en"):
        return "en"
    if v.startswith("pt"):
        return "pt"
    return DEFAULT_LANG


MESSAGES: dict[str, dict[str, str]] = {
    # ---- servidor / ciclo do job -------------------------------------------
    "job.started": {
        "pt": "Job iniciado (backend={backend})",
        "en": "Job started (backend={backend})",
    },
    "job.done": {
        "pt": "Job concluido",
        "en": "Job finished",
    },
    "job.aborted": {
        "pt": "interrompido: o servidor foi encerrado",
        "en": "aborted: the server was shut down",
    },
    "job.error": {
        "pt": "ERRO: {error}",
        "en": "ERROR: {error}",
    },
    "job.upload_received": {
        "pt": "Upload recebido: {name} ({bytes} bytes)",
        "en": "Upload received: {name} ({bytes} bytes)",
    },
    "job.mesh_received": {
        "pt": "Malha recebida: {name} ({bytes} bytes)",
        "en": "Mesh received: {name} ({bytes} bytes)",
    },
    # ---- inspecao de malha ---------------------------------------------------
    "inspect.empty": {
        "pt": "arquivo vazio",
        "en": "empty file",
    },
    "inspect.fail": {
        "pt": "falha ao inspecionar: {error}",
        "en": "inspection failed: {error}",
    },
    "inspect.incompatible": {
        "pt": "esqueleto incompativel \u2014 a malha nao sera usada",
        "en": "incompatible skeleton \u2014 the mesh will not be used",
    },
    # ---- pipeline -------------------------------------------------------------
    "pipe.video_read": {
        "pt": "Lendo video {name}",
        "en": "Reading video {name}",
    },
    "pipe.video_info": {
        "pt": "Video: {count} frames, {width}x{height} @ {fps} fps",
        "en": "Video: {count} frames, {width}x{height} @ {fps} fps",
    },
    "pipe.backend_run": {
        "pt": "Rodando backend '{backend}'",
        "en": "Running backend '{backend}'",
    },
    "pipe.backend_warning": {
        "pt": "Aviso: {message}",
        "en": "Warning: {message}",
    },
    "pipe.backend_frames": {
        "pt": "Backend '{backend}' retornou {n} frames de pose",
        "en": "Backend '{backend}' returned {n} pose frames",
    },
    "pipe.kp2d_save_fail": {
        "pt": "aviso: nao foi possivel salvar kp2d.json ({error})",
        "en": "warning: could not save kp2d.json ({error})",
    },
    "pipe.lifter": {
        "pt": "Aplicando lifter 2D->3D (analitico)",
        "en": "Applying 2D->3D lifter (analytic)",
    },
    "pipe.retarget": {
        "pt": "Retarget para esqueleto Mixamo",
        "en": "Retargeting to Mixamo skeleton",
    },
    "pipe.fk_error": {
        "pt": "FK reverso: erro max por junta {cm} cm, angular max {deg} deg",
        "en": "Reverse FK: max per-joint error {cm} cm, max angular {deg} deg",
    },
    "pipe.head_calibrated": {
        "pt": "Cabeca calibrada pelo frame {frame}: {details}",
        "en": "Head calibrated on frame {frame}: {details}",
    },
    "pipe.mesh_compat": {
        "pt": "Malha: formato={format}, ossos casados={matched}/65, compativel={compatible}, anexavel={attachable}",
        "en": "Mesh: format={format}, bones matched={matched}/65, compatible={compatible}, attachable={attachable}",
    },
    "pipe.mesh_note": {
        "pt": "Malha: {message}",
        "en": "Mesh: {message}",
    },
    "pipe.mesh_attached": {
        "pt": "Malha do usuario anexada ({vertices} vertices{details})",
        "en": "User mesh attached ({vertices} vertices{details})",
    },
    "pipe.mesh_attached_extra": {
        "pt": ", {meshes} sub-malhas, {unweighted} vertices sem peso",
        "en": ", {meshes} sub-meshes, {unweighted} unweighted vertices",
    },
    "pipe.mesh_attach_fail": {
        "pt": "Malha: falha ao anexar ({error}); usando capsule sticks",
        "en": "Mesh: attach failed ({error}); using capsule sticks",
    },
    "pipe.mesh_attach_fail_msg": {
        "pt": "falha ao anexar a malha: {error}",
        "en": "failed to attach mesh: {error}",
    },
    "pipe.mesh_rig_fail": {
        "pt": "aviso: esqueleto da malha nao pode ser lido ({error})",
        "en": "warning: could not read the mesh skeleton ({error})",
    },
    "pipe.mesh_rig_ok": {
        "pt": "Esqueleto da malha lido: {bones} ossos, pivos proprios (fonte: {source})",
        "en": "Mesh skeleton read: {bones} bones, own pivots (source: {source})",
    },
    "pipe.mesh_rig_missing": {
        "pt": "aviso: esqueleto da malha sem dados de bind; usando o rig de referencia",
        "en": "warning: mesh skeleton has no bind data; using the reference rig",
    },
    "pipe.refine_applied": {
        "pt": "Refino aplicado: {stages}{filters}",
        "en": "Refinement applied: {stages}{filters}",
    },
    "pipe.refine_filters": {
        "pt": " (filtros: {filters})",
        "en": " (filters: {filters})",
    },
    "pipe.refine_constraints": {
        "pt": "  constraints: {n} frames corrigidos",
        "en": "  constraints: {n} frames corrected",
    },
    "pipe.refine_collision": {
        "pt": "  anticolisao: {n} frames com correcao ({corrections} ajustes, max {max_deg} graus, esqueleto: {source})",
        "en": "  collision: {n} frames corrected ({corrections} adjustments, max {max_deg} deg, skeleton: {source})",
    },
    "pipe.collision_sizes_fail": {
        "pt": "aviso: nao consegui medir os ossos da malha para a anticolisao ({error})",
        "en": "warning: could not measure mesh bone sizes for collision ({error})",
    },
    "pipe.hands_start": {
        "pt": "Hand tracking: detectando maos nos frames",
        "en": "Hand tracking: detecting hands across frames",
    },
    "pipe.hands_done": {
        "pt": "Hand tracking: maos em {frames}/{total} frames, {rotations} rotacoes de dedo aplicadas",
        "en": "Hand tracking: hands in {frames}/{total} frames, {rotations} finger rotations applied",
    },
    "pipe.hands_fail": {
        "pt": "Hand tracking: falhou ({error}) \u2014 dedos permanecem em identidade",
        "en": "Hand tracking: failed ({error}) \u2014 fingers remain at identity",
    },
    "pipe.hands_save_fail": {
        "pt": "Hand tracking: falha ao salvar o debug das maos ({error})",
        "en": "Hand tracking: failed to save the hands debug ({error})",
    },
    "pipe.anim_saved": {
        "pt": "Animacao salva em anim.json ({n} frames)",
        "en": "Animation saved to anim.json ({n} frames)",
    },
    "pipe.anim_save_fail": {
        "pt": "aviso: nao foi possivel salvar anim.json ({error})",
        "en": "warning: could not save anim.json ({error})",
    },
    "pipe.glb": {
        "pt": "GLB gerado: {bytes} bytes, {bones} ossos, {frames} frames, malha={mesh}, pivos={rig}",
        "en": "GLB generated: {bytes} bytes, {bones} bones, {frames} frames, mesh={mesh}, pivots={rig}",
    },
    "pipe.fbx": {
        "pt": "FBX gerado: {bytes} bytes, {curves} curvas",
        "en": "FBX generated: {bytes} bytes, {curves} curves",
    },
    # ---- compatibilidade da malha (core/mesh.py) ------------------------------
    "mesh.fmt_unknown": {
        "pt": "formato nao reconhecido: use GLB/glTF ou FBX do Mixamo.",
        "en": "unrecognized format: use GLB/glTF or a Mixamo FBX.",
    },
    "mesh.attach_format": {
        "pt": "formato de malha nao suportado para anexo (use FBX ou GLB).",
        "en": "mesh format not supported for attachment (use FBX or GLB).",
    },
    "mesh.incompatible": {
        "pt": "esqueleto incompativel: {n} ossos exigidos ausentes (ex.: {list}).",
        "en": "incompatible skeleton: {n} required bones missing (e.g.: {list}).",
    },
    "mesh.extra": {
        "pt": "{n} ossos fora do padrao Mixamo (ignorados).",
        "en": "{n} bones outside the Mixamo standard (ignored).",
    },
}


def fmt(key: str, lang: str | None = None, **params) -> str:
    """Renderiza a mensagem `key` no idioma `lang` com os parametros dados."""
    tpls = MESSAGES.get(key)
    if tpls is None:
        return f"{key} {params}" if params else key
    tpl = tpls.get(norm_lang(lang)) or tpls.get(DEFAULT_LANG) or ""
    try:
        return tpl.format(**params)
    except (KeyError, IndexError):
        return tpl
