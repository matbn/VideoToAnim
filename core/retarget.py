"""Solver de retarget: keypoints 3D canonicos (COCO-17) -> rig Mixamo.

Entrada: sequencia de FramePose com kp3d preenchido (metros, y-up).
Saida:   Animation com rotacoes locais por osso + translacao do root (Hips).

Algoritmo (por frame):
  1. Derivar as posicoes das juntas Mixamo a partir do COCO-17.
  2. Para cada osso dirigido: direcao alvo = normalize(child - head).
  3. q_world = quat_from_to(direcao_rest, direcao_alvo)  [arco minimo, anti-180].
  4. q_local = conj(q_world_pai) * q_world.
  5. Correcao de sinal (continuidade) e clamp de velocidade angular.
  6. Hips recebe translacao (posicao do quadril, origem da animacao) + rotacao.
  Ossos sem dados (dedos, end-caps) ficam em identidade.

Comprimentos de osso NUNCA sao recalculados por frame: as direcoes sao
normalizadas e o comprimento vem do offset de rest, garantindo rigidez.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import mixamo as mx
from .canonical import COCO_INDEX, FramePose


@dataclass
class Animation:
    fps: float
    num_frames: int
    bone_names: list[str]
    rotations: dict[str, np.ndarray]  # nome -> (T, 4) quaternion local (x,y,z,w)
    root_translation: np.ndarray      # (T, 3) metros
    meta: dict = field(default_factory=dict)


def _derive_joint_positions(kp3d: np.ndarray,
                            head_dir: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Posicoes (mundo) das juntas Mixamo derivadas do COCO-17 3D."""
    ci = COCO_INDEX
    left_hip = kp3d[ci["left_hip"]]
    right_hip = kp3d[ci["right_hip"]]
    left_sh = kp3d[ci["left_shoulder"]]
    right_sh = kp3d[ci["right_shoulder"]]
    nose = kp3d[ci["nose"]]
    hips = 0.5 * (left_hip + right_hip)
    chest = 0.5 * (left_sh + right_sh)

    def lerp(a, b, t):
        return a + (b - a) * t

    if head_dir is None:
        # fallback: direcao nariz->ombros (sensivel a translacao do corpo;
        # a linha dos olhos e preferida quando disponivel — ver _face_up_from_eyes)
        head_dir = nose - chest
        nd = np.linalg.norm(head_dir)
        head_dir = head_dir / nd if nd > 1e-9 else np.array([0.0, 1.0, 0.0])
    else:
        head_dir = np.asarray(head_dir, dtype=np.float64)

    j: dict[str, np.ndarray] = {
        "Hips": hips,
        "Spine": lerp(hips, chest, 0.0),
        "Spine1": lerp(hips, chest, 0.34),
        "Spine2": lerp(hips, chest, 0.67),
        "Neck": chest,
        "Head": nose,
        "HeadTop_End": nose + head_dir * 0.20,
        "LeftShoulder": chest,
        "RightShoulder": chest,
        "LeftArm": left_sh,
        "RightArm": right_sh,
        "LeftForeArm": kp3d[ci["left_elbow"]],
        "RightForeArm": kp3d[ci["right_elbow"]],
        "LeftHand": kp3d[ci["left_wrist"]],
        "RightHand": kp3d[ci["right_wrist"]],
        "LeftUpLeg": left_hip,
        "RightUpLeg": right_hip,
        "LeftLeg": kp3d[ci["left_knee"]],
        "RightLeg": kp3d[ci["right_knee"]],
        "LeftFoot": kp3d[ci["left_ankle"]],
        "RightFoot": kp3d[ci["right_ankle"]],
    }
    for side, hand, fore in (("Left", "LeftHand", "LeftForeArm"), ("Right", "RightHand", "RightForeArm")):
        d = j[hand] - j[fore]
        n = np.linalg.norm(d)
        d = d / n if n > 1e-9 else np.array([0.0, 1.0, 0.0])
        j[f"{side}HandTip"] = j[hand] + d * 0.08
    for side, foot in (("Left", "LeftFoot"), ("Right", "RightFoot")):
        toe = f"{side}ToeBase"
        j[toe] = j[foot] + np.array([0.0, -0.05, 0.10])
        j[toe + "_End"] = j[toe] + np.array([0.0, 0.0, 0.08])
    return j


