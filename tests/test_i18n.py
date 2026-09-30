"""i18n do log de saida: catalogo, JobStore.lang e pipeline com lang."""
from __future__ import annotations

import string

from core import i18n
from core.jobs import JobStore


def test_catalogo_completo():
    assert i18n.MESSAGES, "catalogo vazio"
    for key, tpls in i18n.MESSAGES.items():
        assert set(tpls.keys()) == {"pt", "en"}, key
        for lang, tpl in tpls.items():
            assert tpl.strip(), (key, lang)
        # os MESMOS placeholders nos dois idiomas
        campos = {p[1] for p in string.Formatter().parse(tpls["pt"]) if p[1]}
        campos_en = {p[1] for p in string.Formatter().parse(tpls["en"]) if p[1]}
        assert campos == campos_en, (key, campos ^ campos_en)


def test_norm_lang():
    assert i18n.norm_lang("en") == "en"
    assert i18n.norm_lang("EN-US") == "en"
    assert i18n.norm_lang("pt-BR") == "pt"
    assert i18n.norm_lang(None) == "pt"
    assert i18n.norm_lang("") == "pt"
    assert i18n.norm_lang("fr") == "pt"


def test_fmt():
    s = i18n.fmt("job.started", "en", backend="vitpose")
    assert "started" in s and "vitpose" in s
    assert "iniciado" in i18n.fmt("job.started", "pt", backend="x")
    assert i18n.fmt("nao.existe", "en") == "nao.existe"


def test_jobstore_guarda_o_idioma(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    jid = store.create("v.mp4", 10, "synthetic", {}, lang="en")
    assert store.get(jid)["lang"] == "en"
    jid2 = store.create("v.mp4", 10, "synthetic", {})
    assert store.get(jid2)["lang"] == "pt"


def test_pipeline_loga_no_idioma_escolhido(tmp_path, registry, sample_video):
    from core.pipeline import run_pipeline

    linhas_en: list = []
    run_pipeline(job_id="i18n_en", video_path=sample_video, backend_name="synthetic",
                 params={"fps": 12}, registry=registry, job_dir=tmp_path / "en",
                 logger=linhas_en.append, lang="en")
    assert any(l.startswith("Reading video") for l in linhas_en), linhas_en
    assert any(l.startswith("GLB generated") for l in linhas_en), linhas_en
    assert not any("Lendo video" in l for l in linhas_en)

    linhas_pt: list = []
    run_pipeline(job_id="i18n_pt", video_path=sample_video, backend_name="synthetic",
                 params={"fps": 12}, registry=registry, job_dir=tmp_path / "pt",
                 logger=linhas_pt.append)
    assert any(l.startswith("Lendo video") for l in linhas_pt), linhas_pt
