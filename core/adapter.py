"""Contrato de Adapter de backend de pose.

Todo backend concreto herda de PoseBackend (interface abstrata). O Adapter
padrao reutilizavel e ConfigurableAdapter: ele implementa todo o trabalho de
conversao para o esqueleto canonico a partir de uma AdapterConfig declarativa,
de modo que adicionar um backend normalmente e apenas preencher a configuracao
(mais a chamada de inferencia do modelo).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .canonical import NUM_JOINTS, FramePose, empty_pose


# ---------------------------------------------------------------------------
# Categorias de licenca (primeira classe: nao sao motivo para esconder um
# backend, e sim uma informacao que o usuario ve no dropdown).
# ---------------------------------------------------------------------------
LICENSE_CATEGORIES = {
    "livre":            "Livre — uso comercial permitido (MIT/Apache/BSD)",
    "nao_comercial":    "Não comercial — só pesquisa/uso acadêmico",
    "licenca_a_parte":  "Licença à parte — copyleft, share-alike ou termos próprios",
}
CAT_LIVRE = "livre"
CAT_NAO_COMERCIAL = "nao_comercial"
CAT_LICENCA_A_PARTE = "licenca_a_parte"


@dataclass
class AdapterConfig:
    """Configuracao declarativa de conversao nativo -> canonico COCO-17.

    native_layout: nomes dos keypoints na ordem do backend nativo.
    mapping:       nome nativo -> indice COCO (0..16).
    coord:         "pixel" (absoluto) ou "normalized" ([0,1]).
    y_flip:        se o backend usa y para cima, marque True.
    score_map:     nome nativo -> indice do canal de score (opcional).
    temporal:      "frame" (single-frame) ou "window" (janela temporal).
    """

    name: str
    display_name: str
    native_layout: list[str]
    mapping: dict[str, int]
    coord: str = "pixel"
    y_flip: bool = False
    score_map: dict[str, int] | None = None
    default_score: float = 1.0
    temporal: str = "frame"
    window: int = 1
    smoothing: str = "none"
    smooth_min_cutoff: float = 1.0
    smooth_beta: float = 0.02
    score_threshold: float = 0.2
    multi_person: bool = False
    license: str = ""
    license_category: str = CAT_LIVRE
    notes: str = ""

    def native_index(self) -> dict[str, int]:
        return {n: i for i, n in enumerate(self.native_layout)}

    def validate(self) -> list[str]:
        """Retorna lista de problemas de configuracao (vazia se OK)."""
        problems: list[str] = []
        if not self.name:
            problems.append("name vazio")
        if self.license_category not in LICENSE_CATEGORIES:
            problems.append(f"license_category invalida: {self.license_category}")
        if not self.mapping:
            problems.append("mapping vazio")
        idx = self.native_index()
        for nname, cidx in self.mapping.items():
            if nname not in idx:
                problems.append(f"junta nativa desconhecida no mapping: {nname}")
            if not (0 <= int(cidx) < NUM_JOINTS):
                problems.append(f"indice canonico fora de faixa: {cidx}")
        if self.coord not in ("pixel", "normalized"):
            problems.append(f"coord invalido: {self.coord}")
        if self.temporal not in ("frame", "window"):
            problems.append(f"temporal invalido: {self.temporal}")
        return problems

    def to_canonical(
        self,
        native_kp: np.ndarray,
        native_score: np.ndarray | None,
        width: int,
        height: int,
        extra: dict | None = None,
    ) -> FramePose:
        """Converte keypoints nativos para o esqueleto canonico COCO-17."""
        n = len(self.native_layout)
        kp = np.asarray(native_kp, dtype=np.float32).reshape(n, -1)
        out2d = np.zeros((NUM_JOINTS, 2), np.float32)
        outsc = np.full((NUM_JOINTS,), float(self.default_score), np.float32)
        idx = self.native_index()
        score_flat = np.asarray(native_score, dtype=np.float32).reshape(-1) if native_score is not None else None
        for nname, cidx in self.mapping.items():
            ni = idx.get(nname)
            if ni is None:
                continue
            x, y = float(kp[ni, 0]), float(kp[ni, 1])
            if self.coord == "normalized":
                x *= float(width)
                y *= float(height)
            if self.y_flip:
                y = float(height) - y
            out2d[cidx] = (x, y)
            if score_flat is not None and self.score_map and nname in self.score_map:
                si = self.score_map[nname]
                if 0 <= si < score_flat.size:
                    outsc[cidx] = score_flat[si]
        outsc = np.clip(outsc, 0.0, 1.0)
        return FramePose(out2d, outsc, None, width, height, extra or {})


class PoseBackend(ABC):
    """Interface abstrata de um backend de pose."""

    name: str = "abstract"
    display_name: str = "Abstract"
    is_default: bool = False
    source: str = "builtin"
    # backends de teste/infra nao aparecem no dropdown (mas seguem no registry)
    hidden_from_ui: bool = False
    # categoria de licenca (pode vir da AdapterConfig; aqui e o fallback)
    license_category: str = CAT_LIVRE

    def __init__(self) -> None:
        self._loaded = False

    # ---- ciclo de vida -------------------------------------------------
    def is_available(self) -> bool:
        """True se as dependencias do backend estao presentes."""
        return True

    def availability_reason(self) -> str:
        """Motivo legivel quando is_available() e False."""
        return ""

    def load(self, config: dict | None = None) -> None:
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    # ---- inferencia ----------------------------------------------------
    @abstractmethod
    def infer_video(self, frames: Sequence[np.ndarray], ctx: dict) -> list[FramePose]:
        """Recebe frames BGR (numpy HxWx3) e devolve FramePose por frame."""
        raise NotImplementedError


class ConfigurableAdapter(PoseBackend):
    """Adapter padrao reaproveitavel, dirigido por AdapterConfig.

    Subclasses implementam apenas ``infer_raw`` (a chamada ao modelo) e a
    disponibilidade. Toda a normalizacao canonica fica aqui.
    """

    adapter_config: AdapterConfig

    def __init__(self, adapter_config: AdapterConfig | None = None) -> None:
        super().__init__()
        if adapter_config is not None:
            self.adapter_config = adapter_config
        self.name = self.adapter_config.name
        self.display_name = self.adapter_config.display_name
        self.is_default = self.adapter_config.name == "vitpose"

    # hooks de extensao ---------------------------------------------------
    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        """Hook de inferencia. Deve devolver, por frame, um dict:
        {"kp": ndarray (N,2+), "score": ndarray (N,) | None}
        Retornar None para "sem pose neste frame".
        """
        raise NotImplementedError

    def postprocess(self, pose: FramePose, frame_index: int, ctx: dict) -> FramePose:
        """Hook opcional de pos-processamento por frame (ex.: limpeza)."""
        return pose

    # implementacao ------------------------------------------------------
    def infer_video(self, frames: Sequence[np.ndarray], ctx: dict) -> list[FramePose]:
        raw = self.infer_raw(frames, ctx)
        out: list[FramePose] = []
        w = int(ctx.get("width", 0))
        h = int(ctx.get("height", 0))
        for i, r in enumerate(raw):
            if r is None:
                f = empty_pose(w, h)
            else:
                f = self.adapter_config.to_canonical(
                    r.get("kp"), r.get("score"), w, h, r.get("extra")
                )
            out.append(self.postprocess(f, i, ctx))
        return out
