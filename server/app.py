"""API FastAPI + servidor da SPA (three.js)."""
from __future__ import annotations

import json
import queue
import shutil
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.jobs import JobStore  # noqa: E402
from core.pipeline import run_pipeline  # noqa: E402
from core.registry import build_registry  # noqa: E402

STORAGE = ROOT / "storage"
JOBS_DIR = STORAGE / "jobs"
JOBS_DB = STORAGE / "jobs.db"
WEB_DIR = ROOT / "web"

STORAGE.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

from core import i18n as _i18n  # noqa: E402  (log dos jobs segue o idioma da UI)

store = JobStore(JOBS_DB)
registry = build_registry(ROOT / "plugins")

# Fila de jobs com UMA thread DAEMON. (Antes era um ThreadPoolExecutor: ao
# encerrar, o interpretador espera as futures pendentes do executor -- com jobs
# rodando/enfileirados o processo ficava preso em "Shutting down" ate a fila
# drenar por completo. Relatado pelo usuario em 29/09/2026.)
_JOB_QUEUE: "queue.Queue[tuple]" = queue.Queue()
_CURRENT_JOB: dict = {"id": None}


def _mark_aborted(job_id: str) -> None:
    job = store.get(job_id) or {}
    if job.get("status") in ("running", "queued"):
        lang = job.get("lang") or "pt"
        store.update(job_id, status="error", finished_at=time.time(),
                     error=_i18n.fmt("job.aborted", lang))
        store.append_log(job_id, _i18n.fmt("job.aborted", lang))


def _abort_pending_jobs() -> None:
    """No shutdown: job em execucao e os ainda na fila viram 'interrompido'."""
    cur = _CURRENT_JOB.get("id")
    if cur:
        try:
            _mark_aborted(cur)
        except Exception:  # noqa: BLE001 - nunca travar o shutdown
            pass
    while True:
        try:
            item = _JOB_QUEUE.get_nowait()
        except queue.Empty:
            return
        try:
            _mark_aborted(item[0])
        except Exception:  # noqa: BLE001 - nunca travar o shutdown
            pass


def _mark_stale_jobs() -> None:
    """No boot: jobs running/queued de um processo anterior sao interrompidos."""
    try:
        jobs = store.list(limit=500)
    except Exception:  # noqa: BLE001
        return
    for j in jobs:
        if j.get("status") in ("running", "queued"):
            _mark_aborted(j["id"])


@asynccontextmanager
async def _lifespan(_app):
    _mark_stale_jobs()
    yield
    _abort_pending_jobs()


app = FastAPI(title="VideoToAnim", version="1.0.0", lifespan=_lifespan)


@app.middleware("http")
async def _no_store_assets(request, call_next):
    """Ferramenta local: nunca deixar HTML/JS/CSS em cache.

    Sem isto o navegador pode continuar servindo o app.js/style.css antigos
    depois de uma atualizacao do codigo ("corrigi, mas continua igual").
    """
    resp = await call_next(request)
    p = request.url.path
    if p.startswith("/static/") or p in ("/", "/refine"):
        resp.headers["Cache-Control"] = "no-store"
    return resp

MAX_UPLOAD_MB = 512


def _run_job(job_id: str, video_path: Path, backend: str, params: dict,
             mesh_path: Path | None = None) -> None:
    job_dir = JOBS_DIR / job_id
    job_lang = _i18n.norm_lang((store.get(job_id) or {}).get("lang"))
    store.update(job_id, status="running", started_at=__import__("time").time())
    store.append_log(job_id, _i18n.fmt("job.started", job_lang, backend=backend))
    try:
        result = run_pipeline(
            job_id=job_id,
            video_path=video_path,
            backend_name=backend,
            params=params,
            registry=registry,
            job_dir=job_dir,
            max_frames=params.get("max_frames"),
            mesh_path=mesh_path,
            lang=job_lang,
            logger=lambda m: store.append_log(job_id, m),
        )
        store.set_artifact(job_id, "glb", str(result["glb"]["path"]), result["glb"]["bytes"])
        store.set_artifact(job_id, "fbx", str(result["fbx"]["path"]), result["fbx"]["bytes"])
        if result.get("kp2d"):
            store.set_artifact(job_id, "kp2d", result["kp2d"]["path"], result["kp2d"]["bytes"])
        if result.get("hands"):
            store.set_artifact(job_id, "hands", result["hands"]["path"], result["hands"]["bytes"])
        store.update(job_id, metrics=result["metrics"])
        if result.get("mesh_report") is not None:
            store.update(job_id, mesh_report=result["mesh_report"])
        store.update(job_id, status="done", finished_at=__import__("time").time())
        store.append_log(job_id, _i18n.fmt("job.done", job_lang))
    except Exception as exc:  # noqa: BLE001
        store.append_log(job_id, _i18n.fmt("job.error", job_lang, error=exc))
        store.update(job_id, status="error", error=str(exc), finished_at=__import__("time").time())


