"""Persistencia do registro de jobs."""
from __future__ import annotations

from core.jobs import JobStore


def test_crud_e_persistencia(tmp_path):
    db = tmp_path / "jobs.db"
    store = JobStore(db)
    jid = store.create("a.mp4", 1234, "vitpose", {"fps": 30})
    store.update(jid, status="running")
    store.append_log(jid, "iniciado")
    store.set_artifact(jid, "glb", str(tmp_path / "model.glb"), 999)

    # "reinicia" o servidor: nova instancia, mesmo banco
    store2 = JobStore(db)
    job = store2.get(jid)
    assert job is not None
    assert job["video_name"] == "a.mp4"
    assert job["video_bytes"] == 1234
    assert job["backend"] == "vitpose"
    assert job["params"] == {"fps": 30}
    assert job["status"] == "running"
    assert "iniciado" in job["log"]
    assert job["artifacts"]["glb"]["size"] == 999
    assert job["created_at"] > 0


def test_lista_ordenada_e_limite(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    ids = [store.create(f"v{i}.mp4", i, "synthetic") for i in range(5)]
    jobs = store.list(limit=3)
    assert len(jobs) == 3
    assert {j["id"] for j in jobs}.issubset(set(ids))


def test_erro_fica_registrado(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    jid = store.create("b.mp4", 10, "synthetic")
    store.update(jid, status="error", error="falha simulada")
    job = JobStore(tmp_path / "jobs.db").get(jid)
    assert job["status"] == "error"
    assert job["error"] == "falha simulada"
