"""Pipeline de orquestracao: video -> keypoints -> retarget -> export.

Fluxo:
  1. upload/pre-processamento (leitura de frames)
  2. backend de pose (via Adapter) -> FramePose canonico
  3. pos-processamento/estabilizacao (suavizacao + lifter 2D->3D se preciso)
  4. solver de retarget -> esqueleto Mixamo (Animation)
  5. exportacao GLB + FBX
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .canonical import FramePose
from .export_fbx import build_fbx
from .export_glb import build_glb
from .lifter import AnalyticLifter
from .registry import BackendRegistry
from .retarget import Retargeter, calibrate_head, fk_validation_error
from .smoothing import smooth_frames


def run_pipeline(
    *,
    job_id: str,
    video_path: str | Path,
    backend_name: str,
    params: dict,
    registry: BackendRegistry,
    job_dir: str | Path,
    max_frames: int | None = None,
    mesh_path: str | Path | None = None,
    logger=None,
) -> dict:
    def log(msg: str) -> None:
        if logger:
            logger(msg)

    t0 = time.time()
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    rec = registry.get(backend_name)
    if rec is None:
        raise ValueError(f"backend desconhecido: {backend_name}")
    if not rec.available:
        raise RuntimeError(f"backend '{backend_name}' indisponivel: {rec.reason}")

    log(f"Lendo video {Path(video_path).name}")
    from .video import read_video

    info = read_video(str(video_path), max_frames=max_frames)
    log(f"Video: {info.count} frames, {info.width}x{info.height} @ {info.fps:.1f} fps")

    backend = rec.backend
    backend.load(params)
    ctx = {"width": info.width, "height": info.height, "fps": info.fps, "job_id": job_id}
    log(f"Rodando backend '{backend_name}'")
    frames = backend.infer_video(info.frames, ctx)
    fb = getattr(backend, "_fallback_reason", "")
    if fb:
        log(f"Aviso: {fb}")
    if not isinstance(frames, list) or not frames:
        raise RuntimeError("backend nao retornou keypoints")
    log(f"Backend '{backend_name}' retornou {len(frames)} frames de pose")

    # 3) suavizacao + 3D
    smoothing = params.get("smoothing", "oneeuro")
    frames = smooth_frames(frames, method=smoothing)
    has_3d = any(f.kp3d is not None for f in frames)
    if not has_3d and params.get("lift_2d_to_3d", True):
        log("Aplicando lifter 2D->3D (analitico)")
        frames = AnalyticLifter().lift(frames)
        has_3d = any(f.kp3d is not None for f in frames)
    if not has_3d:
        raise RuntimeError("nenhum keypoint 3D disponivel apos o lifting")

    # 4) retarget
    log("Retarget para esqueleto Mixamo")
    target_fps = float(params.get("fps", info.fps or 30.0))
    anim = Retargeter(fps=target_fps).retarget(frames)
    fk = fk_validation_error(anim, frames)
    log(f"FK reverso: erro max por junta {fk['max_position_m'] * 100:.2f} cm, "
        f"angular max {fk['max_angle_deg']:.2f} deg")

    # 4b) calibracao da cabeca: ancora Neck/Head no frame inicial (o vetor
    # nose-chest estimado carrega um vies constante de dezenas de graus)
    if params.get("head_calibration", True):
        hc = calibrate_head(anim, frame=int(params.get("head_calibration_frame", 0)))
        if hc["applied"]:
            detalhes = ", ".join(f"{k} {v['angle_deg']:.1f} deg" for k, v in hc["bones"].items())
            log(f"Cabeca calibrada pelo frame {hc['frame']}: {detalhes}")
        anim.meta["head_calibration"] = hc

    # 5) malha do usuario (opcional) + checagem de compatibilidade do esqueleto
    mesh_report = None
    skin_mesh = None
    if mesh_path:
        from .mesh import check_compatibility, load_mesh

        report = check_compatibility(mesh_path)
        mesh_report = report.as_dict()
        log(f"Malha: formato={report.format}, ossos casados={len(report.matched)}/65, "
            f"compativel={report.compatible}, anexavel={report.attachable}")
        for msg in report.messages:
            log(f"Malha: {msg}")
        if report.compatible and report.attachable:
            try:
                skin_mesh = load_mesh(mesh_path, report)
                extra = skin_mesh.get("stats") or {}
                log(f"Malha do usuario anexada ({skin_mesh['positions'].shape[0]} vertices"
                    + (f", {extra.get('meshes')} sub-malhas, {extra.get('unweighted', 0)} vertices sem peso" if extra else "")
                    + ")")
                if extra:
                    mesh_report["stats"] = extra
            except Exception as exc:  # noqa: BLE001
                mesh_report["attachable"] = False
                mesh_report["messages"].append(f"falha ao anexar a malha: {exc}")
                log(f"Malha: falha ao anexar ({exc}); usando capsule sticks")

    # 4b) camada de refinamento (opcional): constraints -> filtros -> edicoes
    refine_spec = params.get("refine") if isinstance(params, dict) else None
    if refine_spec:
        from .refine import refine_animation
        from .refine.constraints import default_constraints_path

        # a interface pode mandar atalhos: constraints: true / filters: true
        # (usam os arquivos padrao do projeto)
        c_cfg = refine_spec.get("constraints")
        if c_cfg is True:
            c_cfg = default_constraints_path()
        f_cfg = refine_spec.get("filters")
        if f_cfg is True:
            f_cfg = Path(__file__).resolve().parents[1] / "config" / "filters_default.yaml"

        anim, refine_report = refine_animation(
            anim,
            constraints_config=c_cfg,
            filters_config=f_cfg,
            edits=refine_spec.get("edits"),
        )
        _filtros = ", ".join((refine_report.filters or {}).get("filters", []) or [])
        log(f"Refino aplicado: {' -> '.join(refine_report.stages)}"
            + (f" (filtros: {_filtros})" if _filtros else ""))
        if refine_report.constraints:
            log(f"  constraints: {refine_report.constraints['frames_corrected_total']} frames corrigidos")

    # 4d) hand tracking (opcional): anima os 40 ossos de dedo
    hands_spec = params.get("hands") if isinstance(params, dict) else None
    if isinstance(hands_spec, dict) and hands_spec.get("enabled"):
        try:
            from .hands import HandTracker, apply_hands
            from .provision import run_plan as _prov

            _prov("hands", log=log)
            log("Hand tracking: detectando maos nos frames")
            tracker = HandTracker(
                model_path=hands_spec.get("model_path") or None,
                num_hands=int(hands_spec.get("num_hands", 2)),
                min_conf=float(hands_spec.get("min_conf", 0.3)))
            hfs = tracker.detect(info.frames, float(info.fps or 30.0),
                                 upscale=float(hands_spec.get("upscale", 1.0)))
            com_maos = sum(1 for h in hfs if h.left is not None or h.right is not None)
            anim, hands_rep = apply_hands(anim, hfs,
                                          mirror=bool(hands_spec.get("mirror", True)))
            log(f"Hand tracking: maos em {com_maos}/{len(hfs)} frames, "
                f"{hands_rep['rotations_applied']} rotacoes de dedo aplicadas")
        except Exception as exc:  # noqa: BLE001
            log(f"Hand tracking: falhou ({exc}) — dedos permanecem em identidade")

    # 5) animacao: persiste o clipe bakeado (a camada de refinamento e o editor
    # de bone trabalham sobre ele, sem depender de re-rodar o video)
    try:
        from .refine.io import save_animation

        save_animation(anim, job_dir / "anim.json")
        log(f"Animacao salva em anim.json ({anim.num_frames} frames)")
    except Exception as exc:  # noqa: BLE001
        log(f"aviso: nao foi possivel salvar anim.json ({exc})")

    # 6) exportacao
    glb_info = build_glb(anim, job_dir / "model.glb", skin_mesh=skin_mesh)
    log(f"GLB gerado: {glb_info['bytes']} bytes, {glb_info['bones']} ossos, "
        f"{glb_info['frames']} frames, malha={glb_info['mesh']}")
    fbx_info = build_fbx(anim, job_dir / "model.fbx")
    log(f"FBX gerado: {fbx_info['bytes']} bytes, {fbx_info['curves']} curvas")

    mean_scores = [f.mean_score for f in frames]
    video_fps = float(info.fps or 30.0)
    metrics = {
        "frames": len(frames),
        "fps": target_fps,
        "video_fps": round(video_fps, 3),        # trecho do video que foi de fato processado (segundos) — o frontend usa
        # isto para sincronizar video e esqueleto e para fazer o loop no ponto certo
        "video_span_s": round(len(frames) / max(video_fps, 1e-6), 3),
        "mean_score": float(np.mean(mean_scores)) if mean_scores else 0.0,
        # qual malha foi para o output ("user_mesh" | "stick_capsules") — o frontend
        # usa isso para decidir se mostra os dois paineis 3D (malha + esqueleto) ou
        # o esqueleto duas vezes
        "mesh": glb_info["mesh"],
        "fk_max_position_m": fk["max_position_m"],
        "fk_max_angle_deg": fk["max_angle_deg"],
        "elapsed_s": round(time.time() - t0, 3),
        "source": "backend+3d" if any(f.kp3d is not None for f in frames[:1]) else "lifted",
    }
    return {
        "glb": glb_info,
        "fbx": fbx_info,
        "metrics": metrics,
        "fk": fk,
        "mesh_report": mesh_report,
    }
