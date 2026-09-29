"""Provisionamento automatico dos backends.

Em vez de mostrar "indisponivel", cada backend declara um **plano de instalacao**
(pip, git clone, download) que roda no primeiro uso ou quando o usuario clica em
"instalar" no dropdown. Tudo acontece **dentro do projeto**: o venv atual e
pastas locais (`third_party/`, `models/`) — nada de mexer no sistema.

Nem tudo e automatizavel, e o plano diz isso explicitamente:

* ``auto``      — pip + download: funciona sem intervencao;
* ``pip-heavy`` — instala, mas arrasta dependencia grande (ex.: PyTorch);
* ``manual``    — **nao** ha como automatizar (build C++, licenca propria,
  arquivos atras de aceite); `manual_reason` explica exatamente o que fazer.
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Step:
    kind: str                      # pip | git | download | note
    target: str
    dest: str | None = None
    args: list[str] = field(default_factory=list)
    size_mb: float | None = None
    optional: bool = False
    note: str = ""


@dataclass
class InstallPlan:
    backend: str
    kind: str = "auto"             # auto | pip-heavy | manual
    steps: list[Step] = field(default_factory=list)
    manual_reason: str = ""
    note: str = ""
    manual_code: str = ""          # chave de traducao do motivo manual

    def as_dict(self) -> dict:
        try:
            from core.gpu import compute_target
            t = compute_target(self.backend)
            compute = {"device": t.device, "provider": t.provider,
                       "reason": t.reason,
                       "gpu": (t.gpu.name if t.gpu else ""),
                       "reason_code": t.reason_code,
                       "reason_vars": t.reason_vars or {}}
        except Exception:  # noqa: BLE001
            compute = {}
        return {
            "backend": self.backend,
            "kind": self.kind,
            "compute": compute,
            "manual_reason": self.manual_reason,
            "manual_code": self.manual_code,
            "note": self.note,
            "total_mb": round(sum(s.size_mb or 0 for s in self.steps), 1),
            "steps": [
                {"kind": s.kind, "target": s.target, "size_mb": s.size_mb,
                 "optional": s.optional, "note": s.note}
                for s in self.steps
            ],
        }


_HF_VIT = "https://huggingface.co/onnx-community/vitpose-base-simple/resolve/main"
_MP_MODEL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_full/float16/latest/pose_landmarker_full.task")
_TORCH_CPU = ["--index-url", "https://download.pytorch.org/whl/cpu"]

PLANS: dict[str, InstallPlan] = {
    "vitpose": InstallPlan("vitpose", "auto", [
        Step("pip", "onnxruntime", size_mb=15),
        Step("download", f"{_HF_VIT}/onnx/model_quantized.onnx",
             dest="models/vitpose/model_quantized.onnx", size_mb=83),
        Step("download", f"{_HF_VIT}/config.json", dest="models/vitpose/config.json"),
        Step("download", f"{_HF_VIT}/preprocessor_config.json",
             dest="models/vitpose/preprocessor_config.json"),
    ], note="ONNX Runtime + modelo quantizado (Apache-2.0). Roda em CPU."),

    "mediapipe": InstallPlan("mediapipe", "auto", [
        Step("pip", "mediapipe", size_mb=90),
        Step("download", _MP_MODEL, dest="models/pose_landmarker_full.task", size_mb=9),
    ], note="Tasks API (PoseLandmarker). Roda em CPU."),

    "rtmpose": InstallPlan("rtmpose", "auto", [
        Step("pip", "rtmlib", args=["--no-deps"], size_mb=1),
        Step("note", "os ONNX do RTMPose (~48 MB) sao baixados pelo proprio rtmlib na 1a inferencia"),
    ], note="Usa o onnxruntime ja instalado. Sem mmcv."),

    "yolopose": InstallPlan("yolopose", "pip-heavy", [
        Step("pip", "ultralytics", size_mb=2500,
             note="arrasta o PyTorch (~2.5 GB no total)"),
    ], note="Rapido em GPU. **AGPL-3.0**: avalie a licenca antes de distribuir."),

    "motionbert": InstallPlan("motionbert", "pip-heavy", [
        Step("pip", "torch", args=_TORCH_CPU, size_mb=124),
        Step("git", "https://github.com/Walter0807/MotionBERT", dest="third_party/MotionBERT"),
        Step("note", "pesos: veja third_party/MotionBERT/README (checkpoint do HF) e aponte "
                     "V2M_MOTIONBERT_CKPT para o arquivo"),
    ], note="Lifting 2D->3D. Precisa de um detector 2D como fonte."),

    "mhformer": InstallPlan("mhformer", "pip-heavy", [
        Step("pip", "torch", args=_TORCH_CPU, size_mb=124),
        Step("git", "https://github.com/Vegetebird/MHFormer", dest="third_party/MHFormer"),
        Step("note", "os pesos ficam no Google Drive (link no README do repo) e nao tem "
                     "download confiavel por script — baixe manualmente", optional=True),
    ], note="Lifting temporal (H36M-17)."),

    "hands": InstallPlan("hands", "auto", [
        Step("pip", "mediapipe", size_mb=90),
        Step("download",
             "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task",
             dest="models/hand_landmarker.task", size_mb=8),
    ], note="modelo de mao do MediaPipe (Apache-2.0) — anima os 40 ossos de dedo"),

    "simplebaseline": InstallPlan("simplebaseline", "auto", [
        Step("git", "https://github.com/microsoft/human-pose-estimation.pytorch",
             dest="third_party/human-pose-estimation.pytorch"),
        Step("note", "checkpoint: baixe pelo README do repo e aponte V2M_SIMPLEBASELINE_CKPT"),
    ], note="Top-down classico (COCO-17)."),

    "openpose": InstallPlan("openpose", "manual", [], manual_code="openpose",
                            manual_reason="exige compilar C++ (CMake + CUDA/OpenCL) e os modelos Caffe; "
                                          "nao existe pacote Python instalavel. Alem disso a licenca e "
                                          "NON-COMMERCIAL (codigo e pesos)."),

    "sam3dbody": InstallPlan("sam3dbody", "manual", [], manual_code="sam3dbody",
                             manual_reason="pacote e checkpoint oficiais da Meta, com liberacao/termos "
                                           "proprios (SAM License) — nao ha download automatico."),

    "wham": InstallPlan("wham", "manual", [], manual_code="wham",
                        manual_reason="depende do corpo SMPL/SMPL-X, cujo download exige aceitar a "
                                      "licenca NON-COMMERCIAL da Max Planck (login proprio)."),
}

# rotulos para a interface
KIND_LABEL = {
    "auto": "instalável automaticamente",
    "pip-heavy": "instalável (baixa dependência grande)",
    "manual": "exige passo manual",
}


def plan_for(backend: str) -> InstallPlan:
    return PLANS.get(backend, InstallPlan(backend, "manual", [],
                                          manual_reason="sem plano de instalacao definido",
                                          manual_code="default"))


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------
def _run(cmd: list[str], log) -> tuple[bool, str]:
    log("$ " + " ".join(cmd))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    tail = (p.stdout or "").strip().splitlines()[-4:]
    err = (p.stderr or "").strip().splitlines()[-4:]
    detail = " | ".join(tail + err)[:400]
    return p.returncode == 0, detail


def _pip_target_installed(target: str) -> bool:
    mod = {"onnxruntime": "onnxruntime", "mediapipe": "mediapipe", "rtmlib": "rtmlib",
           "ultralytics": "ultralytics", "torch": "torch"}.get(target)
    if not mod:
        return False
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def resolve_plan(backend: str) -> tuple[InstallPlan, "object"]:
    """Ajusta o plano a GPU detectada: instala a VARIANTE certa dos requisitos.

    Ex.: `onnxruntime` vira `onnxruntime-gpu` numa NVIDIA, `onnxruntime-directml`
    numa AMD/Intel no Windows, e continua `onnxruntime` (CPU) quando nao ha GPU
    compativel. Para torch, acrescenta o `--index-url` da build CUDA.
    """
    from dataclasses import replace

    from core.gpu import compute_target

    plan = plan_for(backend)
    target = compute_target(backend)
    if plan.kind == "manual" or not plan.steps:
        return plan, target

    passos = []
    for s in plan.steps:
        # variante do modelo conforme o dispositivo: fp32 na GPU, int8 na CPU
        if s.kind == "download" and s.target.endswith("model_quantized.onnx") \
                and target.device in ("cuda", "directml", "mps"):
            passos.append(replace(s, target=s.target.replace("model_quantized.onnx", "model.onnx"),
                                  dest=(s.dest or "").replace("model_quantized.onnx", "model.onnx")
                                  if s.dest else None,
                                  note=f"{s.note} [fp32 para {target.device}]".strip()))
            continue
        # onnxruntime-gpu NAO traz cuDNN: sem ele o provider CUDA falha em runtime
        if s.kind == "pip" and s.target == "onnxruntime-gpu" and target.device == "cuda":
            passos.append(s)
            passos.append(replace(s, target="nvidia-cudnn-cu12",
                                  note="cuDNN 9 exigido pelo onnxruntime-gpu", size_mb=700))
            passos.append(replace(s, target="nvidia-cublas-cu12",
                                  note="cuBLAS para o provider CUDA", size_mb=300))
            continue
        if s.kind == "pip" and s.target.startswith("onnxruntime") and target.extra_pip:
            variante = target.extra_pip[-1]
            if variante != s.target:
                passos.append(replace(s, target=variante,
                                      note=f"{s.note} [{target.provider}]".strip()))
                continue
        if s.kind == "pip" and s.target == "torch" and target.extra_pip \
                and target.extra_pip[0] == "--index-url":
            passos.append(replace(s, args=list(target.extra_pip),
                                  note=f"{s.note} [{target.provider}]".strip()))
            continue
        passos.append(s)
    return InstallPlan(plan.backend, plan.kind, passos, plan.manual_reason, plan.note,
                       plan.manual_code), target


def run_plan(backend: str, *, force: bool = False, log=None) -> dict:
    """Executa o plano de instalacao de um backend. Devolve um relatorio."""
    log = log or (lambda _m: None)
    plan, target = resolve_plan(backend)
    log(f"acelerador para {backend}: {target.provider} ({target.device})"
        + (f" — {target.reason}" if target.reason else ""))
    started = time.time()
    results: list[dict] = []

    if plan.kind == "manual" or not plan.steps:
        log(f"{backend}: sem instalacao automatica — {plan.manual_reason}")
        return {"backend": backend, "kind": plan.kind, "ok": False,
                "manual_reason": plan.manual_reason, "results": [],
                "seconds": 0.0}

    for step in plan.steps:
        t0 = time.time()
        if step.kind == "note":
            log(f"nota: {step.target}")
            results.append({"step": "note", "ok": True, "detail": step.target,
                            "seconds": 0.0})
            continue

        if step.kind == "pip":
            if not force and _pip_target_installed(step.target):
                log(f"{step.target}: ja instalado, pulando")
                results.append({"step": "pip", "target": step.target, "ok": True,
                                "detail": "ja instalado", "seconds": 0.0})
                continue
            ok, detail = _run([sys.executable, "-m", "pip", "install", *step.args, step.target], log)

        elif step.kind == "git":
            dest = ROOT / (step.dest or Path(step.target).stem)
            if dest.exists() and any(dest.iterdir()) and not force:
                log(f"{dest.name}: repositorio ja existe, pulando")
                results.append({"step": "git", "target": step.target, "ok": True,
                                "detail": f"{dest} ja existe", "seconds": 0.0})
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            ok, detail = _run(["git", "clone", "--depth", "1", step.target, str(dest)], log)

        elif step.kind == "download":
            dest = ROOT / (step.dest or Path(step.target).name)
            if dest.exists() and dest.stat().st_size > 0 and not force:
                log(f"{dest.name}: ja baixado, pulando")
                results.append({"step": "download", "target": step.target, "ok": True,
                                "detail": "ja baixado", "seconds": 0.0})
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                urllib.request.urlretrieve(step.target, dest)
                ok, detail = True, f"{dest.stat().st_size} bytes"
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, str(exc)
            log(f"download {dest.name}: {'ok' if ok else 'falhou'} ({detail})")
        else:
            ok, detail = False, f"tipo de passo desconhecido: {step.kind}"

        results.append({"step": step.kind, "target": step.target, "ok": ok,
                        "detail": detail, "seconds": round(time.time() - t0, 2),
                        "optional": step.optional})
        if not ok and not step.optional:
            break

    obrigatorios = [r for r in results if not r.get("optional")]
    ok_geral = all(r["ok"] for r in obrigatorios)
    return {"backend": backend, "kind": plan.kind, "ok": ok_geral,
            "results": results, "seconds": round(time.time() - started, 2),
            "manual_reason": "" if ok_geral else plan.manual_reason,
            "device": target.device, "provider": target.provider,
            "gpu": (target.gpu.name if target.gpu else ""),
            "device_reason": target.reason}


def availability_after(backend: str) -> bool:
    """Reimporta e checa o backend depois da instalacao (para o relatorio)."""
    from core.registry import build_registry

    reg = build_registry(ROOT / "plugins")
    rec = reg.get(backend)
    return bool(rec and rec.available)