# osso -> (junta_pai, junta_filha) no dicionario derivado
def _face_up_from_eyes(kp3d: np.ndarray, state: dict) -> np.ndarray | None:
    """Direcao "para cima" do rosto, derivada da linha dos olhos (no plano).

    A orientacao da cabeca NAO pode vir do vetor nariz-vs-ombros: ele muda
    quando o corpo se desloca (agachamento/giro), mesmo com a cabeca parada.
    A linha dos olhos (olhoE->olhoD) fornece a rotacao real do rosto e e
    invariante a translacao. Guardas contra frames degenerados (oclusao):
    se a distancia entre os olhos colapsar, mantem a ultima direcao valida.
    """
    ci = COCO_INDEX
    v = np.asarray(kp3d[ci["right_eye"]], dtype=np.float64) - np.asarray(kp3d[ci["left_eye"]], dtype=np.float64)
    v = v.copy()
    v[2] = 0.0                      # a rotacao do rosto interessa no plano (x, y)
    comp = float(np.linalg.norm(v[:2]))
    if comp < 1e-9:
        return state.get("up")
    ema = state.get("len", 0.0)
    if ema > 0.0 and comp < max(0.55 * ema, 0.012):
        return state.get("up")      # olhos degenerados neste frame: segura o anterior
    up = np.array([-v[1], v[0], 0.0], dtype=np.float64)
    n = float(np.linalg.norm(up))
    if n < 1e-9:
        return state.get("up")
    up = up / n
    if up[1] < 0.0:                 # "para cima" e o lado com y>0 (y-up)
        up = -up
    prev = state.get("up")
    if prev is not None:
        # suavizacao adaptativa: a rotacao do rosto e de baixa frequencia
        # (cabeca nao "sacode"); nos frames em que os olhos ficam curtos
        # (perfil/oclusao) o peso da medicao nova cai
        w = min(1.0, comp / max(ema, 1e-9)) if ema > 0.0 else 1.0
        alpha = 0.30 * w
        blended = (1.0 - alpha) * np.asarray(prev, dtype=np.float64) + alpha * up
        nb = float(np.linalg.norm(blended))
        up = blended / nb if nb > 1e-9 else up
    state["up"] = up
    state["len"] = comp if ema <= 0.0 else 0.9 * ema + 0.1 * comp
    return up


BONE_DIRECTION = {
    "Hips": ("Hips", "Spine1"),
    "Spine": ("Spine", "Spine1"),
    "Spine1": ("Spine1", "Spine2"),
    "Spine2": ("Spine2", "Neck"),
    "Neck": ("Neck", "Head"),
    "Head": ("Head", "HeadTop_End"),
    "LeftShoulder": ("LeftShoulder", "LeftArm"),
    "RightShoulder": ("RightShoulder", "RightArm"),
    "LeftArm": ("LeftArm", "LeftForeArm"),
    "RightArm": ("RightArm", "RightForeArm"),
    "LeftForeArm": ("LeftForeArm", "LeftHand"),
    "RightForeArm": ("RightForeArm", "RightHand"),
    "LeftHand": ("LeftHand", "LeftHandTip"),
    "RightHand": ("RightHand", "RightHandTip"),
    "LeftUpLeg": ("LeftUpLeg", "LeftLeg"),
    "RightUpLeg": ("RightUpLeg", "RightLeg"),
    "LeftLeg": ("LeftLeg", "LeftFoot"),
    "RightLeg": ("RightLeg", "RightFoot"),
    "LeftFoot": ("LeftFoot", "LeftToeBase"),
    "RightFoot": ("RightFoot", "RightToeBase"),
    "LeftToeBase": ("LeftToeBase", "LeftToeBase_End"),
    "RightToeBase": ("RightToeBase", "RightToeBase_End"),
}


