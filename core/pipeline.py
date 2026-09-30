"""Pipeline de orquestracao: video -> keypoints -> retarget -> export.

Fluxo:
  1. upload/pre-processamento (leitura de frames)
  2. backend de pose (via Adapter) -> FramePose canonico
  3. pos-processamento/estabilizacao (suavizacao + lifter 2D->3D se preciso)
  4. solver de retarget -> esqueleto Mixamo (Animation)
  5. exportacao GLB + FBX
"""
from __future__ import annotations

import json

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




def _arm_side_history(frames) -> dict[str, float]:
    """Lado (frente=+1 / atras=-1) de cada braco por EVIDENCIA do video.

    Compara o z do PUNHO com o z do OMBRO no kp3d canonico (COCO): na media dos
    frames em que o braco esta visivel, o sinal diz de que lado do tronco o
    braco vive. Frames de braço cruzando o plano chegam com sinal oposto em
    alguns frames, mas a MEDiana do clip e estavel — e o video e a verdade.
    """
    from .canonical import COCO_INDEX
    import numpy as np

    ci = COCO_INDEX
    zs = {"LeftArm": [], "RightArm": []}
    for f in frames:
        k = getattr(f, "kp3d", None)
        if k is None:
            continue
        k = np.asarray(k, np.float64)
        for side, wrist, sh in (("LeftArm", "left_wrist", "left_shoulder"),
                                ("RightArm", "right_wrist", "right_shoulder")):
            z_w = float(k[ci[wrist]][2]); z_s = float(k[ci[sh]][2])
            if abs(z_w - z_s) > 0.02:      # so frames com sinal legivel
                zs[side].append(z_w - z_s)
    out: dict[str, float] = {}
    for side, vals in zs.items():
        if vals:
            out[side] = 1.0 if float(np.median(vals)) >= 0.0 else -1.0
    return out or None


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
    lang: str | None = None,
    logger=None,
) -> dict:
    def log(msg: str) -> None:
        if logger:
            logger(msg)

    t0 = time.time()
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    from . import i18n as _i18n

    _L = _i18n.norm_lang(lang)

    def _logi(key: str, **params) -> None:
        log(_i18n.fmt(key, _L, **params))

    rec = registry.get(backend_name)
    if rec is None:
        raise ValueError(f"backend desconhecido: {backend_name}")
    if not rec.available:
        raise RuntimeError(f"backend '{backend_name}' indisponivel: {rec.reason}")

    _logi("pipe.video_read", name=Path(video_path).name)
    from .video import read_video

    info = read_video(str(video_path), max_frames=max_frames)
    _logi("pipe.video_info", count=info.count, width=info.width,
          height=info.height, fps=f"{info.fps:.1f}")

    backend = rec.backend
    backend.load(params)
    ctx = {"width": info.width, "height": info.height, "fps": info.fps, "job_id": job_id}
    _logi("pipe.backend_run", backend=backend_name)
    frames = backend.infer_video(info.frames, ctx)
    fb = getattr(backend, "_fallback_reason", "")
    if fb:
        _logi("pipe.backend_warning", message=fb)
    if not isinstance(frames, list) or not frames:
        raise RuntimeError("backend nao retornou keypoints")
    _logi("pipe.backend_frames", backend=backend_name, n=len(frames))

    # 3) suavizacao + 3D
    smoothing = params.get("smoothing", "oneeuro")
    frames = smooth_frames(frames, method=smoothing)

    # debug: persiste os keypoints 2D usados (overlay "esqueleto detectado" na UI)
    kp2d_info = None
    try:
        kp_rows = []
        for f in frames:
            if f.kp2d is None:
                kp_rows.append(None)
                continue
            kp_arr = np.asarray(f.kp2d, np.float64)
            sc = (np.asarray(f.score, np.float64) if f.score is not None
                  else np.ones(kp_arr.shape[0], np.float64))
            kp_rows.append([[round(float(x), 2), round(float(y), 2), round(float(s), 4)]
                            for (x, y), s in zip(kp_arr[:, :2], sc)])
        kp_path = job_dir / "kp2d.json"
        kp_path.write_text(json.dumps({
            "fps": float(info.fps or 30.0),
            "width": int(info.width or 0),
            "height": int(info.height or 0),
            "frames": kp_rows,
        }), encoding="utf-8")
        kp2d_info = {"path": str(kp_path), "bytes": kp_path.stat().st_size}
    except Exception as exc:  # noqa: BLE001
        _logi("pipe.kp2d_save_fail", error=exc)

    has_3d = any(f.kp3d is not None for f in frames)
    if not has_3d and params.get("lift_2d_to_3d", True):
        _logi("pipe.lifter")
        frames = AnalyticLifter().lift(frames)
        has_3d = any(f.kp3d is not None for f in frames)
    if not has_3d:
        raise RuntimeError("nenhum keypoint 3D disponivel apos o lifting")

    # 4) retarget
    _logi("pipe.retarget")
    target_fps = float(params.get("fps", info.fps or 30.0))
    anim = Retargeter(fps=target_fps).retarget(frames)
    fk = fk_validation_error(anim, frames)
    _logi("pipe.fk_error", cm=f"{fk['max_position_m'] * 100:.2f}",
          deg=f"{fk['max_angle_deg']:.2f}")

    # 4b) calibracao da cabeca: ancora Neck/Head no frame inicial (o vetor
    # nose-chest estimado carrega um vies constante de dezenas de graus)
    if params.get("head_calibration", True):
        hc = calibrate_head(anim, frame=int(params.get("head_calibration_frame", 0)))
        if hc["applied"]:
            detalhes = ", ".join(f"{k} {v['angle_deg']:.1f} deg" for k, v in hc["bones"].items())
            _logi("pipe.head_calibrated", frame=hc["frame"], details=detalhes)
        anim.meta["head_calibration"] = hc

    # 5) malha do usuario (opcional) + checagem de compatibilidade do esqueleto
    mesh_report = None
    skin_mesh = None
    rig = None
    if mesh_path:
        from .mesh import check_compatibility, load_mesh

        report = check_compatibility(mesh_path, lang=_L)
        mesh_report = report.as_dict()
        _logi("pipe.mesh_compat", format=report.format, matched=len(report.matched),
              compatible=report.compatible, attachable=report.attachable)
        for msg in report.messages:
            _logi("pipe.mesh_note", message=msg)
        if report.compatible and report.attachable:
            try:
                skin_mesh = load_mesh(mesh_path, report)
                extra = skin_mesh.get("stats") or {}
                _det = ""
                if extra:
                    _det = _i18n.fmt("pipe.mesh_attached_extra", _L,
                                     meshes=extra.get("meshes"),
                                     unweighted=extra.get("unweighted", 0))
                _logi("pipe.mesh_attached", vertices=skin_mesh["positions"].shape[0],
                      details=_det)
                if extra:
                    mesh_report["stats"] = extra
            except Exception as exc:  # noqa: BLE001
                mesh_report["attachable"] = False
                mesh_report["messages"].append(
                    _i18n.fmt("pipe.mesh_attach_fail_msg", _L, error=exc))
                _logi("pipe.mesh_attach_fail", error=exc)
            if skin_mesh is not None:
                # pivos proprios: le o esqueleto de rest do arquivo (Mixamo
                # autorigger -> TransformLink dos clusters) para a exportacao
                try:
                    from .mesh import mesh_skeleton

                    rig = mesh_skeleton(mesh_path)
                except Exception as exc:  # noqa: BLE001
                    _logi("pipe.mesh_rig_fail", error=exc)
                    rig = None
                if rig and rig.get("ok"):
                    mesh_report.setdefault("stats", {})["rig"] = {
                        "source": rig.get("source"), "bones": rig.get("n_bones")}
                    _logi("pipe.mesh_rig_ok", bones=rig["n_bones"],
                          source=rig.get("source"))
                else:
                    rig = None
                    _logi("pipe.mesh_rig_missing")

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

        # US-07: anticolisao na execucao principal. Padrao: config do projeto;
        # tamanhos detectados da MALHA anexa a cada execucao (requisito).
        col_cfg = refine_spec.get("collision")
        if col_cfg is True:
            col_cfg = Path(__file__).resolve().parents[1] / "config" / "collision_default.yaml"
        col_mesh_lengths = None
        col_offsets = None
        if col_cfg and mesh_path and skin_mesh is not None:
            try:
                from .mesh import mesh_bone_lengths

                col_mesh_lengths = mesh_bone_lengths(mesh_path)
            except Exception as exc:
                _logi("pipe.collision_sizes_fail", error=exc)
            if rig and rig.get("ok") and col_mesh_lengths is not None:
                col_offsets = {b: np.asarray(v["world_offset"], np.float64)
                               for b, v in rig["bones"].items()}

        # lado (frente/tras) de cada MEMBRO segundo o VIDEO (kp3d canonico):
        # impede a anticolisao de trocar o braco de lado entre frames
        side_history = None
        if col_cfg:
            try:
                side_history = _arm_side_history(frames)
            except Exception as exc:
                _logi("pipe.collision_side_fail", error=exc)
                side_history = None

        anim, refine_report = refine_animation(
            anim,
            constraints_config=c_cfg,
            collision_config=col_cfg,
            collision_mesh_lengths=col_mesh_lengths,
            collision_skeleton_offsets=col_offsets,
            filters_config=f_cfg,
            edits=refine_spec.get("edits"),
            side_history=side_history,
        )
        _filtros = ", ".join((refine_report.filters or {}).get("filters", []) or [])
        _fsuf = _i18n.fmt("pipe.refine_filters", _L, filters=_filtros) if _filtros else ""
        _logi("pipe.refine_applied", stages=" -> ".join(refine_report.stages),
              filters=_fsuf)
        if refine_report.constraints:
            _logi("pipe.refine_constraints",
                  n=refine_report.constraints["frames_corrected_total"])
        if refine_report.collision:
            _col = refine_report.collision
            _logi("pipe.refine_collision",
                  n=_col.get("frames_corrected_total", 0),
                  corrections=_col.get("corrections_total", 0),
                  max_deg=_col.get("max_correction_deg", 0.0),
                  source=_col.get("skeleton_source", "reference"))

    # 4d) hand tracking (opcional): anima os ossos de dedo
    hands_info = None
    hands_spec = params.get("hands") if isinstance(params, dict) else None
    if isinstance(hands_spec, dict) and hands_spec.get("enabled"):
        try:
            from .hands import HandTracker, apply_hands
            from .provision import run_plan as _prov

            _prov("hands", log=log)
            _logi("pipe.hands_start")
            tracker = HandTracker(
                model_path=hands_spec.get("model_path") or None,
                num_hands=int(hands_spec.get("num_hands", 2)),
                min_conf=float(hands_spec.get("min_conf", 0.3)))
            hfs = tracker.detect(info.frames, float(info.fps or 30.0),
                                 upscale=float(hands_spec.get("upscale", 1.0)))
            try:
                # debug: persiste os landmarks das maos (overlay 2D com maos)
                from .hands import hands_debug_payload

                payload = hands_debug_payload(hfs, int(info.width or 0),
                                              int(info.height or 0),
                                              float(info.fps or 30.0))
                h_path = job_dir / "hands.json"
                h_path.write_text(json.dumps(payload), encoding="utf-8")
                hands_info = {"path": str(h_path), "bytes": h_path.stat().st_size}
            except Exception as exc:  # noqa: BLE001
                _logi("pipe.hands_save_fail", error=exc)
            com_maos = sum(1 for h in hfs if h.left is not None or h.right is not None)
            anim, hands_rep = apply_hands(anim, hfs,
                                          mirror=bool(hands_spec.get("mirror", True)))
            _logi("pipe.hands_done", frames=com_maos, total=len(hfs),
                  rotations=hands_rep["rotations_applied"])
        except Exception as exc:  # noqa: BLE001
            _logi("pipe.hands_fail", error=exc)

    # 5) animacao: persiste o clipe bakeado (a camada de refinamento e o editor
    # de bone trabalham sobre ele, sem depender de re-rodar o video)
    try:
        from .refine.io import save_animation

        save_animation(anim, job_dir / "anim.json")
        _logi("pipe.anim_saved", n=anim.num_frames)
    except Exception as exc:  # noqa: BLE001
        _logi("pipe.anim_save_fail", error=exc)

    # 6) exportacao
    glb_info = build_glb(anim, job_dir / "model.glb", skin_mesh=skin_mesh, rig=rig)
    _logi("pipe.glb", bytes=glb_info["bytes"], bones=glb_info["bones"],
          frames=glb_info["frames"], mesh=glb_info["mesh"],
          rig=glb_info.get("rig", "reference"))
    fbx_info = build_fbx(anim, job_dir / "model.fbx")
    _logi("pipe.fbx", bytes=fbx_info["bytes"], curves=fbx_info["curves"])

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
        "kp2d": kp2d_info,
        "hands": hands_info,
    }
