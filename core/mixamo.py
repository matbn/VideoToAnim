"""Contrato do esqueleto Mixamo (65 ossos) e utilitarios de quaternion/FK.

Convencao do projeto:
  - Prefixo de nome: ``mixamorig:``
  - Bind pose: T-pose
  - Eixos: Y-up, right-handed, personagem olhando para +Z
  - Unidades: metros
  - Rotacao local de rest: identidade (a orientacao mora nos offsets de translacao)

Total: 7 (nucleo) + 24*2 (bracos) + 5*2 (pernas) = 65 ossos.
"""
from __future__ import annotations

import numpy as np

PREFIX = "mixamorig:"

# ---------------------------------------------------------------------------
# Tabela de ossos: (nome, pai, offset local em metros, e_end_cap)
# ---------------------------------------------------------------------------
_CORE = [
    ("Hips", None, (0.0, 0.98, 0.0), False),
    ("Spine", "Hips", (0.0, 0.10, 0.0), False),
    ("Spine1", "Spine", (0.0, 0.12, 0.0), False),
    ("Spine2", "Spine1", (0.0, 0.13, 0.0), False),
    ("Neck", "Spine2", (0.0, 0.15, 0.0), False),
    ("Head", "Neck", (0.0, 0.08, 0.0), False),
    ("HeadTop_End", "Head", (0.0, 0.18, 0.0), True),
]

_ARM_LEFT = [
    ("LeftShoulder", "Spine2", (0.03, 0.06, 0.0), False),
    ("LeftArm", "LeftShoulder", (0.14, 0.0, 0.0), False),
    ("LeftForeArm", "LeftArm", (0.28, 0.0, 0.0), False),
    ("LeftHand", "LeftForeArm", (0.26, 0.0, 0.0), False),
]

_FINGER_BASE_LEFT = {
    "Thumb": (0.075, 0.0, 0.020),
    "Index": (0.080, 0.0, 0.012),
    "Middle": (0.082, 0.0, 0.0),
    "Ring": (0.078, 0.0, -0.012),
    "Pinky": (0.072, 0.0, -0.022),
}
_FINGER_SCALES = (1.0, 0.62, 0.55, 0.45)

_LEG_LEFT = [
    ("LeftUpLeg", "Hips", (0.09, -0.05, 0.0), False),
    ("LeftLeg", "LeftUpLeg", (0.0, -0.44, 0.0), False),
    ("LeftFoot", "LeftLeg", (0.0, -0.42, 0.0), False),
    ("LeftToeBase", "LeftFoot", (0.0, -0.07, 0.10), False),
    ("LeftToe_End", "LeftToeBase", (0.0, 0.0, 0.08), True),
]


def _mirror(name: str) -> str:
    if name.startswith("Left"):
        return "Right" + name[4:]
    if name.startswith("Right"):
        return "Left" + name[4:]
    return name


def _build_bones() -> list[tuple[str, str | None, tuple[float, float, float], bool]]:
    bones: list[tuple[str, str | None, tuple[float, float, float], bool]] = list(_CORE)
    for name, parent, off, _end in _ARM_LEFT:
        bones.append((name, parent, off, False))
    for finger, base in _FINGER_BASE_LEFT.items():
        parent = "LeftHand"
        for i, sc in enumerate(_FINGER_SCALES, start=1):
            bname = f"LeftHand{finger}{i}"
            off = tuple(round(base[k] * sc, 5) for k in range(3))
            is_end = i == len(_FINGER_SCALES)
            bones.append((bname, parent, off, is_end))
            parent = bname
    for name, parent, off, is_end in _LEG_LEFT:
        bones.append((name, parent, off, is_end))
    # lado direito
    mirrored: list[tuple[str, str | None, tuple[float, float, float], bool]] = []
    for name, parent, off, is_end in bones:
        if name.startswith("Left"):
            mirrored.append(
                (
                    _mirror(name),
                    _mirror(parent) if parent and parent.startswith("Left") else parent,
                    (-off[0], off[1], off[2]),
                    is_end,
                )
            )
    return bones + mirrored


