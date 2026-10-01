# -*- coding: utf-8 -*-
"""Transplante de partes do corpo entre dois clipes (job A -> job B).

Ideia: rodar o mesmo video com backends diferentes costuma dar resultados
complementares — o braco de um job rastreia melhor que o do outro. Em vez de
escolher o job inteiro, o usuario escolhe **as partes** que quer de cada um.

Por que nao basta copiar as rotacoes LOCAIS
--------------------------------------------
`Animation.rotations` guarda a rotacao de cada osso **em relacao ao pai**. A
rotacao local do braco de A esta expressa na orientacao do ombro de A. Se o
ombro vem de B e o braco de A, colar a rotacao local faz o braco NAO ficar
igual ao de A — ele fica torto, porque o pai e outro.

Por isso o transplante acontece em **espaco de mundo**: pegamos a rotacao de
mundo do osso em A e a reexpressamos em relacao ao pai (ja transplantado) de B:

    local_novo[t] = conj(mundo_pai_novo[t]) . mundo_A[t]

O resultado e que a parte transplantada fica **exatamente** como no job de
origem, pendurada no corpo do job alvo — inclusive as juntas que NAO foram
transplantadas mas estao abaixo de uma que foi (o antebraco e a mao acompanham o
braco). Ossos acima da parte nao mudam.
"""
from __future__ import annotations

import numpy as np

from core.mixamo import (ANIMATED_BONES, BONE_PARENT, quat_conj, quat_identity,
                         quat_normalize)
from core.retarget import Animation

# Partes do corpo.
#
# `bones` sao as RAIZES da parte e `subtree` diz se os descendentes entram.
# `subtree: False` existe porque "torso" tem de ser SO a coluna: a subarvore de
# Spine2 inclui bracos, cabeca e pernas, e uma "parte" que engole o personagem
# inteiro nao e uma parte -- quem marca "torso" espera marcar so o torso.
# O quadril e a raiz do rig: a subarvore dele e o personagem todo.
BODY_PARTS: dict[str, dict] = {
    "hips":      {"label": "quadril / raiz", "bones": ["Hips"], "subtree": False, "root": True},
    "torso":     {"label": "torso", "bones": ["Spine", "Spine1", "Spine2"], "subtree": False},
    "head":      {"label": "cabeca", "bones": ["Neck"], "subtree": True},
    "left_arm":  {"label": "braco esquerdo", "bones": ["LeftShoulder"], "subtree": True},
    "right_arm": {"label": "braco direito", "bones": ["RightShoulder"], "subtree": True},
    "left_leg":  {"label": "perna esquerda", "bones": ["LeftUpLeg"], "subtree": True},
    "right_leg": {"label": "perna direita", "bones": ["RightUpLeg"], "subtree": True},
}

PART_ORDER = ["hips", "torso", "head", "left_arm", "right_arm", "left_leg", "right_leg"]


def _children() -> dict[str, list[str]]:
    kids: dict[str, list[str]] = {}
    for name, parent in BONE_PARENT.items():
        kids.setdefault(parent, []).append(name)
    return kids


def subtree(root: str) -> list[str]:
    """O osso `root` e todos os descendentes."""
    kids = _children()
    out, stack = [], [root]
    while stack:
        b = stack.pop()
        out.append(b)
        stack.extend(kids.get(b, []))
    return out


def part_bones(part: str) -> list[str]:
    """Ossos de uma parte, em ordem topologica (pai antes do filho)."""
    spec = BODY_PARTS.get(part)
    if spec is None:
        raise ValueError(f"parte desconhecida: {part!r} (validas: {sorted(BODY_PARTS)})")
    bones: set[str] = set()
    for root in spec["bones"]:
        bones.update(subtree(root) if spec.get("subtree", True) else [root])
    return _topological([b for b in bones if b in ANIMATED_BONES])


def _topological(bones: list[str]) -> list[str]:
    wanted = set(bones)
    out: list[str] = []
    done: set[str] = set()

    def visit(b: str) -> None:
        if b in done or b not in wanted:
            return
        p = BONE_PARENT.get(b)
        if p is not None and p in wanted:
            visit(p)
        done.add(b)
        out.append(b)

    for b in sorted(wanted):
        visit(b)
    return out


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """quat_mul vetorizado no ultimo eixo."""
    ax, ay, az, aw = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bx, by, bz, bw = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ], axis=-1)


def _qconj(q: np.ndarray) -> np.ndarray:
    return np.stack([-q[..., 0], -q[..., 1], -q[..., 2], q[..., 3]], axis=-1)


def _locals(anim: Animation) -> dict[str, np.ndarray]:
    T = anim.num_frames
    ident = np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (T, 1))
    return {b: np.asarray(anim.rotations.get(b, ident), np.float64) for b in ANIMATED_BONES}


