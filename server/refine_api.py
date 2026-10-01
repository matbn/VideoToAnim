"""API da camada de refinamento (filtros, constraints, editor de bone, sessao).

Mantem, por job, um "clipe atual" (editado) em disco, mais o historico de edicoes
persistido — assim fechar e reabrir a sessao nao perde trabalho.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.mixamo import BONE_PARENT, world_rotation
from core.refine import boneedit_mod as be
from core.refine import collision as collision_mod
from core.refine import constraints as cons
from core.refine import filter_plan, io as rio, report as rep_mod, transplant as tr
from core.refine.filters import FILTERS

# Sem import do app no topo: criaria um ciclo (app.py inclui este router).
# JOBS_DIR/STORAGE derivam do mesmo layout; o store e resolvido tardiamente.
ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT / "storage" / "jobs"
STORAGE = ROOT / "storage"


class _StoreLazy:
    """Proxy que resolve o JobStore do app so na primeira chamada."""

    def __getattr__(self, item: str):
        from .app import store as _s

        return getattr(_s, item)


store = _StoreLazy()

router = APIRouter(prefix="/api/refine", tags=["refine"])

REFINE_DIR = STORAGE / "refine"

# cache por processo: a malha do job (vem de um FBX) e' custosa de re-carregar
_MESH_CACHE: dict[str, dict | None] = {}


def _job_skin_mesh(job_id: str):
    """Malha do usuario anexada ao job, se houver.

    Sem isto a previa do editor (e o export refinado) mostrariam os
    "capsule sticks" no lugar do personagem processado.
    """
    if job_id in _MESH_CACHE:
        return _MESH_CACHE[job_id]
    mesh = None
    try:
        d = _job_dir(job_id)
        cand = sorted(d.glob("mesh.*"))
        if cand:
            from core.mesh import check_compatibility, load_mesh

            rep = check_compatibility(cand[0])
            if rep.compatible and rep.attachable:
                mesh = load_mesh(cand[0], rep)
    except Exception:  # noqa: BLE001
        mesh = None
    _MESH_CACHE[job_id] = mesh
    return mesh


_MESH_LEN_CACHE: dict[str, dict | None] = {}


def _job_mesh_lengths(job_id: str) -> dict | None:
    """Comprimentos dos ossos do esqueleto DA MALHA do job (anticolisao).

    O rig do Mixamo tem formato padrao mas cada malha tem tamanhos proprios;
    a anticolisao re-detecta aqui, a cada execucao. None quando nao ha malha
    anexa ou o formato nao permite leitura.
    """
    try:
        cand = sorted(_job_dir(job_id).glob("mesh.*"))
    except Exception:  # noqa: BLE001
        return None
    if not cand:
        return None
    key = f"{job_id}:{cand[0].stat().st_mtime_ns}"  # trocar a malha re-detecta
    if key in _MESH_LEN_CACHE:
        return _MESH_LEN_CACHE[key]
    out = None
    try:
        from core.mesh import mesh_bone_lengths

        out = mesh_bone_lengths(cand[0]) or None
    except Exception:  # noqa: BLE001
        out = None
    _MESH_LEN_CACHE[key] = out
    return out


_MESH_RIG_CACHE: dict[str, dict | None] = {}


def _job_mesh_rig(job_id: str) -> dict | None:
    """Esqueleto de rest DA MALHA do job (pivos proprios p/ exportacao)."""
    try:
        cand = sorted(_job_dir(job_id).glob("mesh.*"))
    except Exception:  # noqa: BLE001
        return None
    if not cand:
        return None
    key = f"{job_id}:{cand[0].stat().st_mtime_ns}"
    if key in _MESH_RIG_CACHE:
        return _MESH_RIG_CACHE[key]
    out = None
    try:
        from core.mesh import mesh_skeleton

        rig = mesh_skeleton(cand[0])
        if rig and rig.get("ok"):
            out = rig
    except Exception:  # noqa: BLE001
        out = None
    _MESH_RIG_CACHE[key] = out
    return out


def _job_mesh_rig_offsets(job_id: str) -> dict | None:
    """Offsets de mundo por osso do esqueleto da malha (anticolisao)."""
    rig = _job_mesh_rig(job_id)
    if not rig:
        return None
    return {b: np.asarray(v["world_offset"], np.float64)
            for b, v in rig["bones"].items()}


CONSTRAINTS_PATH = Path(__file__).resolve().parents[1] / "config" / "constraints_humanoid.yaml"
FILTERS_PATH = Path(__file__).resolve().parents[1] / "config" / "filters_default.yaml"
COLLISION_PATH = Path(__file__).resolve().parents[1] / "config" / "collision_default.yaml"


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------
def _job_dir(job_id: str) -> Path:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job nao encontrado")
    d = JOBS_DIR / job_id
    if not d.exists():
        raise HTTPException(404, "diretorio do job nao existe")
    return d


def _source_anim_path(job_id: str) -> Path:
    p = _job_dir(job_id) / "anim.json"
    if not p.exists():
        raise HTTPException(409, "este job nao tem anim.json (rode o pipeline de novo: "
                                 "a animacao bakeada passou a ser salva junto)")
    return p


def _session_paths(job_id: str) -> tuple[Path, Path]:
    d = REFINE_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "current.json", d / "session.json"


def _load_current(job_id: str):
    cur, sess = _session_paths(job_id)
    if cur.exists():
        return rio.load_animation(cur), be.EditHistory.load(sess) if sess.exists() else be.EditHistory(source=job_id)
    src = _source_anim_path(job_id)
    anim = rio.load_animation(src)
    hist = be.EditHistory(source=str(src))
    rio.save_animation(anim, cur)
    hist.save(sess)
    return anim, hist


def _save_current(job_id: str, anim, hist: be.EditHistory) -> None:
    cur, sess = _session_paths(job_id)
    rio.save_animation(anim, cur)
    hist.save(sess)


# ---------------------------------------------------------------------------
# filtros / constraints
# ---------------------------------------------------------------------------
@router.get("/filters")
def list_filters() -> dict:
    return {"filters": [
        {"name": s.name, "params": s.params, "description": s.description,
         "description_en": s.description_en}
        for s in FILTERS.values()
    ]}


@router.get("/plan")
def get_plan() -> dict:
    if FILTERS_PATH.exists():
        return filter_plan.FilterPlan.load(FILTERS_PATH).to_dict()
    return filter_plan.FilterPlan().to_dict()


@router.get("/constraints")
def get_constraints() -> dict:
    preset = cons.load_default_preset(CONSTRAINTS_PATH)
    return {"path": str(CONSTRAINTS_PATH), "preset": preset.to_dict()}


@router.get("/collision")
def get_collision() -> dict:
    """Configuracao padrao da anticolisao (pares, raios, limites)."""
    cfg = collision_mod.load_default_config(COLLISION_PATH)
    return {"path": str(COLLISION_PATH), "config": cfg.to_dict()}


class PresetBody(BaseModel):
    preset: dict


@router.put("/constraints")
def put_constraints(body: PresetBody) -> dict:
    try:
        preset = cons.ConstraintPreset.from_dict(body.preset)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"preset malformado: {exc}") from exc
    errs = preset.validate()
    if errs:
        raise HTTPException(400, "preset invalido: " + "; ".join(errs))
    preset.save(CONSTRAINTS_PATH, header="# editado pela interface\n")
    return {"saved": str(CONSTRAINTS_PATH), "limits": len(preset.limits)}


# ---------------------------------------------------------------------------
# animacao (para o editor)
# ---------------------------------------------------------------------------
@router.get("/animation/{job_id}")
def get_animation(job_id: str) -> dict:
    anim, hist = _load_current(job_id)
    return {
        "job_id": job_id,
        "fps": anim.fps,
        "num_frames": anim.num_frames,
        "animated_bones": [b for b in anim.rotations.keys()],
        # o editor precisa da arvore paraconverter euler global -> quaternion
        # local na previa ao vivo (so os ossos, nunca o no da cena)
        "bone_parents": {b: p for b, p in BONE_PARENT.items()},
        "root_translation": np.asarray(anim.root_translation).round(5).tolist(),
        "history": hist.to_dict(),
    }


@router.get("/animation/{job_id}/frame/{t}")
def get_frame(job_id: str, t: int, space: str = "local") -> dict:
    """Pose do frame. `space` escolhe o referencial dos angulos X/Y/Z.

    `local` (padrao) = rotacao do osso em relacao ao pai (eixos acompanham a
    hierarquia). `global` = orientacao absoluta na cena (eixos do mundo).
    O quaternion em `quat` e sempre o local, que e o que a Animation guarda.
    """
    anim, _ = _load_current(job_id)
    if not (0 <= t < anim.num_frames):
        raise HTTPException(400, f"frame fora do clipe (0..{anim.num_frames - 1})")
    if space not in be.EDIT_SPACES:
        raise HTTPException(400, f"space invalido: {space!r} "
                                 f"(use {' ou '.join(be.EDIT_SPACES)})")
    frame_rots = {k: np.asarray(s[t], np.float64) for k, s in anim.rotations.items()}
    out = {}
    for bone, series in anim.rotations.items():
        q_ref = frame_rots[bone] if space == "local" else world_rotation(frame_rots, bone)
        out[bone] = {
            "quat": np.asarray(series[t]).round(6).tolist(),
            "euler_deg": np.asarray(cons.quat_to_euler_xyz_deg(q_ref)).round(3).tolist(),
        }
    return {"frame": t, "space": space, "bones": out,
            "root_translation": np.asarray(anim.root_translation[t]).round(5).tolist()}


class EditBody(BaseModel):
    bone: str
    frame: int
    rotation_euler_deg: list[float] | None = None
    rotation: list[float] | None = None
    space: str = "local"
    translation: list[float] | None = None
    start: int | None = None
    end: int | None = None
    author: str = "user"
    note: str = ""


@router.post("/animation/{job_id}/edit")
def post_edit(job_id: str, body: EditBody) -> dict:
    anim, hist = _load_current(job_id)
    edit = be.BoneEdit(bone=body.bone, frame=body.frame,
                       rotation=body.rotation, rotation_euler_deg=body.rotation_euler_deg,
                       space=body.space,
                       translation=body.translation, start=body.start, end=body.end,
                       author=body.author, note=body.note)
    try:
        new_anim, rep = be.apply_edit(anim, edit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    hist.add(edit)
    _save_current(job_id, new_anim, hist)
    return {"report": rep, "history_len": len(hist.edits)}


@router.post("/animation/{job_id}/undo")
def post_undo(job_id: str) -> dict:
    anim, hist = _load_current(job_id)
    try:
        info = hist.undo()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    new_anim, _ = hist.replay(rio.load_animation(_source_anim_path(job_id)))
    _save_current(job_id, new_anim, hist)
    return {**info, "history_len": len(hist.edits)}


@router.post("/animation/{job_id}/redo")
def post_redo(job_id: str) -> dict:
    anim, hist = _load_current(job_id)
    try:
        info = hist.redo()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    new_anim, _ = hist.replay(rio.load_animation(_source_anim_path(job_id)))
    _save_current(job_id, new_anim, hist)
    return {**info, "history_len": len(hist.edits)}


@router.post("/animation/{job_id}/reset")
def post_reset(job_id: str) -> dict:
    src = _source_anim_path(job_id)
    anim = rio.load_animation(src)
    hist = be.EditHistory(source=str(src))
    _save_current(job_id, anim, hist)
    return {"reset": True, "history_len": 0}


# ---------------------------------------------------------------------------
# comparativo de filtros + refino final
# ---------------------------------------------------------------------------
class CompareBody(BaseModel):
    filters: list[str] | None = None
    params: dict | None = None


@router.post("/compare/{job_id}")
def post_compare(job_id: str, body: CompareBody | None = None) -> dict:
    anim, _ = _load_current(job_id)
    names = (body.filters if body and body.filters else list(FILTERS.keys()))
    desconhecidos = [n for n in names if n not in FILTERS]
    if desconhecidos:
        raise HTTPException(400, f"filtros desconhecidos: {desconhecidos}")
    comp = rep_mod.compare_filters(anim, names, params=(body.params if body else None))
    return {"rows": [c.as_row() for c in comp],
            "markdown": rep_mod.to_markdown(comp, "Comparativo de estabilizacao")}


class ApplyBody(BaseModel):
    use_filters: bool = True
    use_constraints: bool = True
    use_collision: bool = True
    filters: list[str] | None = None      # quais filtros usar (default: o plano do arquivo)


@router.get("/transplant/parts")
def get_transplant_parts() -> dict:
    """Partes do corpo que o usuario pode escolher de cada job."""
    return {
        "parts": [{"id": pid, "label": spec["label"], "bones": tr.part_bones(pid)}
                  for pid, spec in tr.BODY_PARTS.items()],
        "order": list(tr.PART_ORDER),
    }


class TransplantBody(BaseModel):
    source_job: str
    parts: list[str]
    copy_root_translation: bool | None = None
    # mapeamento de intervalo: frames da ORIGEM -> frames do DESTINO (inclusive)
    source_range: list[int] | None = None
    target_range: list[int] | None = None


def _transplant_paths(job_id: str) -> tuple[Path, Path]:
    """Sessao do transplante: onde ficam o clipe atual e o historico.

    (Nome proprio: `_session_paths` ja existe e devolve so 2 caminhos.)
    """
    d = REFINE_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "current.json", d / "history.json"


@router.post("/transplant/{job_id}")
def post_transplant(job_id: str, body: TransplantBody) -> dict:
    """Copia as partes escolhidas do job de origem para a sessao deste job.

    O clipe resultante passa a ser o "atual" do job alvo (o que o editor e os
    downloads ja leem), e o clipe anterior fica guardado para desfazer.
    """
    if body.source_job == job_id:
        raise HTTPException(400, "origem e destino sao o mesmo job")
    desconhecidas = [p for p in body.parts if p not in tr.BODY_PARTS]
    if desconhecidas:
        raise HTTPException(400, f"partes desconhecidas: {desconhecidas} "
                                 f"(validas: {list(tr.BODY_PARTS)})")
    if not body.parts:
        raise HTTPException(400, "nenhuma parte selecionada")
    try:
        src = store.get(body.source_job)
    except Exception:  # noqa: BLE001
        src = None
    if not src or src.get("status") != "done":
        raise HTTPException(404, f"job de origem nao encontrado ou nao concluido: {body.source_job}")
    try:
        antes, _ = _load_current(job_id)
        source, _ = _load_current(body.source_job)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"clipe ausente: {exc}") from exc

    frame_map = None
    if body.source_range or body.target_range:
        if not (body.source_range and body.target_range):
            raise HTTPException(400, "informe os DOIS intervalos (origem e destino), "
                                     "ou nenhum para usar o clipe inteiro")
        if len(body.source_range) != 2 or len(body.target_range) != 2:
            raise HTTPException(400, "cada intervalo tem 2 valores: [inicio, fim] (inclusivo)")
        frame_map = {"source": body.source_range, "target": body.target_range}

    try:
        novo, report = tr.transplant(antes, source, body.parts,
                                     copy_root_translation=body.copy_root_translation,
                                     frame_map=frame_map)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    report["source_job"] = body.source_job
    report["target_job"] = job_id
    report["vs_source"] = tr.difference_report(antes, novo, source, frame_map)

    current, hist_path = _transplant_paths(job_id)
    hist = be.EditHistory.load(hist_path) if hist_path.exists() else be.EditHistory()
    hist.snapshots.append({"clip": rio.animation_to_dict(antes),
                           "parts": list(body.parts), "source_job": body.source_job})
    hist.updated_at = time.time()

    rio.save_animation(novo, current)
    rio.export_animation_glb(novo, REFINE_DIR / job_id / "current.glb",
                             skin_mesh=_job_skin_mesh(job_id),
                             rig=_job_mesh_rig(job_id))
    rio.export_animation_fbx(novo, REFINE_DIR / job_id / "current.fbx")
    rio.save_animation(novo, _job_dir(job_id) / "anim_refined.json")
    hist.save(hist_path)
    for key, name in (("glb_refined", "current.glb"), ("fbx_refined", "current.fbx")):
        p = REFINE_DIR / job_id / name
        store.set_artifact(job_id, key, str(p), p.stat().st_size)
    return {"report": report, "history_len": len(hist.edits),
            "snapshots": len(hist.snapshots)}


@router.post("/transplant/{job_id}/undo")
def post_transplant_undo(job_id: str) -> dict:
    """Desfaz o ultimo transplante, voltando ao clipe anterior."""
    _, hist_path = _transplant_paths(job_id)
    if not hist_path.exists():
        raise HTTPException(404, "sem historico para este job")
    hist = be.EditHistory.load(hist_path)
    if not hist.snapshots:
        raise HTTPException(400, "nenhum transplante para desfazer")
    snap = hist.snapshots.pop()
    antes = rio.animation_from_dict(snap["clip"])
    current, _ = _transplant_paths(job_id)
    rio.save_animation(antes, current)
    rio.export_animation_glb(antes, REFINE_DIR / job_id / "current.glb",
                             skin_mesh=_job_skin_mesh(job_id),
                             rig=_job_mesh_rig(job_id))
    rio.export_animation_fbx(antes, REFINE_DIR / job_id / "current.fbx")
    rio.save_animation(antes, _job_dir(job_id) / "anim_refined.json")
    hist.save(hist_path)
    return {"undone": snap.get("parts", []), "source_job": snap.get("source_job"),
            "snapshots": len(hist.snapshots)}


@router.post("/animation/{job_id}/apply")
def post_apply(job_id: str, body: ApplyBody | None = None) -> dict:
    from core.refine import refine_animation

    body = body or ApplyBody()
    anim, _ = _load_current(job_id)
    fcfg = None
    if body.use_filters:
        if body.filters:
            fcfg = filter_plan.FilterPlan(rules=[
                filter_plan.FilterRule(name=n, bones=None) for n in body.filters])
        elif FILTERS_PATH.exists():
            fcfg = filter_plan.FilterPlan.load(FILTERS_PATH)
    ccfg = CONSTRAINTS_PATH if body.use_constraints else None
    colcfg = COLLISION_PATH if body.use_collision else None
    colmesh = _job_mesh_lengths(job_id) if body.use_collision else None
    coloffs = _job_mesh_rig_offsets(job_id) if body.use_collision else None
    refined, rep = refine_animation(anim, constraints_config=ccfg, filters_config=fcfg,
                                    collision_config=colcfg, collision_mesh_lengths=colmesh,
                                    collision_skeleton_offsets=coloffs)

    job_dir = _job_dir(job_id)
    glb = rio.export_animation_glb(refined, job_dir / "model_refined.glb",
                                   skin_mesh=_job_skin_mesh(job_id),
                                   rig=_job_mesh_rig(job_id))
    fbx = rio.export_animation_fbx(refined, job_dir / "model_refined.fbx")
    rio.save_animation(refined, job_dir / "anim_refined.json")
    payload = rep_mod.save_report(STORAGE / "refine" / job_id, constraints=rep.constraints,
                                  extra={"filters": rep.filters, "collision": rep.collision,
                                         "stages": rep.stages, "comparison": None})
    store.set_artifact(job_id, "glb_refined", str(glb["path"]), glb["bytes"])
    store.set_artifact(job_id, "fbx_refined", str(fbx["path"]), fbx["bytes"])
    return {"glb": glb, "fbx": fbx, "stages": rep.stages,
            "constraints": rep.constraints, "collision": rep.collision,
            "filters": rep.filters, "report": payload}

@router.get("/animation/{job_id}/glb")
def get_current_glb(job_id: str):
    """Exporta o clipe ATUAL (com as edicoes) em GLB, para a previa do editor."""
    from fastapi.responses import FileResponse

    anim, _ = _load_current(job_id)
    out = REFINE_DIR / job_id / "current.glb"
    out.parent.mkdir(parents=True, exist_ok=True)
    rio.export_animation_glb(anim, out, skin_mesh=_job_skin_mesh(job_id),
                             rig=_job_mesh_rig(job_id))
    return FileResponse(out, media_type="model/gltf-binary", filename=f"{job_id}_edited.glb")