def _job_worker() -> None:
    """Worker unico (serializa os jobs como o antigo executor de 1 worker)."""
    while True:
        job_id, video_path, backend, params, mesh_path = _JOB_QUEUE.get()
        _CURRENT_JOB["id"] = job_id
        try:
            _run_job(job_id, video_path, backend, params, mesh_path)
        except Exception as exc:  # noqa: BLE001 - rede de seguranca do worker
            try:
                store.update(job_id, status="error", error=str(exc),
                             finished_at=time.time())
            except Exception:  # noqa: BLE001
                pass
        finally:
            _CURRENT_JOB["id"] = None


# thread daemon: o encerramento do processo NUNCA espera jobs pendentes
threading.Thread(target=_job_worker, name="v2m-job-worker", daemon=True).start()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "backends": len(registry.list()), "registry_errors": registry.errors}


# US-03: /api/backends custa segundos (sonda de compute por backend). Cache TTL
# curto por processo; invalidado apos instalacoes (server.install_api) e
# desativado sob pytest para nao mascarar estado entre testes.
_BACKENDS_CACHE: dict = {}
_BACKENDS_TTL_S = 30.0


@app.get("/api/backends")
def list_backends(include_hidden: bool = False) -> dict:
    """Backends que aparecem no dropdown (oculta os de teste/infra por padrao)."""
    import os as _os
    import time as _time

    ent = _BACKENDS_CACHE.get(bool(include_hidden))
    if (ent is not None and not _os.environ.get("PYTEST_CURRENT_TEST")
            and (_time.time() - ent[0]) < _BACKENDS_TTL_S):
        return ent[1]
    default = registry.default()
    all_records = registry.list()
    records = all_records if include_hidden else [r for r in all_records if not r.hidden]
    payload = {
        "default": default.name if default else None,
        "preferred": registry.preferred_default_name(),
        "backends": [r.as_public_dict() for r in records],
        "hidden_count": sum(1 for r in all_records if r.hidden),
        "disabled_plugins": registry.disabled,
        "errors": registry.errors,
    }
    _BACKENDS_CACHE[bool(include_hidden)] = (_time.time(), payload)
    return payload


