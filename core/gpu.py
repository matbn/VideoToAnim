"""Deteccao de GPU e escolha da variante correta dos requisitos.

O objetivo e nao instalar/rodar em CPU quando existe GPU compativel — e, quando
NAO existe, dizer exatamente por que (ex.: "sua GPU e AMD, mas este backend so
publica build CUDA/NVIDIA").

Fornecedores reconhecidos: `nvidia`, `amd`, `intel`, `apple`, `unknown`.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GPU:
    vendor: str                 # nvidia | amd | intel | apple | unknown
    name: str
    vram_mb: int | None = None
    driver: str = ""
    cuda: str = ""              # versao de CUDA reportada pelo driver
    source: str = ""            # de onde veio a informacao


@dataclass
class ComputeTarget:
    device: str                 # cuda | directml | mps | rocm | cpu
    provider: str               # nome legivel
    gpu: GPU | None = None
    reason: str = ""
    extra_pip: list[str] = field(default_factory=list)   # args extras de pip
    reason_code: str = ""       # chave de traducao (front resolve PT/EN)
    reason_vars: dict | None = None


# ---------------------------------------------------------------------------
# Deteccao
# ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except Exception:  # noqa: BLE001
        return ""


def ensure_cuda_dlls() -> list[str]:
    """Registra as DLLs de CUDA/cuDNN instaladas via pip (nvidia-*) no processo.

    O `onnxruntime-gpu` NAO embute o cuDNN: sem `cudnn64_9.dll` no PATH o provider
    CUDA falha em runtime (NOT_IMPLEMENTED). Aqui as pastas `site-packages/nvidia/*/bin`
    entram no PATH — o `add_dll_directory` sozinho nao basta, porque o cuDNN resolve
    as sub-DLLs pela busca padrao do Windows. Devolve as pastas registradas.
    """
    import os
    import site
    import sys

    if sys.platform != "win32":
        return []
    dirs: list[str] = []
    raizes: list[Path] = []
    try:
        raizes += [Path(sp) / "nvidia" for sp in site.getsitepackages()]
    except Exception:  # noqa: BLE001
        pass
    for raiz in raizes:
        if not raiz.exists():
            continue
        for sub in sorted(raiz.iterdir()):
            binp = sub / "bin"
            if binp.exists() and str(binp) not in dirs:
                try:
                    os.add_dll_directory(str(binp))
                    dirs.append(str(binp))
                except Exception:  # noqa: BLE001
                    pass
    if dirs:
        extra = ";".join(dirs)
        os.environ["PATH"] = extra + ";" + os.environ.get("PATH", "")
    return dirs


def detect_gpus() -> list[GPU]:
    """Lista as GPUs presentes, com o maximo de informacao que o SO oferece."""
    gpus: list[GPU] = []

    # --- NVIDIA (nvidia-smi) --------------------------------------------
    if shutil.which("nvidia-smi"):
        out = _run(["nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits"])
        cuda = ""
        full = _run(["nvidia-smi"])
        for line in full.splitlines():
            if "CUDA Version" in line:
                cuda = line.split("CUDA Version:")[-1].strip().split()[0]
                break
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if not parts or not parts[0]:
                continue
            gpus.append(GPU(
                vendor="nvidia", name=parts[0],
                vram_mb=int(float(parts[1])) if len(parts) > 1 and parts[1].isdigit() else None,
                driver=parts[2] if len(parts) > 2 else "",
                cuda=cuda, source="nvidia-smi",
            ))

    # --- Windows: demais adaptadores (AMD/Intel) -------------------------
    if platform.system() == "Windows":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_VideoController | "
                    "Select-Object -ExpandProperty Name"])
        for line in out.strip().splitlines():
            nome = line.strip()
            if not nome:
                continue
            low = nome.lower()
            if "nvidia" in low or "microsoft basic" in low:
                continue
            vendor = ("amd" if any(k in low for k in ("amd", "radeon"))
                      else "intel" if "intel" in low else "unknown")
            gpus.append(GPU(vendor=vendor, name=nome, source="wmi"))

    # --- Apple Silicon ---------------------------------------------------
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        gpus.append(GPU(vendor="apple", name="Apple Silicon (MPS)", source="platform"))

    # --- AMD ROCm em Linux ----------------------------------------------
    if platform.system() == "Linux":
        if Path("/opt/rocm").exists() or shutil.which("rocminfo"):
            if not any(g.vendor == "amd" for g in gpus):
                gpus.append(GPU(vendor="amd", name="AMD (ROCm)", source="rocm"))
    return gpus


def best_gpu(gpus: list[GPU] | None = None, prefer: tuple[str, ...] = ("nvidia", "apple", "amd", "intel")) -> GPU | None:
    gpus = gpus if gpus is not None else detect_gpus()
    for vendor in prefer:
        for g in gpus:
            if g.vendor == vendor:
                return g
    return gpus[0] if gpus else None


# ---------------------------------------------------------------------------
# Capacidade de cada backend
# ---------------------------------------------------------------------------
# engine: como o backend executa → define quais aceleradores existem
BACKEND_ENGINE = {
    "vitpose": "onnx",
    "rtmpose": "onnx",
    "mediapipe": "tflite",
    "yolopose": "torch",
    "motionbert": "torch",
    "mhformer": "torch",
    "simplebaseline": "torch",
    "openpose": "cpp",
    "sam3dbody": "torch",
    "wham": "torch",
    "synthetic": "none",
}

ENGINE_LABEL = {
    "onnx": "ONNX Runtime",
    "torch": "PyTorch",
    "tflite": "TensorFlow Lite (MediaPipe)",
    "cpp": "build C++ (OpenPose)",
    "none": "—",
}


def _rotulo_gpu(g: "GPU") -> str:
    """Rotulo da GPU sem duplicar o vendor ('NVIDIA GeForce RTX 5060 ...')."""
    nome = (g.name or "").strip()
    if nome.lower().startswith((g.vendor or "").lower()):
        return nome
    prefixo = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel",
               "apple": "Apple"}.get(g.vendor, (g.vendor or "?").upper())
    return f"{prefixo} {nome}"


def compute_target(backend: str, gpus: list[GPU] | None = None) -> ComputeTarget:
    """Qual acelerador usar para este backend nesta maquina, e por que."""
    gpus = gpus if gpus is not None else detect_gpus()
    engine = BACKEND_ENGINE.get(backend, "none")
    gpu = best_gpu(gpus)
    sistema = platform.system()

    if engine == "none":
        return ComputeTarget("cpu", "CPU", None, "backend nao usa acelerador",
                             reason_code="engine.none")

    # --- ONNX Runtime ---------------------------------------------------
    if engine == "onnx":
        if gpu and gpu.vendor == "nvidia":
            return ComputeTarget(
                "cuda", "ONNX Runtime · CUDAExecutionProvider", gpu,
                _rotulo_gpu(gpu) + (f" (CUDA {gpu.cuda})" if gpu.cuda else ""),
                extra_pip=["onnxruntime-gpu"],
            )
        if gpu and gpu.vendor in ("amd", "intel") and sistema == "Windows":
            return ComputeTarget(
                "directml", "ONNX Runtime · DirectMLExecutionProvider", gpu,
                f"{_rotulo_gpu(gpu)} via DirectML (Windows)",
                extra_pip=["onnxruntime-directml"],
            )
        if gpu and gpu.vendor == "apple":
            return ComputeTarget(
                "mps", "ONNX Runtime · CoreML", gpu, "Apple Silicon",
                extra_pip=["onnxruntime-silicon"],
            )
        return ComputeTarget("cpu", "CPU", None,
                             "nenhuma GPU compativel com ONNX Runtime encontrada",
                             extra_pip=["onnxruntime"], reason_code="onnx.none")

    # --- PyTorch --------------------------------------------------------
    if engine == "torch":
        if gpu and gpu.vendor == "nvidia":
            index = f"https://download.pytorch.org/whl/cu{_cuda_tag(gpu.cuda)}"
            return ComputeTarget("cuda", f"PyTorch · CUDA (cu{_cuda_tag(gpu.cuda)})", gpu,
                                 _rotulo_gpu(gpu),
                                 extra_pip=["--index-url", index])
        if gpu and gpu.vendor == "amd":
            if sistema == "Linux":
                return ComputeTarget("rocm", "PyTorch · ROCm", gpu,
                                     f"{_rotulo_gpu(gpu)} no Linux (ROCm)",
                                     extra_pip=["--index-url", "https://download.pytorch.org/whl/rocm6.1"],
                                     reason_code="torch.rocmLinux",
                                     reason_vars={"gpu": _rotulo_gpu(gpu)})
            return ComputeTarget(
                "cpu", "CPU (PyTorch sem CUDA)", gpu,
                f"sua GPU ({_rotulo_gpu(gpu)}) e o PyTorch oficial NAO publica build "
                f"ROCm para {sistema} — este backend so acelera em NVIDIA aqui. "
                f"No Windows existe 'torch-directml', mas com suporte limitado e sem garantia.",
                extra_pip=[], reason_code="torch.amdWindows",
                reason_vars={"gpu": _rotulo_gpu(gpu), "sistema": sistema},
            )
        if gpu and gpu.vendor == "apple":
            return ComputeTarget("mps", "PyTorch · MPS (Metal)", gpu, "Apple Silicon", extra_pip=[])
        return ComputeTarget("cpu", "CPU", None, "nenhuma GPU compativel com PyTorch encontrada",
                             reason_code="torch.none")

    # --- MediaPipe (TFLite) ---------------------------------------------
    if engine == "tflite":
        if gpu and gpu.vendor == "nvidia":
            return ComputeTarget(
                "cpu", "CPU", gpu,
                "o wheel de desktop do MediaPipe (pip) e compilado SEM suporte a GPU: "
                "'GPU processing is disabled in build flags' (confirmado nesta maquina, "
                "mediapipe 1.0.1). O delegate de GPU so existe nas builds moveis "
                "(Android/iOS). Em CPU a inferencia e rapida (~13 ms/frame para maos); "
                "os backends pesados continuam usando a GPU.",
                extra_pip=["mediapipe"], reason_code="tflite.desktop",
            )
        return ComputeTarget("cpu", "CPU", gpu, "", extra_pip=["mediapipe"])

    return ComputeTarget("cpu", "CPU", gpu, f"engine {engine} sem aceleracao automatica",
                         reason_code="engine.generic", reason_vars={"engine": engine})


def _cuda_tag(cuda: str) -> str:
    """Mapeia a versao de CUDA do driver para o sufixo de wheel do PyTorch."""
    versoes = {"13": "128", "12": "124", "11": "118"}
    major = (cuda or "").split(".")[0]
    if major and major.isdigit():
        for k, v in versoes.items():
            if int(major) >= int(k):
                return v
    return "124"


def summary() -> dict:
    """Resumo para a interface: GPUs + alvo de cada backend."""
    gpus = detect_gpus()
    return {
        "gpus": [
            {"vendor": g.vendor, "name": g.name, "vram_mb": g.vram_mb,
             "driver": g.driver, "cuda": g.cuda, "source": g.source}
            for g in gpus
        ],
        "targets": {
            b: {"device": t.device, "provider": t.provider, "reason": t.reason,
                "extra_pip": t.extra_pip}
            for b, t in ((b, compute_target(b, gpus)) for b in BACKEND_ENGINE)
        },
    }


def accelerator_available() -> bool:
    """True se existe algum acelerador util para os backends de pose."""
    gpus = detect_gpus()
    return any(g.vendor in ("nvidia", "amd", "intel", "apple") for g in gpus)