BONES: list[tuple[str, str | None, tuple[float, float, float], bool]] = _build_bones()
BONE_NAMES: list[str] = [b[0] for b in BONES]
BONE_INDEX: dict[str, int] = {n: i for i, n in enumerate(BONE_NAMES)}
BONE_PARENT: dict[str, str | None] = {b[0]: b[1] for b in BONES}
BONE_OFFSET: dict[str, np.ndarray] = {b[0]: np.asarray(b[2], dtype=np.float64) for b in BONES}
BONE_IS_END: dict[str, bool] = {b[0]: b[3] for b in BONES}
ANIMATED_BONES: list[str] = [b[0] for b in BONES if not b[3]]
END_CAPS: list[str] = [b[0] for b in BONES if b[3]]


def assert_contract() -> None:
    """Valida o contrato do esqueleto (nomes unicos, 65 ossos, pais validos)."""
    assert len(BONES) == 65, f"esperado 65 ossos, obtido {len(BONES)}"
    assert len(set(BONE_NAMES)) == 65, "nomes de osso duplicados"
    for name, parent, _off, _end in BONES:
        if parent is not None:
            assert parent in BONE_INDEX, f"pai desconhecido de {name}: {parent}"
    roots = [n for n, p, _o, _e in BONES if p is None]
    assert roots == ["Hips"], f"raiz deve ser unica (Hips), obtido {roots}"


# ---------------------------------------------------------------------------
# Cinematica direta (FK) no espaco local
# ---------------------------------------------------------------------------
def quat_identity() -> np.ndarray:
    return np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)


def quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array(
        [
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        ],
        dtype=np.float64,
    )


def quat_conj(q: np.ndarray) -> np.ndarray:
    return np.array([-q[0], -q[1], -q[2], q[3]], dtype=np.float64)


def quat_normalize(q: np.ndarray) -> np.ndarray:
    """Normaliza quaternions no ULTIMO eixo (aceita 1 quaternion ou uma serie (T,4)).

    Cuidado historico: usar np.linalg.norm(q) sem eixo numa matriz (T,4) calcula a
    norma de Frobenius e divide a serie inteira por ela — cada linha fica com norma
    errada (bug silencioso). Este formato cobre os dois casos.
    """
    q = np.asarray(q, dtype=np.float64)
    n = np.linalg.norm(q, axis=-1, keepdims=True)
    safe = np.where(n < 1e-12, 1.0, n)
    out = q / safe
    if np.any(n < 1e-12):
        out = np.where(n < 1e-12, quat_identity(), out)
    return out


def quat_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    q = quat_normalize(q)
    v = np.asarray(v, dtype=np.float64)
    u = q[:3]
    w = q[3]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def quat_from_to(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Quaternion de arco minimo que leva a direcao ``a`` em ``b`` (ambas unitarias).

    Trata o caso degenerado de 180 graus escolhendo um eixo ortogonal estavel,
    evitando inversoes grosseiras.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return quat_identity()
    a = a / na
    b = b / nb
    d = float(np.dot(a, b))
    if d > 1.0 - 1e-9:
        return quat_identity()
    if d < -1.0 + 1e-9:
        # 180 graus: eixo ortogonal estavel
        axis = np.cross(a, np.array([1.0, 0.0, 0.0]))
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, np.array([0.0, 1.0, 0.0]))
        axis = axis / np.linalg.norm(axis)
        return np.array([axis[0], axis[1], axis[2], 0.0], dtype=np.float64)
    axis = np.cross(a, b)
    q = np.array([axis[0], axis[1], axis[2], 1.0 + d], dtype=np.float64)
    return quat_normalize(q)


