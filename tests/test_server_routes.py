"""Regressao de rotas HTTP criticas.

Caiu em producao real: uma edicao no server/app.py perdeu o include do router
de refino — o servidor subia sem /api/refine/* (dropdown de filtros vazio) e
sem a pagina /refine, sem erro nenhum no log.

Nota sobre o FastAPI atual: roteadores incluidos aparecem em `app.routes` como
`_IncludedRouter` (sem `.path`); por isso a checagem usa o schema OpenAPI, que
resolve a arvore inteira — e tambem um subprocesso para provar que importar os
modulos de API ANTES do app nao quebra (import circular).
"""
import subprocess
import sys
from pathlib import Path

from server.app import app, list_backends

ROOT = Path(__file__).resolve().parents[1]


def _rotas() -> set:
    return set(app.openapi()["paths"])


def test_router_de_refino_registrado() -> None:
    rotas = _rotas()
    assert "/api/refine/filters" in rotas
    assert "/api/refine/constraints" in rotas
    assert any(p.startswith("/api/refine/animation") for p in rotas)


def test_pagina_do_editor_registrada() -> None:
    assert "/refine" in _rotas()


def test_router_de_instalacao_registrado() -> None:
    assert any(p.endswith("/install") for p in _rotas())


def test_pagina_principal_registrada() -> None:
    assert "/" in _rotas()


def test_payload_de_backends_expoe_compute() -> None:
    """O dispositivo (GPU/CPU + motivo) precisa vir no payload para a UI adaptar."""
    payload = list_backends()
    vit = next(b for b in payload["backends"] if b["name"] == "vitpose")
    dev = ((vit.get("install") or {}).get("compute") or {}).get("device")
    assert dev in {"cuda", "cpu", "directml", "mps", "rocm"}
    assert "CPU" not in vit["display_name"], "display_name nao deve fixar dispositivo"


def test_import_direto_das_apis_nao_quebra() -> None:
    """Importar os modulos de API ANTES do app nao pode dar import circular."""
    for modulo in ("server.install_api", "server.refine_api"):
        r = subprocess.run([sys.executable, "-c", f"import {modulo}"],
                           cwd=str(ROOT), capture_output=True, text=True)
        assert r.returncode == 0, f"{modulo}: {r.stderr[-400:]}"
