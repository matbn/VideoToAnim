"""Anticolisao: detecta e corrige auto-penetracao entre membros da malha.

Cada osso animado e aproximado por uma capsula: o segmento
``cabeca(osso) -> cabeca(filho de direcao)`` mais um raio. Um conjunto curado
de pares de capsulas (ex.: antebraco x tronco) nao deve se interpenetrar; se a
distancia entre os eixos cair abaixo de ``rA + rB - pen_min``, o osso-raiz do
membro (ombro/quadril) e girado por um arco minimo que afasta o membro do osso
invadido -- sempre pelo lado MAIS PROXIMO (frente/costas/lateral emergem do
sinal do proprio vetor de aproximacao).

Deteccao de tamanhos ANTES de cada execucao (requisito)
-------------------------------------------------------
O rig do Mixamo tem formato padrao, mas cada malha tem COMPRIMENTOS DE OSSO
proprios. Por isso ``detect_sizes`` roda de novo a cada execucao: com malha
anexada, le os comprimentos reais do arquivo da malha (via
``core.mesh.mesh_bone_lengths``) e deriva a escala global (mediana das razoes
contra o rig de referencia, limitada a [0.5, 2.0]); sem malha, usa o rig de
referencia (escala 1.0). Raios, ``pen_min`` e limites de correcao escalam
juntos, e o resultado sai no relatorio em ``detected``.

Ordem no refino: constraints -> anticolisao -> filtros -> edicoes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core import mixamo as mx
from core.mixamo import (
    BONE_OFFSET,
    BONE_PARENT,
    quat_conj,
    quat_from_to,
    quat_identity,
    quat_mul,
    quat_normalize,
    quat_rotate,
)
from core.refine.constraints import quat_angle_deg, slerp

_IDENT = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)

# Raio nominal (m) de cada capsula no rig de REFERENCIA (1.74 m).
# Ossos longos: ~0.13-0.17 do comprimento; tronco/cabeca/mao por massa
# (calibrado tambem com a mediana/p90 da distancia vertice->eixo medidos no
# MixamoChar de benchmark).
REF_RADII: dict[str, float] = {
    "Hips": 0.110,
    "Spine": 0.110,
    "Spine1": 0.110,
    "Spine2": 0.110,
    "Neck": 0.040,
    "Head": 0.090,
    "LeftArm": 0.045, "RightArm": 0.045,
    "LeftForeArm": 0.040, "RightForeArm": 0.040,
    "LeftHand": 0.040, "RightHand": 0.040,
    "LeftUpLeg": 0.075, "RightUpLeg": 0.075,
    "LeftLeg": 0.050, "RightLeg": 0.050,
    "LeftFoot": 0.045, "RightFoot": 0.045,
}

# Ossos usados na escala global (tronco + membros; mao/pe/dedos ficam de fora
# porque a escolha do "filho de direcao" torna a razao ruidosa).
GLOBAL_SCALE_BONES = [
    "Hips", "Spine", "Spine1", "Spine2", "Neck", "Head",
    "LeftArm", "RightArm", "LeftForeArm", "RightForeArm",
    "LeftUpLeg", "RightUpLeg", "LeftLeg", "RightLeg",
]


def _ref_lengths() -> dict[str, float]:
    out: dict[str, float] = {}
    for bone in mx.BONE_NAMES:
        if mx.BONE_IS_END.get(bone):
            continue
        child = mx._first_child(bone)
        if child is None:
            continue
        out[bone] = float(np.linalg.norm(BONE_OFFSET[child]))
    return out


REF_LENGTHS: dict[str, float] = _ref_lengths()


def detect_sizes(mesh_lengths: dict[str, float] | None = None,
                 size_mode: str = "global") -> dict:
    """Deteccao de tamanhos que roda ANTES de cada execucao da anticolisao.

    `mesh_lengths`: comprimentos (m) por osso lidos do arquivo da malha
    (``core.mesh.mesh_bone_lengths``). Sem malha, cai no rig de referencia.

    Devolve dict serializavel: source, size_mode, global_scale, ratios,
    lengths e radii (raios efetivos das capsulas).
    """
    ratios: dict[str, float] = {}
    if mesh_lengths:
        for bone, comp in mesh_lengths.items():
            ref = REF_LENGTHS.get(bone)
            if ref and ref > 1e-9 and comp > 1e-6:
                ratios[bone] = float(comp) / ref
    if not ratios:
        return {
            "source": "reference",
            "size_mode": size_mode,
            "global_scale": 1.0,
            "bones_detected": 0,
            "ratios": {},
            "lengths": {b: round(v, 5) for b, v in REF_LENGTHS.items()},
            "radii": {b: float(v) for b, v in REF_RADII.items()},
        }
    gs_bones = [b for b in GLOBAL_SCALE_BONES if b in ratios] or list(ratios)
    gs = float(np.clip(float(np.median([ratios[b] for b in gs_bones])), 0.5, 2.0))
    radii: dict[str, float] = {}
    for bone, radius in REF_RADII.items():
        fator = gs
        if size_mode == "per_bone" and bone in ratios:
            fator = float(np.clip(ratios[bone], 0.55, 1.8))
        radii[bone] = float(radius * fator)
    return {
        "source": "mesh",
        "size_mode": size_mode,
        "global_scale": round(gs, 4),
        "bones_detected": len(ratios),
        "ratios": {b: round(v, 4) for b, v in sorted(ratios.items())},
        "lengths": {b: round(v, 5) for b, v in sorted(mesh_lengths.items()) if b in ratios},
        "radii": {b: round(v, 5) for b, v in radii.items()},
    }


# ---------------------------------------------------------------------------
# Geometria: FK por frame, distancia segmento-segmento, osso-raiz do membro
# ---------------------------------------------------------------------------
def _fk_frame(local: dict[str, np.ndarray], root_t,
              offsets: dict[str, np.ndarray] | None = None) -> tuple[dict, dict]:
    """FK de UMA pose: posicoes e rotacoes mundo da cabeca de cada osso.

    Mesma convencao de ``core.mixamo.fk_world`` (rotacoes locais = deltas do
    rest; posicao do filho = pai + rot(pai) * offset), mas devolvendo TAMBEM as
    rotacoes mundo, necessarias para converter a correcao (mundo) para local.

    `offsets`: offsets de rest POR OSSO (mundo) — permite rodar a anticolisao
    no esqueleto DA PROPRIA MALHA (pivos proprios); default = rig de referencia.
    """
    off_map = BONE_OFFSET if offsets is None else offsets
    pos: dict[str, np.ndarray] = {}
    rot: dict[str, np.ndarray] = {}
    for name in mx.BONE_NAMES:
        parent = BONE_PARENT[name]
        lr = local.get(name)
        lr = quat_identity() if lr is None else quat_normalize(lr)
        if parent is None:
            pos[name] = np.asarray(root_t, np.float64)
            rot[name] = lr
        else:
            off = np.asarray(off_map[name], np.float64)
            pos[name] = pos[parent] + quat_rotate(rot[parent], off)
            rot[name] = quat_normalize(quat_mul(rot[parent], lr))
    return pos, rot


def _seg_of(name: str, pos: dict[str, np.ndarray]):
    child = mx._first_child(name)
    if child is None or name not in pos or child not in pos:
        return None
    return pos[name], pos[child]


def _seg_seg_distance(p1, q1, p2, q2):
    """Distancia minima entre os segmentos [p1,q1] e [p2,q2] (Ericson 5.1.9).

    Devolve (d, c1, c2): a distancia e os pontos de maxima aproximacao.
    """
    d1 = q1 - p1
    d2 = q2 - p2
    r = p1 - p2
    a = float(d1 @ d1)
    e = float(d2 @ d2)
    f = float(d2 @ r)
    c = float(d1 @ r)
    b = float(d1 @ d2)
    den = a * e - b * b
    s = float(np.clip((b * f - c * e) / den, 0.0, 1.0)) if den > 1e-12 else 0.0
    if e < 1e-12:
        c1 = p1 + s * d1
        return float(np.linalg.norm(c1 - p2)), c1, p2
    t = (b * s + f) / e
    if t < 0.0:
        t = 0.0
        s = float(np.clip(-c / a, 0.0, 1.0)) if a > 1e-12 else 0.0
    elif t > 1.0:
        t = 1.0
        s = float(np.clip((b - c) / a, 0.0, 1.0)) if a > 1e-12 else 0.0
    c1 = p1 + s * d1
    c2 = p2 + t * d2
    return float(np.linalg.norm(c1 - c2)), c1, c2


def _limb_root(bone: str) -> str | None:
    """Osso-raiz do membro que pode girar para resolver a colisao."""
    for side in ("Left", "Right"):
        if bone.startswith(side):
            tail = bone[len(side):]
            if tail in ("Arm", "ForeArm", "Hand"):
                return side + "Arm"
            if tail in ("UpLeg", "Leg", "Foot", "ToeBase"):
                return side + "UpLeg"
            return None
    return None


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
@dataclass
class CapsulePair:
    a: str
    b: str
    pen_min: float
    enabled: bool = True
    mode: str = "fix"        # fix | warn (warn so registra, nao corrige)
    movers: str = "a"        # a | b | both (quais membros giram)

    @property
    def key(self) -> str:
        return f"{self.a} x {self.b}"


def _default_pairs() -> list[CapsulePair]:
    pairs: list[CapsulePair] = []

    def add(a: str, b: str, pen: float, **kw):
        pairs.append(CapsulePair(a, b, pen, **kw))

    for side in ("Left", "Right"):
        # braco x tronco (mesmo lado) - casos classicos de retarget
        add(f"{side}ForeArm", "Spine", 0.025)
        add(f"{side}ForeArm", "Spine1", 0.025)
        add(f"{side}ForeArm", "Spine2", 0.025)
        add(f"{side}ForeArm", "Hips", 0.030)
        add(f"{side}Hand", "Hips", 0.040)
        add(f"{side}Hand", "Spine", 0.045)
        add(f"{side}Arm", "Spine2", 0.030)
        add(f"{side}Arm", "Neck", 0.020)
        add(f"{side}Arm", "Head", 0.025)
        # opcionais (default OFF): contato legitimo e comum no dia a dia
        add(f"{side}ForeArm", f"{side}UpLeg", 0.050, enabled=False)
        add(f"{side}ForeArm", f"{side}Leg", 0.050, enabled=False)
        add(f"{side}Hand", f"{side}UpLeg", 0.055, enabled=False)
        add(f"{side}Hand", f"{side}Leg", 0.055, enabled=False)
        add(f"{side}Hand", "Spine1", 0.050, enabled=False)
        add(f"{side}Hand", "Head", 0.040, enabled=False)
        add(f"{side}Hand", "Neck", 0.030, enabled=False)
        add(f"{side}ForeArm", "Neck", 0.030, enabled=False)
    # braco x perna (cruzado)
    for s, o in (("Left", "Right"), ("Right", "Left")):
        add(f"{s}ForeArm", f"{o}UpLeg", 0.030)
        add(f"{s}ForeArm", f"{o}Leg", 0.030)
        add(f"{s}Hand", f"{o}UpLeg", 0.030)
        add(f"{s}Hand", f"{o}Leg", 0.030)
        add(f"{s}Arm", f"{o}UpLeg", 0.030)
    # perna x perna (L x R)
    add("LeftUpLeg", "RightUpLeg", 0.020, movers="both")
    add("LeftUpLeg", "RightLeg", 0.020, movers="both")
    add("RightUpLeg", "LeftLeg", 0.020, movers="both")
    add("LeftLeg", "RightLeg", 0.020, movers="both")
    add("LeftLeg", "RightFoot", 0.020, movers="both")
    add("RightLeg", "LeftFoot", 0.020, movers="both")
    add("LeftFoot", "RightFoot", 0.020, movers="both")
    # braco x braco (L x R): abraco e legitimo -> somente AVISO
    add("LeftForeArm", "RightForeArm", 0.060, mode="warn", movers="both")
    add("LeftHand", "RightHand", 0.060, mode="warn", movers="both")
    add("LeftArm", "RightArm", 0.060, mode="warn", movers="both")
    return pairs


@dataclass
class CollisionConfig:
    enabled: bool = True
    size_mode: str = "global"     # global | per_bone
    damping: float = 0.6
    passes: int = 2
    max_correction_m: float = 0.03
    max_deg_per_frame: float = 14.0
    pairs: list[CapsulePair] = field(default_factory=_default_pairs)

    def validate(self) -> list[str]:
        errs: list[str] = []
        if self.size_mode not in ("global", "per_bone"):
            errs.append(f"size_mode invalido: {self.size_mode!r} (use global/per_bone)")
        if not (0.0 < float(self.damping) <= 1.0):
            errs.append(f"damping fora de (0,1]: {self.damping}")
        if not (1 <= int(self.passes) <= 5):
            errs.append(f"passes fora de [1,5]: {self.passes}")
        if float(self.max_correction_m) <= 0:
            errs.append("max_correction_m deve ser > 0")
        if not (0.0 < float(self.max_deg_per_frame) <= 60.0):
            errs.append(f"max_deg_per_frame fora de (0,60]: {self.max_deg_per_frame}")
        for p in self.pairs:
            for bone in (p.a, p.b):
                if bone not in mx.BONE_INDEX:
                    errs.append(f"par {p.key}: osso inexistente {bone!r}")
                elif bone not in REF_RADII:
                    errs.append(f"par {p.key}: osso sem raio definido ({bone})")
            if p.pen_min < 0:
                errs.append(f"par {p.key}: pen_min negativo")
            if p.mode not in ("fix", "warn"):
                errs.append(f"par {p.key}: mode invalido {p.mode!r}")
            if p.movers not in ("a", "b", "both"):
                errs.append(f"par {p.key}: movers invalido {p.movers!r}")
            if p.mode == "fix" and not any(_limb_root(x) for x in (p.a, p.b)):
                errs.append(f"par {p.key}: nenhum lado tem osso-raiz de membro")
        return errs

    def to_dict(self) -> dict:
        return {
            "enabled": bool(self.enabled),
            "size_mode": self.size_mode,
            "damping": float(self.damping),
            "passes": int(self.passes),
            "max_correction_m": float(self.max_correction_m),
            "max_deg_per_frame": float(self.max_deg_per_frame),
            "pairs": [
                {"a": p.a, "b": p.b, "pen_min": float(p.pen_min),
                 "enabled": bool(p.enabled), "mode": p.mode, "movers": p.movers}
                for p in self.pairs
            ],
        }

    @staticmethod
    def from_dict(d: dict) -> "CollisionConfig":
        cfg = CollisionConfig()
        cfg.enabled = bool(d.get("enabled", cfg.enabled))
        cfg.size_mode = str(d.get("size_mode", cfg.size_mode))
        cfg.damping = float(d.get("damping", cfg.damping))
        cfg.passes = int(d.get("passes", cfg.passes))
        cfg.max_correction_m = float(d.get("max_correction_m", cfg.max_correction_m))
        cfg.max_deg_per_frame = float(d.get("max_deg_per_frame", cfg.max_deg_per_frame))
        items = d.get("pairs")
        if items:
            by_key = {(p.a, p.b): p for p in cfg.pairs}
            out: list[CapsulePair] = list(cfg.pairs)
            for item in items:
                a, b = str(item["a"]), str(item["b"])
                cur = by_key.get((a, b))
                if cur is None:
                    cur = CapsulePair(a, b, float(item.get("pen_min", 0.03)))
                    out.append(cur)
                    by_key[(a, b)] = cur
                if "pen_min" in item:
                    cur.pen_min = float(item["pen_min"])
                if "enabled" in item:
                    cur.enabled = bool(item["enabled"])
                if "mode" in item:
                    cur.mode = str(item["mode"])
                if "movers" in item:
                    cur.movers = str(item["movers"])
            cfg.pairs = out
        return cfg

    @staticmethod
    def load(path: str | Path) -> "CollisionConfig":
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if str(p).lower().endswith((".yaml", ".yml")):
            import yaml
            data = yaml.safe_load(text) or {}
        else:
            import json
            data = json.loads(text or "{}")
        cfg = CollisionConfig.from_dict(data)
        errs = cfg.validate()
        if errs:
            raise ValueError("config de anticolisao invalida:\n  - " + "\n  - ".join(errs))
        return cfg

    def save(self, path: str | Path, header: str = "") -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            import yaml
            body = yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False)
        except Exception:  # pragma: no cover
            import json
            body = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        p.write_text(header + body, encoding="utf-8", newline="\n")


_HDR = (
    "# Anticolisao (VideoToAnim)\n"
    "#\n"
    "# Capsulas por osso (segmento cabeca->filho de direcao + raio) com pares\n"
    "# curados que nao devem se interpenetrar. A cada execucao os tamanhos sao\n"
    "# re-detectados da malha anexa (o formato do rig Mixamo e padrao, mas cada\n"
    "# personagem tem comprimentos de osso proprios) e raios/pen_min escalam.\n"
    "#\n"
    "# mode:    fix  -> corrige girando o osso-raiz do membro (ombro/quadril)\n"
    "#          warn -> apenas registra aviso no relatorio (ex.: abraco)\n"
    "# movers:  a | b | both - quais membros giram para resolver o par\n"
    "# enabled: false desliga o par\n"
    "#\n"
    "# size_mode: global (escala unica pela mediana das razoes) | per_bone\n"
    "\n"
)


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "collision_default.yaml"


def load_default_config(path: str | Path | None = None) -> CollisionConfig:
    p = Path(path) if path else default_config_path()
    if not p.exists():
        cfg = CollisionConfig()
        cfg.save(p, header=_HDR)
        return cfg
    return CollisionConfig.load(p)


# ---------------------------------------------------------------------------
# Aplicacao
# ---------------------------------------------------------------------------
def _correct_frame(rots_t: dict, root_t, pairs: list[CapsulePair],
                   radii: dict[str, float], pen_scale: float,
                   cfg: CollisionConfig, writable: set[str], offsets=None,
                   side_hint: dict[str, float] | None = None):
    """Corrige UMA frame (in-place em `rots_t`). Devolve (changed, stats).

    `side_hint`: membro-raiz -> lado esperado no eixo de frente (+1 = frente,
    -1 = atras). Quando presente, a correcao NAO empurra o membro para o lado
    oposto (o "empurra pro lado mais proximo" ignora a continuidade temporal e
    pode trocar o braco de lado entre frames — relatado como braco indo para
    tras no video em que ele esta claramente na frente)."""
    changed: dict[str, np.ndarray] = {}
    stats = {"triggered": {}, "residual": 0.0, "max_deg": 0.0, "applied": 0}
    max_cap_m = float(cfg.max_correction_m) * pen_scale
    for _pass in range(max(1, int(cfg.passes))):
        pos, rot = _fk_frame(rots_t, root_t, offsets)
        moves: dict[str, list] = {}
        for pair in pairs:
            sa = _seg_of(pair.a, pos)
            sb = _seg_of(pair.b, pos)
            if sa is None or sb is None:
                continue
            d, c1, c2 = _seg_seg_distance(sa[0], sa[1], sb[0], sb[1])
            pen = (radii[pair.a] + radii[pair.b]) - d
            pen_min = pair.pen_min * pen_scale
            if pen <= pen_min + 1e-9:
                continue
            prev = stats["triggered"].get(pair.key, 0.0)
            stats["triggered"][pair.key] = max(prev, float(pen))
            if pair.mode == "warn":
                continue
            if d < 1e-6:
                n = np.asarray(sa[1], np.float64) - np.asarray(c2, np.float64)
                nn = float(np.linalg.norm(n))
                n = n / nn if nn > 1e-9 else np.array([0.0, 0.0, 1.0])
            else:
                n = (c1 - c2) / d
            delta = n * ((pen - pen_min) * float(cfg.damping))
            mag = float(np.linalg.norm(delta))
            if mag > max_cap_m > 0:
                delta = delta * (max_cap_m / mag)
            movers = ("a", "b") if pair.movers == "both" else (pair.movers,)
            for mover in movers:
                bone = pair.a if mover == "a" else pair.b
                root = _limb_root(bone)
                if root is None or root not in writable:
                    continue
                pivot = np.asarray(pos[root], np.float64)
                cpt = c1 if mover == "a" else c2
                r_vec = np.asarray(cpt, np.float64) - pivot
                if float(np.linalg.norm(r_vec)) < 1e-6:
                    continue
                dv = delta if mover == "a" else -delta
                if side_hint:
                    lado = side_hint.get(root)
                    if lado:
                        dvz = float(dv[2])
                        if dvz * lado < 0:      # iria contra o lado do membro
                            dv = dv - np.array([0.0, 0.0, dvz])
                moves.setdefault(root, []).append((r_vec, dv))
        if not moves:
            break
        for root, lst in moves.items():
            q_tot = None
            for r_vec, dv in lst:
                qc = quat_from_to(r_vec, r_vec + dv)
                q_tot = qc if q_tot is None else quat_mul(q_tot, qc)
            if q_tot is None:
                continue
            ang = quat_angle_deg(q_tot)
            lim = float(cfg.max_deg_per_frame)
            if ang > lim > 0:
                q_tot = slerp(_IDENT, q_tot, lim / max(ang, 1e-9))
                ang = lim
            parent = BONE_PARENT[root]
            wr_parent = rot[parent] if parent is not None else _IDENT
            w_new = quat_mul(q_tot, rot[root])
            rots_t[root] = quat_normalize(quat_mul(quat_conj(wr_parent), w_new))
            changed[root] = rots_t[root]
            stats["applied"] += 1
            stats["max_deg"] = max(stats["max_deg"], float(ang))
    pos, _rot = _fk_frame(rots_t, root_t, offsets)
    for pair in pairs:
        sa = _seg_of(pair.a, pos)
        sb = _seg_of(pair.b, pos)
        if sa is None or sb is None:
            continue
        d, _c1, _c2 = _seg_seg_distance(sa[0], sa[1], sb[0], sb[1])
        pen = (radii[pair.a] + radii[pair.b]) - d
        stats["residual"] = max(stats["residual"], float(pen))
    return changed, stats


def apply_collision(anim, *, config: "CollisionConfig | dict | None" = None,
                    mesh_lengths: dict[str, float] | None = None,
                    skeleton_offsets: dict | None = None,
                    side_history: dict[str, float] | None = None):
    """Aplica a anticolisao a uma `Animation`. Devolve (Animation, relatorio).

    A deteccao de tamanhos roda AQUI, a cada chamada (requisito): se
    `mesh_lengths` vier da malha anexa, raios e tolerancias escalam por ela.

    `side_history`: {"LeftArm": +1|-1, "RightArm": +1|-1} — lado (frente/atrás)
    conhecido de cada MEMBRO (por ex., do frame anterior ou da malha no rest).
    Presente, impede que a correcao troque o braco de lado entre frames.
    """
    from core.retarget import Animation

    if isinstance(config, CollisionConfig):
        cfg = config
    elif isinstance(config, dict):
        cfg = CollisionConfig.from_dict(config)
    else:
        cfg = CollisionConfig()
    errs = cfg.validate()
    if errs:
        raise ValueError("config de anticolisao invalida:\n  - " + "\n  - ".join(errs))

    det = detect_sizes(mesh_lengths, cfg.size_mode)
    pen_scale = float(det["global_scale"])
    radii = {k: float(v) for k, v in det["radii"].items()}

    out_rot = {k: np.asarray(v, np.float64).copy() for k, v in anim.rotations.items()}
    rt = getattr(anim, "root_translation", None)
    root_tr = np.asarray(rt, np.float64) if rt is not None else np.zeros((0, 3), np.float64)
    T = int(anim.num_frames)

    report: dict = {
        "enabled": bool(cfg.enabled),
        "detected": det,
        "skeleton_source": "mesh" if skeleton_offsets is not None else "reference",
        "pairs_enabled": int(sum(1 for p in cfg.pairs if p.enabled)),
        "frames_corrected_total": 0,
        "corrections_total": 0,
        "max_correction_deg": 0.0,
        "residual_max_pen_m": 0.0,
        "pairs": {},
        "warnings": {},
    }
    meta = dict(getattr(anim, "meta", {}) or {})
    meta["collision"] = {
        "size_source": det["source"],
        "global_scale": det["global_scale"],
        "frames_corrected": 0,
    }

    def _out():
        return Animation(
            fps=anim.fps, num_frames=anim.num_frames, bone_names=list(anim.bone_names),
            rotations=out_rot,
            root_translation=root_tr.copy(),
            meta=meta,
        )

    side_hint: dict[str, float] | None = None
    if side_history:
        side_hint = {k: (1.0 if v >= 0 else -1.0) for k, v in side_history.items()}

    if not cfg.enabled or T <= 0 or not out_rot:
        return _out(), report

    active = [p for p in cfg.pairs if p.enabled]
    writable = set(out_rot.keys())
    for t in range(T):
        rots_t: dict[str, np.ndarray] = {}
        for b in mx.BONE_NAMES:
            q = out_rot.get(b)
            rots_t[b] = q[t] if q is not None else _IDENT
        root_t = root_tr[t] if t < len(root_tr) else BONE_OFFSET["Hips"]
        changed, st = _correct_frame(rots_t, root_t, active, radii, pen_scale, cfg,
                                     writable, offsets=skeleton_offsets,
                                     side_hint=side_hint)
        for b, q in changed.items():
            out_rot[b][t] = q
        if changed:
            report["frames_corrected_total"] += 1
            report["corrections_total"] += int(st["applied"])
        report["max_correction_deg"] = max(report["max_correction_deg"], float(st["max_deg"]))
        report["residual_max_pen_m"] = max(report["residual_max_pen_m"], float(st["residual"]))
        for k, pen in st["triggered"].items():
            ent = report["pairs"].setdefault(k, {"frames": 0, "max_pen_m": 0.0})
            ent["frames"] += 1
            ent["max_pen_m"] = max(ent["max_pen_m"], float(pen))

    warn_keys = {p.key for p in cfg.pairs if p.mode == "warn"}
    for k in list(report["pairs"].keys()):
        if k in warn_keys:
            report["warnings"][k] = report["pairs"].pop(k)
    for ent in list(report["pairs"].values()) + list(report["warnings"].values()):
        ent["max_pen_m"] = round(float(ent["max_pen_m"]), 5)
    report["residual_max_pen_m"] = round(report["residual_max_pen_m"], 5)
    report["max_correction_deg"] = round(report["max_correction_deg"], 3)
    meta["collision"]["frames_corrected"] = report["frames_corrected_total"]
    return _out(), report