def world_rotation_series(anim: Animation) -> dict[str, np.ndarray]:
    """Rotacao de MUNDO de cada osso, por frame: {osso: (T,4)}."""
    local = _locals(anim)
    out: dict[str, np.ndarray] = {}
    for b in _topological(list(ANIMATED_BONES)):
        p = BONE_PARENT.get(b)
        out[b] = local[b] if p is None or p not in out else quat_normalize(_qmul(out[p], local[b]))
    return out


def _slerp_series(q0: np.ndarray, q1: np.ndarray, u: np.ndarray) -> np.ndarray:
    """slerp de duas series (T,4) com pesos `u` (T,)."""
    d = np.sum(q0 * q1, axis=1)
    q1 = np.where((d < 0.0)[:, None], -q1, q1)      # caminho mais curto
    d = np.clip(np.sum(q0 * q1, axis=1), -1.0, 1.0)
    th = np.arccos(d)
    st = np.sin(th)
    out = np.empty_like(q0)
    perto = st < 1e-9
    if np.any(perto):
        out[perto] = (q0[perto] * (1.0 - u[perto, None]) + q1[perto] * u[perto, None])
    longe = ~perto
    if np.any(longe):
        a = (np.sin((1.0 - u[longe]) * th[longe]) / st[longe])[:, None]
        b = (np.sin(u[longe] * th[longe]) / st[longe])[:, None]
        out[longe] = q0[longe] * a + q1[longe] * b
    return quat_normalize(out)


def resample_range(world: dict[str, np.ndarray], T_src: int,
                   src: tuple[int, int], tgt: tuple[int, int]) -> dict[str, np.ndarray]:
    """Reamostra `world` do intervalo `src` para o intervalo `tgt`.

    Devolve series (T_dest,4). `src` e `tgt` sao (inicio, fim) INCLUSIVOS; o
    tamanho pode mudar (a durations sao reamostradas por slerp no tempo).
    """
    s0, s1 = int(src[0]), int(src[1])
    t0, t1 = int(tgt[0]), int(tgt[1])
    n = t1 - t0 + 1
    out: dict[str, np.ndarray] = {}
    for bone, series in world.items():
        i0 = np.clip(np.floor(np.linspace(s0, s1, n)).astype(int), 0, T_src - 1)
        i1 = np.clip(i0 + 1, 0, T_src - 1)
        frac = (np.linspace(s0, s1, n) - i0)[:, None]
        out[bone] = _slerp_series(series[i0], series[i1], frac[:, 0])
    return out


def resample_vector(v: np.ndarray, T_src: int,
                    src: tuple[int, int], tgt: tuple[int, int]) -> np.ndarray:
    """Interpola linearmente um vetor (T,C) do intervalo `src` para `tgt`.

    (A translacao da raiz NAO pode passar por slerp/quat_normalize: normalizar
    um vetor de posicao o_destroy.)
    """
    n = int(tgt[1]) - int(tgt[0]) + 1
    pos = np.linspace(float(src[0]), float(src[1]), n)
    i0 = np.clip(np.floor(pos).astype(int), 0, T_src - 1)
    i1 = np.clip(i0 + 1, 0, T_src - 1)
    f = (pos - i0)[:, None]
    return v[i0] * (1.0 - f) + v[i1] * f


def check_compatible(target: Animation, source: Animation,
                     frame_map: dict | None = None) -> None:
    """Os dois clipes precisam ser o MESMO video, frame a frame.

    Com `frame_map` o usuario esta mapeando os intervalos na mao, entao os
    tamanhos podem diferir — o que nao pode diferir e o fps, senao as duracoes
    nao batem.
    """
    if abs(float(target.fps) - float(source.fps)) > 1e-6:
        raise ValueError(
            f"os jobs tem fps diferentes ({target.fps} vs {source.fps}); "
            f"o transplante exige o mesmo clipe")
    faltando = [b for b in ANIMATED_BONES
                if b not in target.rotations or b not in source.rotations]
    if faltando:
        raise ValueError(f"ossos ausentes nos dois clipes: {faltando[:6]}")
    if frame_map is not None:
        return
    if target.num_frames != source.num_frames:
        raise ValueError(
            f"os jobs tem numeros de frames diferentes ({target.num_frames} vs "
            f"{source.num_frames}); o transplante exige o mesmo clipe, ou use "
            f"o mapeamento de intervalo (frames da origem -> frames do destino)")