def quat_to_matrix(q: np.ndarray) -> np.ndarray:
    x, y, z, w = quat_normalize(q)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array(
        [
            [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
            [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
            [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
        ],
        dtype=np.float64,
    )


def quat_sign_continuity(q_prev: np.ndarray, q: np.ndarray) -> np.ndarray:
    if np.dot(q_prev, q) < 0.0:
        return -np.asarray(q, dtype=np.float64)
    return np.asarray(q, dtype=np.float64)


# ---------------------------------------------------------------------------
# FK: rotacoes locais -> posicoes mundo das cabecas dos ossos
# ---------------------------------------------------------------------------
def fk_world(local_rot: dict[str, np.ndarray], root_translation: np.ndarray) -> dict[str, np.ndarray]:
    """Retorna posicao mundo da cabeca de cada osso."""
    world_pos: dict[str, np.ndarray] = {}
    world_rot: dict[str, np.ndarray] = {}

    def visit(name: str) -> None:
        parent = BONE_PARENT[name]
        if parent is None:
            world_rot[name] = quat_normalize(local_rot.get(name, quat_identity()))
            world_pos[name] = np.asarray(root_translation, dtype=np.float64)
            return
        visit(parent)
        # offset de rest do filho no espaco do pai
        local_off = BONE_OFFSET[name]
        rotated = quat_rotate(world_rot[parent], local_off)
        world_pos[name] = world_pos[parent] + rotated
        world_rot[name] = quat_normalize(quat_mul(world_rot[parent], local_rot.get(name, quat_identity())))

    for b in BONE_NAMES:
        visit(b)
    return world_pos


def rest_world_positions() -> dict[str, np.ndarray]:
    """Posicoes mundo de rest (T-pose, identidade), com o Hips na altura pelvica."""
    return fk_world({n: quat_identity() for n in BONE_NAMES}, BONE_OFFSET["Hips"].copy())


def rest_world_rotations() -> dict[str, np.ndarray]:
    return {n: quat_identity() for n in BONE_NAMES}


def bone_rest_directions() -> dict[str, np.ndarray]:
    """Direcao unitaria de cada osso (cabeca -> proximo no) no rest, em mundo."""
    pos = rest_world_positions()
    dirs: dict[str, np.ndarray] = {}
    for name in BONE_NAMES:
        if BONE_IS_END[name]:
            continue
        child = _first_child(name)
        if child is None:
            continue
        v = pos[child] - pos[name]
        n = np.linalg.norm(v)
        dirs[name] = v / n if n > 1e-9 else np.array([0.0, 1.0, 0.0])
    return dirs


_CHILDREN: dict[str, list[str]] = {}
for _n, _p, _o, _e in BONES:
    if _p is not None:
        _CHILDREN.setdefault(_p, []).append(_n)

# ordem de preferencia para definir a direcao do osso
_DIRECTION_CHILD: dict[str, str] = {
    "Hips": "Spine",
    "Spine": "Spine1",
    "Spine1": "Spine2",
    "Spine2": "Neck",
    "Neck": "Head",
    "Head": "HeadTop_End",
    "LeftShoulder": "LeftArm",
    "RightShoulder": "RightArm",
    "LeftUpLeg": "LeftLeg",
    "RightUpLeg": "RightLeg",
}


def _first_child(name: str) -> str | None:
    if name in _DIRECTION_CHILD:
        return _DIRECTION_CHILD[name]
    kids = _CHILDREN.get(name, [])
    return kids[0] if kids else None


# ---------------------------------------------------------------------------
# Mapeamento osso -> junta canonica COCO-17 (cabeca do osso)
# ---------------------------------------------------------------------------
BONE_TO_COCO = {
    "LeftArm": "left_shoulder",
    "RightArm": "right_shoulder",
    "LeftForeArm": "left_elbow",
    "RightForeArm": "right_elbow",
    "LeftHand": "left_wrist",
    "RightHand": "right_wrist",
    "LeftUpLeg": "left_hip",
    "RightUpLeg": "right_hip",
    "LeftLeg": "left_knee",
    "RightLeg": "right_knee",
    "LeftFoot": "left_ankle",
    "RightFoot": "right_ankle",
}
