"""Testes do provisionamento automatico dos backends.

Garante que todo backend visivel tem plano de instalacao coerente e que a
distincao auto / pip-heavy / manual e honesta (manual tem motivo, e nao tenta
instalar).
"""
from __future__ import annotations

from core.provision import KIND_LABEL, PLANS, plan_for, run_plan
from core.registry import build_registry

TIPOS_VALIDOS = {"auto", "pip-heavy", "manual"}
PASSOS_VALIDOS = {"pip", "git", "download", "note"}


def test_todo_backend_visivel_tem_plano(registry):
    for rec in registry.list():
        if rec.hidden:
            continue
        assert rec.name in PLANS, f"{rec.name} sem plano de instalacao"


def test_planos_sao_bem_formados(registry):
    for rec in registry.list():
        if rec.hidden:
            continue
        plan = plan_for(rec.name)
        assert plan.kind in TIPOS_VALIDOS, rec.name
        for s in plan.steps:
            assert s.kind in PASSOS_VALIDOS, f"{rec.name}: passo {s.kind}"


def test_manual_tem_motivo_e_nao_tenta_instalar():
    manuais = [n for n, p in PLANS.items() if p.kind == "manual"]
    assert manuais, "deve haver backends marcados como manuais"
    for nome in manuais:
        plan = plan_for(nome)
        assert plan.manual_reason, f"{nome} e manual mas nao explica o motivo"
        assert not plan.steps, f"{nome} e manual mas tem passos"
        rep = run_plan(nome)                      # nao deve executar nada
        assert rep["ok"] is False
        assert rep["results"] == []
        assert rep["manual_reason"]


def test_auto_tem_passos_e_estimativa():
    for nome, plan in PLANS.items():
        if plan.kind == "auto":
            assert plan.steps, f"{nome} e 'auto' mas nao tem passos"
            assert all(s.kind in ("pip", "git", "download", "note") for s in plan.steps)


def test_todo_kind_tem_rotulo():
    for plan in PLANS.values():
        assert plan.kind in KIND_LABEL, plan.backend


def test_api_publica_expoe_instalacao(registry):
    for rec in registry.list():
        if rec.hidden:
            continue
        d = rec.as_public_dict()["install"]
        assert d["kind"] in TIPOS_VALIDOS
        assert isinstance(d["installable"], bool)
        assert d["label"]
        if d["installable"]:
            assert not d["manual_reason"]
        else:
            assert d["manual_reason"], f"{rec.name} nao instalavel sem motivo"


def test_planos_cobrem_os_backends_esperados():
    esperados = {"vitpose", "mediapipe", "rtmpose", "motionbert", "yolopose",
                 "sam3dbody", "wham", "openpose", "simplebaseline", "mhformer"}
    assert esperados <= set(PLANS)
    # os que ja rodam nesta maquina devem ser 'auto'
    for nome in ("vitpose", "mediapipe", "rtmpose"):
        assert PLANS[nome].kind == "auto"


def test_backends_offline_dos_testes_continuam_disponiveis(registry):
    """Provisionar nao pode quebrar o que ja funcionava."""
    for nome in ("synthetic", "vitpose", "mediapipe", "rtmpose"):
        rec = registry.get(nome)
        if rec is None:
            continue
        assert rec.available, f"{nome} deveria seguir disponivel"