def calibrate_head(anim: Animation, frame: int = 0,
                   bones: tuple[str, ...] = ("Neck", "Head")) -> dict:
    """Ancora a orientacao da cabeca pelo frame de referencia (calibracao).

    O solver orienta pescoco/cabeca por UM vetor estimado (nose - chest) cuja
    profundidade vem de um lifter monoculo — carrega um vies sistematico de
    dezenas de graus, e o personagem "nasce" olhando para baixo/para o lado.
    Aqui a rotacao LOCAL de Neck/Head no frame de referencia vira a identidade:
    a cabeca comeca na orientacao de repouso do rig (frente do corpo) e TODO o
    movimento relativo e preservado (q'(t1)^-1 * q'(t2) == q(t1)^-1 * q(t2)).
    """
    rep: dict = {"frame": int(frame), "bones": {}, "applied": False}
    if anim.num_frames <= 0:
        return rep
    f = int(np.clip(int(frame), 0, anim.num_frames - 1))
    rep["frame"] = f
    for short in bones:
        key = None
        for k in anim.rotations:
            if k.replace("mixamorig:", "") == short:
                key = k
                break
        if key is None:
            continue
        series = np.asarray(anim.rotations[key], np.float64)
        q_ref = mx.quat_normalize(series[f])

        # 1) zera a rotacao LOCAL no frame de referencia
        inv = mx.quat_conj(q_ref)
        out = np.empty_like(series)
        for t in range(series.shape[0]):
            out[t] = mx.quat_normalize(mx.quat_mul(inv, series[t]))

        # 2) neutraliza TAMBEM no MUNDO: a cadeia da coluna (Hips..Spine2)
        # contribui com uma rotacao propria no frame de referencia; zerar so o
        # local deixava um vies constante visivel (ex.: cabeca 24 graus de lado).
        # Multiplica-se a serie por D (offset local constante) tal que a rotacao
        # de MUNDO do osso no frame f vire identidade.
        def _world_of(name: str, t: int) -> np.ndarray:
            cadeia: list[str] = []
            n: str | None = name
            while n is not None:
                cadeia.append(n)
                n = mx.BONE_PARENT.get(n)
            w = mx.quat_identity()
            for nome2 in reversed(cadeia):
                chave = nome2
                if chave not in anim.rotations:
                    for k2 in anim.rotations:
                        if k2.replace("mixamorig:", "") == nome2:
                            chave = k2
                            break
                    else:
                        continue
                w = mx.quat_mul(w, np.asarray(anim.rotations[chave][t], np.float64))
            return w

        pai = mx.BONE_PARENT.get(short)
        w_pai = _world_of(pai, f) if pai else mx.quat_identity()
        ql_f = mx.quat_normalize(out[f])
        d_offset = mx.quat_mul(mx.quat_conj(ql_f), mx.quat_conj(w_pai))
        for t in range(out.shape[0]):
            out[t] = mx.quat_normalize(mx.quat_mul(out[t], d_offset))
        for t in range(1, out.shape[0]):   # continuidade de sinal por seguranca
            out[t] = mx.quat_sign_continuity(out[t - 1], out[t])
        anim.rotations[key] = out
        rep["bones"][short] = {
            "angle_deg": float(np.degrees(2.0 * np.arctan2(
                float(np.linalg.norm(q_ref[:3])), abs(float(q_ref[3]))))),
            "world_offset_deg": float(np.degrees(2.0 * np.arctan2(
                float(np.linalg.norm(d_offset[:3])), abs(float(d_offset[3]))))),
        }
    rep["applied"] = bool(rep["bones"])
    return rep


