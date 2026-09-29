"""API de instalacao automatica dos backends (primeiro uso / botao no dropdown)."""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException

from core.provision import KIND_LABEL, plan_for, run_plan
from core.registry import build_registry

# ROOT calculado localmente DE PROPOSITO: importar de .app criaria um ciclo
# (o app inclui este router; este modulo importaria o app no meio do boot).
ROOT = Path(__file__).resolve().parents[1]

router = APIRouter(prefix="/api/backends", tags=["install"])

# estado em memoria por backend (log + status) para a interface acompanhar
_STATE: dict[str, dict] = {}
_LOCK = threading.Lock()


def _state(backend: str) -> dict:
    with _LOCK:
        return _STATE.setdefault(backend, {"status": "idle", "log": "", "report": None})


def _log(backend: str, msg: str) -> None:
    st = _state(backend)
    st["log"] = (st["log"] + msg + "\n")[-8000:]


@router.get("/{backend}/install")
def install_status(backend: str) -> dict:
    plan = plan_for(backend)
    st = _state(backend)
    return {"backend": backend, "status": st["status"], "log": st["log"],
            "report": st["report"], "plan": plan.as_dict(),
            "label": KIND_LABEL.get(plan.kind, plan.kind)}


@router.post("/{backend}/install")
def start_install(backend: str, force: bool = False) -> dict:
    reg = build_registry(ROOT / "plugins")
    if reg.get(backend) is None:
        raise HTTPException(404, f"backend desconhecido: {backend}")
    plan = plan_for(backend)
    if plan.kind == "manual" or not plan.steps:
        raise HTTPException(
            400, f"{backend} nao pode ser instalado automaticamente: {plan.manual_reason}")
    st = _state(backend)
    if st["status"] == "running":
        return {"backend": backend, "status": "running", "log": st["log"]}

    def _job() -> None:
        st["status"] = "running"
        st["log"] = ""
        st["report"] = None
        _log(backend, f"iniciando instalacao de {backend} ({plan.kind})")
        try:
            rep = run_plan(backend, force=force, log=lambda m: _log(backend, m))
            rep["available_after"] = _check_available(backend)
            st["report"] = rep
            st["status"] = "done" if rep["ok"] else "error"
            _log(backend, f"concluido: ok={rep['ok']} disponivel={rep['available_after']}")
        except Exception as exc:  # noqa: BLE001
            st["status"] = "error"
            st["report"] = {"ok": False, "error": str(exc)}
            _log(backend, f"ERRO: {exc}")

    threading.Thread(target=_job, daemon=True).start()
    return {"backend": backend, "status": "running"}


def _check_available(backend: str) -> bool:
    try:
        reg = build_registry(ROOT / "plugins")
        rec = reg.get(backend)
        return bool(rec and rec.available)
    except Exception:  # noqa: BLE001
        return False