def transplant(target: Animation, source: Animation, parts: list[str],
               copy_root_translation: bool | None = None,
               frame_map: dict | None = None) -> tuple[Animation, dict]:
    """Devolve `target` com as `parts` vindas de `source`.

    `frame_map` = {"source": [s0, s1], "target": [t0, t1]} mapeia um intervalo
    da origem para um intervalo do destino (inclusive), reamostrando no tempo.
    Sem ele, o clipe inteiro e usado e os dois precisam ter o mesmo tamanho.
    """
    if not parts:
        raise ValueError("nenhuma parte selecionada")
    check_compatible(target, source, frame_map)

    grafted: set[str] = set()
    for part in parts:
        grafted.update(part_bones(part))

    src_world = world_rotation_series(source)
    work = _locals(target)
    w: dict[str, np.ndarray] = {}
    written: set[str] = set()

    T = target.num_frames
    if frame_map:
        s0, s1 = int(frame_map["source"][0]), int(frame_map["source"][1])
        t0, t1 = int(frame_map["target"][0]), int(frame_map["target"][1])
        if not (0 <= s0 <= s1 < source.num_frames):
            raise ValueError(
                f"intervalo de origem [{s0},{s1}] fora do clipe de origem "
                f"(0..{source.num_frames - 1})")
        if not (0 <= t0 <= t1 < T):
            raise ValueError(
                f"intervalo de destino [{t0},{t1}] fora do clipe de destino (0..{T - 1})")
        # so o intervalo do destino e reescrito; o resto fica intacto
        range_world = resample_range(src_world, source.num_frames, (s0, s1), (t0, t1))
        active = set(range(t0, t1 + 1))
        graft_at: dict[str, dict[int, np.ndarray]] = {b: {} for b in grafted}
        for b in grafted:
            for j, t in enumerate(range(t0, t1 + 1)):
                graft_at[b][t] = range_world[b][j]
    else:
        active = set(range(T))
        graft_at = {b: {t: src_world[b][t] for t in range(T)} for b in grafted}

    for b in _topological(list(ANIMATED_BONES)):
        p = BONE_PARENT.get(b)
        parent_world = w.get(p) if p is not None else None
        if parent_world is None:
            desejado = work[b].copy()
        else:
            # fora do intervalo transplantado o osso continua sendo do destino;
            # dentro dele entra a origem. E como o filho herda o pai, os ossos
            # NAO escolhidos abaixo de um transplantado giram junto.
            desejado = _qmul(parent_world, work[b])
        if b in grafted:
            for t in active:
                desejado[t] = graft_at[b][t]
        if p is None:
            work[b] = quat_normalize(desejado)
        else:
            work[b] = quat_normalize(_qmul(_qconj(parent_world), desejado))
        w[b] = quat_normalize(desejado)
        if (b in grafted and active) or (p is not None and p in written):
            written.add(b)

    # translacao da raiz so entra se o quadril foi escolhido
    root = np.asarray(target.root_translation, np.float64).copy()
    if copy_root_translation is None:
        copy_root_translation = BODY_PARTS.get("hips", {}).get("root") and "hips" in parts
    if copy_root_translation:
        root_src = np.asarray(source.root_translation, np.float64)
        if frame_map:
            sub = resample_vector(root_src, source.num_frames, (s0, s1), (t0, t1))
            novo = root.copy()
            novo[list(sorted(active))] = sub
            root = novo
        else:
            root = root_src.copy()

    out = Animation(
        fps=target.fps, num_frames=target.num_frames,
        bone_names=list(target.bone_names),
        rotations={b: work[b] for b in target.rotations},
        root_translation=root,
        meta={**dict(getattr(target, "meta", {}) or {}),
              "transplanted_from": sorted(parts),
              "transplant_bones": sorted(grafted)},
    )
    report = {
        "parts": list(parts),
        "grafted_bones": sorted(grafted),
        "bones_written": sorted(written),
        "frames": T,
        "copied_root_translation": bool(copy_root_translation),
        "frame_map": ({"source": [s0, s1], "target": [t0, t1]} if frame_map else None),
    }
    return out, report


def difference_report(target: Animation, result: Animation, source: Animation,
                      frame_map: dict | None = None) -> dict:
    """Mede o resultado: erro angular da parte transplantada vs a origem.

    Com `frame_map`, so o intervalo mapeado foi reescrito — medir o clipe
    inteiro daria um numero enorme e sem sentido (os frames de fora continuam
    do destino, por desenho). Quando a duracao muda, a origem e comparada na
    posicao reamostrada.
    """
    grafted = set(result.meta.get("transplant_bones", []))
    if not grafted:
        return {"mean_deg": 0.0, "max_deg": 0.0, "bones": 0, "frames": 0}
    rw = world_rotation_series(result)
    sw = world_rotation_series(source)
    if frame_map:
        t0, t1 = int(frame_map["target"][0]), int(frame_map["target"][1])
        s0, s1 = int(frame_map["source"][0]), int(frame_map["source"][1])
        n = t1 - t0 + 1
        pos = np.linspace(s0, s1, n)
        idx = np.clip(np.round(pos).astype(int), 0, source.num_frames - 1)
        frames = np.arange(t0, t1 + 1)
        sw = {b: v[idx] for b, v in sw.items()}
        rw = {b: v[frames] for b, v in rw.items()}
    else:
        frames = np.arange(result.num_frames)
    errs = []
    for b in sorted(grafted):
        dot = np.abs(np.sum(rw[b] * sw[b], axis=1))
        ang = np.degrees(2.0 * np.arccos(np.clip(dot, -1.0, 1.0)))
        errs.extend(ang.tolist())
    return {"mean_deg": float(np.mean(errs)), "max_deg": float(np.max(errs)),
            "bones": len(grafted), "frames": int(len(frames))}