class Retargeter:
    def __init__(self, fps: float = 30.0, max_deg_per_frame: float = 60.0):
        self.fps = float(fps)
        self.max_deg = float(max_deg_per_frame)
        self._rest_dirs = mx.bone_rest_directions()

    def retarget(self, frames: list[FramePose]) -> Animation:
        if not frames:
            raise ValueError("nenhum frame para retarget")
        if not any(f.kp3d is not None for f in frames):
            raise ValueError("retarget exige kp3d; nenhum frame possui 3D canonico")

        T = len(frames)
        identity = np.array([0.0, 0.0, 0.0, 1.0], np.float64)
        rotations: dict[str, np.ndarray] = {
            name: np.tile(identity, (T, 1)) for name in mx.ANIMATED_BONES
        }
        root_t = np.zeros((T, 3), np.float64)

        prev_q: dict[str, np.ndarray] = {}
        prev_root: np.ndarray | None = None
        max_ang = np.deg2rad(self.max_deg)
        last_valid: FramePose | None = None
        face_state: dict = {"up": None, "len": 0.0}
        prev_dir: dict[str, np.ndarray] = {}
        prev_world: dict[str, np.ndarray] = {}

        for t, frame in enumerate(frames):
            if frame.kp3d is None:
                if last_valid is None:
                    continue
                kp3d = last_valid.kp3d
            else:
                kp3d = frame.kp3d
                last_valid = frame

            joints = _derive_joint_positions(kp3d, head_dir=_face_up_from_eyes(kp3d, face_state))
            root_pos = joints["Hips"].astype(np.float64)
            if prev_root is not None:
                root_pos = prev_root + np.clip(root_pos - prev_root, -0.2, 0.2)
            root_t[t] = root_pos
            prev_root = root_pos

            world_q: dict[str, np.ndarray] = {}
            d_root = joints["Spine1"] - joints["Hips"]
            n_root = np.linalg.norm(d_root)
            rest_root = self._rest_dirs.get("Hips", np.array([0.0, 1.0, 0.0]))
            world_q["Hips"] = mx.quat_from_to(rest_root, d_root / n_root) if n_root > 1e-9 else identity.copy()

            for bone, spec in BONE_DIRECTION.items():
                if bone == "Hips":
                    continue
                pa, ch = spec
                if pa not in joints or ch not in joints:
                    continue
                d = joints[ch] - joints[pa]
                nd = np.linalg.norm(d)
                d0 = self._rest_dirs.get(bone)
                if d0 is None:
                    continue
                d = d / nd if nd > 1e-9 else d0
                # orientacao por TRANSPORTE PARALELO: gira a orientacao ja
                # acumulada pelo minimo que leva a direcao ANTERIOR na atual.
                # A montagem absoluta a partir do repouso (from_to(d0, d))
                # acumulava twist parasita quando o osso varria um arco grande
                # (ex.: antebraco cruzando o peito girava ~180 sem o braco
                # mexer); o transporte por frame elimina esse efeito.
                pd = prev_dir.get(bone)
                pw = prev_world.get(bone)
                if pd is not None and pw is not None and float(np.dot(pd, d)) > -0.9995:
                    world_q[bone] = mx.quat_mul(mx.quat_from_to(pd, d), pw)
                else:
                    world_q[bone] = mx.quat_from_to(d0, d)
                prev_dir[bone] = d
                prev_world[bone] = world_q[bone]

            # mundo -> local
            for bone in mx.ANIMATED_BONES:
                if bone not in world_q:
                    continue
                parent = mx.BONE_PARENT[bone]
                if parent is None or parent not in world_q:
                    q_l = world_q[bone]
                else:
                    q_l = mx.quat_mul(mx.quat_conj(world_q[parent]), world_q[bone])
                q_l = mx.quat_normalize(q_l)
                if bone in prev_q:
                    q_l = mx.quat_sign_continuity(prev_q[bone], q_l)
                    dot = abs(float(np.dot(prev_q[bone], q_l)))
                    ang = 2.0 * np.arccos(np.clip(dot, -1.0, 1.0))
                    if ang > max_ang and ang > 1e-9:
                        s = max_ang / ang
                        q_l = mx.quat_normalize(prev_q[bone] * (1 - s) + q_l * s)
                rotations[bone][t] = q_l
                prev_q[bone] = q_l

        meta = {"fps": self.fps, "bones": len(mx.BONE_NAMES), "animated": len(mx.ANIMATED_BONES)}
        return Animation(self.fps, T, list(mx.BONE_NAMES), rotations, root_t, meta)


def fk_validation_error(anim: Animation, frames: list[FramePose]) -> dict:
    """FK reverso: reaplica FK e compara com as juntas alvo.

    Retorna erro posicional (m) e angular (graus) por junta e agregados.
    """
    per_joint_pos: dict[str, list[float]] = {}
    per_joint_ang: dict[str, list[float]] = {}
    root_t = anim.root_translation

    for t, frame in enumerate(frames):
        if frame.kp3d is None:
            continue
        local_rot = {b: anim.rotations[b][t] for b in mx.ANIMATED_BONES}
        world_pos = mx.fk_world(local_rot, root_t[t])
        joints = _derive_joint_positions(frame.kp3d)
        for bone, coco_name in mx.BONE_TO_COCO.items():
            if bone not in world_pos:
                continue
            target = joints.get(bone)
            if target is None:
                target = frame.kp3d[COCO_INDEX[coco_name]]
            per_joint_pos.setdefault(coco_name, []).append(float(np.linalg.norm(world_pos[bone] - target)))
            spec = BONE_DIRECTION.get(bone)
            if spec:
                pa, ch = spec
                if pa in joints and ch in joints:
                    v_fk = world_pos.get(ch, world_pos[bone]) - world_pos[bone]
                    v_tg = joints[ch] - joints[pa]
                    nf, nt = np.linalg.norm(v_fk), np.linalg.norm(v_tg)
                    if nf > 1e-9 and nt > 1e-9:
                        cos = float(np.clip(np.dot(v_fk / nf, v_tg / nt), -1.0, 1.0))
                        per_joint_ang.setdefault(bone, []).append(float(np.degrees(np.arccos(cos))))

    pos_mean = {k: float(np.mean(v)) for k, v in per_joint_pos.items()}
    ang_mean = {k: float(np.mean(v)) for k, v in per_joint_ang.items()}
    return {
        "position_m_mean": pos_mean,
        "angle_deg_mean": ang_mean,
        "max_position_m": max(pos_mean.values()) if pos_mean else 0.0,
        "max_angle_deg": max(ang_mean.values()) if ang_mean else 0.0,
    }
