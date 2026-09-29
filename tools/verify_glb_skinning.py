"""Verifica se a malha de um GLB realmente DEFORMA com a animacao.

Motivo: um GLB pode estar estruturalmente correto (tem skin, tem animacao, abre
no three.js) e mesmo assim a malha nao se mover — por exemplo se os vertices
estiverem todos presos a ossos que nao animam (era o caso dos dedos/end-caps).

Aplica a formula de skinning do glTF 2.0 em dois instantes:

    p'(t) = sum_k  w_k * ( worldTransform(joint_k, t) @ IBM_k ) @ p

Uso:
    python tools/verify_glb_skinning.py caminho/model.glb
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.mesh import _read_accessor  # noqa: E402


def _mat_from_quat_t(q: np.ndarray, t: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    n = float(np.sqrt(x * x + y * y + z * z + w * w)) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    m = np.eye(4)
    m[:3, :3] = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])
    m[:3, 3] = t
    return m


def _sample(times: np.ndarray, values: np.ndarray, t: float) -> np.ndarray:
    if t <= times[0]:
        return values[0]
    if t >= times[-1]:
        return values[-1]
    i = int(np.searchsorted(times, t, side="right") - 1)
    a = (t - times[i]) / max(times[i + 1] - times[i], 1e-9)
    return values[i] * (1 - a) + values[i + 1] * a


def measure(path: str | Path, samples: int = 400) -> dict:
    """Mede o quanto a malha do GLB se desloca entre t=0 e t=duracao/2."""
    from pygltflib import GLTF2

    path = Path(path)
    g = GLTF2().load(str(path))
    out: dict = {"path": str(path), "ok": False}
    if not g.skins:
        out["error"] = "GLB sem skin"
        return out

    skin = g.skins[0]
    joints = list(skin.joints)
    ibm = _read_accessor(g, skin.inverseBindMatrices).reshape(-1, 4, 4).transpose(0, 2, 1)
    ibm = np.ascontiguousarray(ibm, dtype=np.float64)

    pos_parts, jnt_parts, wgt_parts, tri = [], [], [], 0
    for mesh in g.meshes or []:
        for prim in mesh.primitives:
            a = prim.attributes
            pos_parts.append(_read_accessor(g, a.POSITION).astype(np.float64))
            if prim.indices is not None:
                tri += len(_read_accessor(g, prim.indices)) // 3
            if a.JOINTS_0 is not None and a.WEIGHTS_0 is not None:
                jnt_parts.append(_read_accessor(g, a.JOINTS_0).astype(np.int64))
                wgt_parts.append(_read_accessor(g, a.WEIGHTS_0).astype(np.float64))
    if not pos_parts:
        out["error"] = "GLB sem geometria"
        return out

    pos = np.concatenate(pos_parts, 0)
    jnt = np.concatenate(jnt_parts, 0) if jnt_parts else np.zeros((len(pos), 4), np.int64)
    wgt = np.concatenate(wgt_parts, 0) if wgt_parts else np.zeros((len(pos), 4), np.float64)
    ws = wgt.sum(axis=1)

    used, counts = np.unique(jnt[wgt > 1e-6], return_counts=True)
    order = np.argsort(-counts)
    top = [{"joint": (g.nodes[joints[int(used[k])]].name if int(used[k]) < len(joints) else str(used[k])),
            "influences": int(counts[k])} for k in order[:8]]

    # osso DOMINANTE por vertice (argmax do peso) — responde "o que a malha segue?"
    dom_idx = np.argmax(wgt, axis=1)
    dom_j = jnt[np.arange(len(jnt)), dom_idx]
    dom_w = wgt[np.arange(len(wgt)), dom_idx]
    dused, dcounts = np.unique(dom_j[dom_w > 1e-6], return_counts=True)
    dorder = np.argsort(-dcounts)
    dominant = [{"joint": (g.nodes[joints[int(dused[k])]].name if int(dused[k]) < len(joints) else str(dused[k])),
                 "vertices": int(dcounts[k])} for k in dorder[:10]]
    n_distinct = int(np.sum(dcounts > 0.01 * len(pos)))
    # quantos vertices tem influencia "espalhada" (>=2 juntas relevantes)
    n_joints_per_vertex = (wgt > 0.05).sum(axis=1)

    out.update({
        "vertices": int(len(pos)), "triangles": int(tri), "joints": len(joints),
        "joints_in_use": int(len(used)),
        "unweighted": int((ws < 1e-6).sum()),
        "weights_normalized": bool(np.allclose(ws[ws > 0], 1.0, atol=1e-3)),
        "top_joints": top,
        "dominant_joints": dominant,
        "vertices_dominated_by_few": int((n_joints_per_vertex <= 1).sum()),
        "distinct_dominant_joints": n_distinct,
    })

    if not g.animations:
        out["error"] = "GLB sem animacao"
        return out

    anim = g.animations[0]
    chan: dict = {}
    for c in anim.channels:
        s = anim.samplers[c.sampler]
        chan[(c.target.node, c.target.path)] = (
            _read_accessor(g, s.input).astype(np.float64),
            _read_accessor(g, s.output).astype(np.float64),
        )
    dur = max(float(v[0][-1]) for v in chan.values())
    out["duration_s"] = round(dur, 3)

    parent: dict = {}
    for i, n in enumerate(g.nodes):
        for c in (n.children or []):
            parent[c] = i

    def world_transforms(t: float, use_animation: bool = True) -> dict:
        loc = {}
        for i, n in enumerate(g.nodes):
            tr = np.array(n.translation if n.translation else [0, 0, 0], np.float64)
            ro = np.array(n.rotation if n.rotation else [0, 0, 0, 1], np.float64)
            if use_animation:
                if (i, "translation") in chan:
                    tr = _sample(*chan[(i, "translation")], t)
                if (i, "rotation") in chan:
                    ro = _sample(*chan[(i, "rotation")], t)
            loc[i] = _mat_from_quat_t(ro, tr)
        world: dict = {}

        def visit(i: int) -> np.ndarray:
            if i in world:
                return world[i]
            p = parent.get(i)
            world[i] = loc[i] if p is None else visit(p) @ loc[i]
            return world[i]

        for i in range(len(g.nodes)):
            visit(i)
        return world

    step = max(1, len(pos) // max(1, samples))
    idx = np.arange(0, len(pos), step)
    pos_s, jnt_s, wgt_s = pos[idx], jnt[idx], wgt[idx]

    def skin_at(t: float, use_animation: bool = True) -> np.ndarray:
        w = world_transforms(t, use_animation)
        acc = np.zeros((len(pos_s), 3), np.float64)
        for k in range(4):
            m = wgt_s[:, k] > 1e-6
            if not m.any():
                continue
            rows = np.where(m)[0]
            src = jnt_s[rows, k]
            for sj in np.unique(src):
                r = rows[src == sj]
                if sj < 0 or sj >= len(joints):
                    continue
                M = w[joints[int(sj)]] @ ibm[int(sj)]
                acc[r] += wgt_s[r, k, None] * (pos_s[r] @ M[:3, :3].T + M[:3, 3])
        return acc

    a = skin_at(0.0)
    b = skin_at(dur * 0.5)
    d = np.linalg.norm(a - b, axis=1)

    # VALIDACAO INDEPENDENTE: no REST pose (sem animacao) a malha skinada tem de
    # coincidir com a geometria bruta. Pega erro de convencao (IBM x node).
    # Obs.: NAO se pode usar t=0 da animacao — o frame 0 ja e uma pose, nao o bind.
    rest = skin_at(0.0, use_animation=False)
    bind_err = float(np.abs(rest - pos_s).max())
    out["bind_pose_error_m"] = bind_err
    out["bind_pose_ok"] = bool(bind_err < 0.02)

    # RIGIDEZ: se a malha se move como corpo rigido (T-pose preservada), as distancias
    # entre vertices nao mudam. Mede a variacao relativa de alguns pares.
    rng = np.random.default_rng(7)
    n = len(a)
    if n >= 8:
        pairs = rng.integers(0, n, size=(min(4000, n * 6), 2))
        pairs = pairs[pairs[:, 0] != pairs[:, 1]]
        d0 = np.linalg.norm(a[pairs[:, 0]] - a[pairs[:, 1]], axis=1)
        d1 = np.linalg.norm(b[pairs[:, 0]] - b[pairs[:, 1]], axis=1)
        keep = d0 > 1e-4
        rel = np.abs(d1[keep] - d0[keep]) / d0[keep] if keep.any() else np.zeros(1)
        rigidity = {
            "pairs": int(keep.sum()),
            "median_rel_change": float(np.median(rel)),
            "frac_shape_preserved": float((rel < 0.02).mean()),
        }
    else:
        rigidity = {"pairs": 0, "median_rel_change": 0.0, "frac_shape_preserved": 0.0}

    # fracao de vertices de MEMBROS que se movem — metrica robusta ao fato de a
    # cabeca ficar estavel de proposito (calibracao + linha dos olhos) e o cabelo
    # ser uma fatia grande dos vertices. A saude do skinning se mede nos membros.
    limb = np.zeros(len(pos_s), bool)
    for i in range(len(pos_s)):
        k = int(np.argmax(wgt_s[i]))
        if wgt_s[i, k] <= 1e-6:
            continue
        j = int(jnt_s[i, k])
        if 0 <= j < len(joints):
            nome = (g.nodes[joints[j]].name or "").replace("mixamorig:", "")
            if nome.startswith(("LeftArm", "RightArm", "LeftForeArm", "RightForeArm",
                                "LeftHand", "RightHand",
                                "LeftUpLeg", "RightUpLeg", "LeftLeg", "RightLeg",
                                "LeftFoot", "RightFoot", "LeftToeBase", "RightToeBase")):
                limb[i] = True
    out["moved_fraction_limbs"] = float(((d > 0.01) & limb).sum() / max(int(limb.sum()), 1))

    out.update({
        "ok": True,
        "sampled": int(len(d)),
        "max_displacement_m": float(d.max()),
        "mean_displacement_m": float(d.mean()),
        "moved_fraction": float((d > 0.01).mean()),
        "deforms": bool(d.max() > 1e-4),
        "rigidity": rigidity,
        "rigid_like": bool(rigidity["frac_shape_preserved"] > 0.9),
    })
    return out


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "storage" / "jobs" / "demo" / "model.glb")
    r = measure(path)
    if not r.get("ok"):
        print(f"FALHA: {r.get('error')}")
        return 1
    print(f"arquivo        : {Path(path).name} ({Path(path).stat().st_size} bytes)")
    print(f"vertices       : {r['vertices']} · triangulos: {r['triangles']}")
    print(f"juntas (skin)  : {r['joints']} | em uso: {r['joints_in_use']}")
    print(f"pesos          : normalizados={r['weights_normalized']} | sem peso: {r['unweighted']}")
    for t in r["top_joints"]:
        print(f"   {t['joint']:28} {t['influences']:6d} influencias")
    print(f"duração        : {r['duration_s']} s")
    print("osso dominante por vertice (top 6):")
    for t in r.get("dominant_joints", [])[:6]:
        print(f"   {t['joint']:28} {t['vertices']:6d} vertices")
    from core import mixamo as _mx
    dedos = {b for b in _mx.BONE_NAMES if "Hand" in b and b[-1].isdigit()}
    suspeitos = [d["joint"].replace("mixamorig:", "") for d in r.get("dominant_joints", [])[:5]
                 if d["joint"].replace("mixamorig:", "") in dedos]
    if suspeitos:
        print(f"   !! osso(s) de dedo/end-cap entre os 3 dominantes: {suspeitos} — sintoma classico de "
              f"clusters vazios do FBX tratados como 'influencia todos'")
    else:
        print("   OK: os ossos dominantes sao de corpo (Hips/Spine/cabeca/membros), nao dedos")
    print(f"vertices com <=1 junta relevante: {r['vertices_dominated_by_few']} "
          f"| juntas dominantes distintas: {r['distinct_dominant_joints']}")
    print(f"rigidez        : forma preservada em {r['rigidity']['frac_shape_preserved'] * 100:.0f}% dos pares "
          f"(mediana de mudanca {r['rigidity']['median_rel_change'] * 100:.2f}%)")
    print(f"pose de bind   : erro max {r['bind_pose_error_m'] * 1000:.2f} mm "
          f"({'OK (skinning bate com a geometria)' if r['bind_pose_ok'] else 'FALHA: IBM/no inconsistente'})")
    if r.get("rigid_like"):
        print("   !! a malha se move como CORPO RIGIDO (forma de T-pose preservada)")
    print(f"deslocamento t0 -> t/2 ({r['sampled']} vertices): max {r['max_displacement_m']:.4f} m · "
          f"media {r['mean_displacement_m']:.4f} m · movidos >1cm: {r['moved_fraction'] * 100:.0f}%")
    if not r["deforms"]:
        print(">>> RESULTADO: a malha NAO DEFORMA (bug de skinning).")
        return 2
    print(">>> RESULTADO: a malha DEFORMA corretamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