@app.post("/api/mesh/inspect")
async def inspect_mesh_file(mesh: UploadFile = File(...),
                            lang: str = Form("pt")) -> dict:
    """Diagnostico de uma malha antes de processar: formato, esqueleto e se e anexavel."""
    from core.mesh import check_compatibility, load_mesh

    lang = _i18n.norm_lang(lang)

    data = await mesh.read()
    if not data:
        raise HTTPException(400, _i18n.fmt("inspect.empty", lang))
    tmp = STORAGE / "inspect"
    tmp.mkdir(parents=True, exist_ok=True)
    suffix = Path(mesh.filename or "mesh").suffix or ".fbx"
    probe = tmp / f"probe{suffix}"
    probe.write_bytes(data)

    out: dict = {"filename": mesh.filename, "bytes": len(data)}
    try:
        rep = check_compatibility(probe, lang=lang)
    except Exception as exc:  # noqa: BLE001
        out.update({"ok": False, "error": _i18n.fmt("inspect.fail", lang, error=exc)})
        return out
    out["ok"] = True
    out["report"] = rep.as_dict()
    if rep.compatible and rep.attachable:
        try:
            m = load_mesh(probe, rep)
            out["attach"] = {
                "ok": True,
                "vertices": int(m["positions"].shape[0]),
                "triangles": int(len(m["indices"]) // 3),
                "stats": m.get("stats"),
            }
        except Exception as exc:  # noqa: BLE001
            out["attach"] = {"ok": False, "error": str(exc)}
    else:
        out["attach"] = {"ok": False, "error": _i18n.fmt("inspect.incompatible", lang)}
    return out


@app.post("/api/jobs")
async def create_job(
    video: UploadFile = File(...),
    backend: str = Form(...),
    params: str = Form("{}"),
    mesh: UploadFile | None = File(None),
    lang: str = Form("pt"),
) -> JSONResponse:
    rec = registry.get(backend)
    if rec is None:
        raise HTTPException(400, f"backend desconhecido: {backend}")
    if not rec.available:
        raise HTTPException(400, f"backend '{backend}' indisponivel: {rec.reason}")
    try:
        parsed = json.loads(params or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("params deve ser um objeto JSON")
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"params invalido: {exc}") from exc

    lang = _i18n.norm_lang(lang)

    data = await video.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"arquivo maior que {MAX_UPLOAD_MB} MB")

    job_id = store.create(video.filename or "video", len(data), backend, parsed,
                          lang=lang)
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(video.filename or "video.mp4").suffix or ".mp4"
    video_path = job_dir / f"input{suffix}"
    with open(video_path, "wb") as fh:
        fh.write(data)
    store.set_artifact(job_id, "video", str(video_path), len(data))

    mesh_path = None
    mesh_name = ""
    if mesh is not None and (mesh.filename or ""):
        mdata = await mesh.read()
        if mdata:
            mesh_name = mesh.filename or "mesh"
            msuffix = Path(mesh_name).suffix or ".glb"
            mesh_path = job_dir / f"mesh{msuffix}"
            with open(mesh_path, "wb") as fh:
                fh.write(mdata)
            store.append_log(job_id, _i18n.fmt("job.mesh_received", lang,
                                               name=mesh_name, bytes=len(mdata)))

    store.update(job_id, log=(store.get(job_id) or {}).get("log", "")
                 + _i18n.fmt("job.upload_received", lang, name=video.filename,
                             bytes=len(data)) + "\n")
    _JOB_QUEUE.put((job_id, video_path, backend, parsed, mesh_path))
    return JSONResponse({"job_id": job_id, "status": "queued", "mesh": mesh_name}, status_code=201)


@app.get("/api/jobs")
def list_jobs(status: str | None = None, backend: str | None = None,
              limit: int = 200) -> dict:
    jobs = store.list(limit=max(1, min(int(limit), 500)))
    if status:
        jobs = [j for j in jobs if j.get("status") == status]
    if backend:
        jobs = [j for j in jobs if j.get("backend") == backend]
    return {"jobs": jobs, "total": len(jobs)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job nao encontrado")
    return job


_MEDIA_TYPES = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    ".fbx": "application/octet-stream", ".json": "application/json",
}


@app.get("/api/jobs/{job_id}/artifacts/{name}")
def download_artifact(job_id: str, name: str) -> FileResponse:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job nao encontrado")
    art = (job.get("artifacts") or {}).get(name)
    if not art:
        raise HTTPException(404, f"artefato '{name}' indisponivel")
    path = Path(art["path"])
    if not path.exists():
        raise HTTPException(404, "arquivo nao encontrado no disco")
    media = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    # "video" e servido inline (para tocar junto do esqueleto); os demais baixam
    filename = f"{job_id}{path.suffix}" if name == "video" else f"{job_id}.{name}"
    return FileResponse(path, filename=filename, media_type=media)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


from .install_api import router as _install_router  # noqa: E402
from .refine_api import router as _refine_router  # noqa: E402

app.include_router(_install_router)
app.include_router(_refine_router)


@app.get("/refine")
def refine_page() -> FileResponse:
    return FileResponse(WEB_DIR / "refine.html")
