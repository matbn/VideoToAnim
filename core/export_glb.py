"""Exportador GLB/glTF 2.0 com esqueleto Mixamo, malha de 'capsule sticks' e
animacao. Constroi o container GLB manualmente (header + chunk JSON + chunk
BIN) para controle total do resultado; ``pygltflib`` e usado nos testes como
validador independente do arquivo gerado.

Convencoes: metros, Y-up, frente +Z, quaternions normalizados (x, y, z, w).
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

from . import mixamo as mx
from .retarget import Animation

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

FLOAT = 5126
UNSIGNED_SHORT = 5123

_N_SIDES = 6


class _Builder:
    def __init__(self) -> None:
        self.parts: list[bytes] = []
        self.offset = 0
        self.buffer_views: list[dict] = []
        self.accessors: list[dict] = []

    def add_view(self, data: bytes, target: int | None = None) -> int:
        pad = (-self.offset) % 4
        if pad:
            self.parts.append(b"\x00" * pad)
            self.offset += pad
        view = {"buffer": 0, "byteOffset": self.offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        self.buffer_views.append(view)
        idx = len(self.buffer_views) - 1
        self.parts.append(data)
        self.offset += len(data)
        return idx

    def add_accessor(self, view: int, component_type: int, count: int, type_: str,
                     min_=None, max_=None) -> int:
        acc = {"bufferView": view, "componentType": component_type, "count": count, "type": type_}
        if min_ is not None:
            acc["min"] = list(map(float, min_))
        if max_ is not None:
            acc["max"] = list(map(float, max_))
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def blob(self) -> bytes:
        return b"".join(self.parts)


def _cylinder(a: np.ndarray, b: np.ndarray, radius: float):
    axis = b - a
    n = np.linalg.norm(axis)
    if n < 1e-9:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    axis = axis / n
    ref = np.array([0.0, 1.0, 0.0]) if abs(axis[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(axis, ref)
    u = u / np.linalg.norm(u)
    v = np.cross(axis, u)
    verts = []
    for center in (a, b):
        for k in range(_N_SIDES):
            ang = 2.0 * np.pi * k / _N_SIDES
            verts.append(center + radius * (np.cos(ang) * u + np.sin(ang) * v))
    idx = []
    for k in range(_N_SIDES):
        k2 = (k + 1) % _N_SIDES
        idx += [k, k2, _N_SIDES + k, _N_SIDES + k, k2, _N_SIDES + k2]
    return np.asarray(verts, dtype=np.float32), np.asarray(idx, dtype=np.int64)


def build_glb(anim: Animation, out_path: str | Path, radius: float = 0.022,
              skin_mesh: dict | None = None) -> dict:
    rest_pos = mx.rest_world_positions()
    bones = mx.BONE_NAMES
    n_bones = len(bones)
    T = anim.num_frames

    b = _Builder()

    # ---- IBM (inverse bind matrices) -------------------------------------
    ibm = np.zeros((n_bones, 16), np.float64)
    for i, name in enumerate(bones):
        m = np.eye(4)
        m[:3, 3] = -rest_pos[name]
        ibm[i] = m.reshape(4, 4).T.reshape(-1)  # column-major
    ibm_view = b.add_view(np.ascontiguousarray(ibm.astype(np.float32)).tobytes())
    ibm_acc = b.add_accessor(ibm_view, FLOAT, n_bones, "MAT4")

    # ---- malha: personagem do usuario (skin_mesh) ou capsule sticks --------
    if skin_mesh is not None:
        verts = np.asarray(skin_mesh["positions"], dtype=np.float32)
        idxs = np.asarray(skin_mesh["indices"], dtype=np.int64)
        joints = np.asarray(skin_mesh["joints"], dtype=np.int64)
        weights = np.asarray(skin_mesh["weights"], dtype=np.float32)
        wsum = weights.sum(axis=1, keepdims=True)
        weights = weights / np.where(wsum < 1e-6, 1.0, wsum)
        mesh_kind = "user_mesh"
    else:
        verts_all: list[np.ndarray] = []
        idx_all: list[np.ndarray] = []
        joints_all: list[np.ndarray] = []
        vbase = 0
        for name in bones:
            if mx.BONE_IS_END[name]:
                continue
            child = mx._first_child(name)
            if child is None:
                continue
            a, c = rest_pos[name], rest_pos[child]
            v, idx = _cylinder(a, c, radius)
            if v.shape[0] == 0:
                continue
            verts_all.append(v)
            idx_all.append(idx + vbase)
            joints_all.append(np.tile(np.array([mx.BONE_INDEX[name], 0, 0, 0], np.int64), (v.shape[0], 1)))
            vbase += v.shape[0]
        verts = np.concatenate(verts_all, axis=0) if verts_all else np.zeros((0, 3), np.float32)
        idxs = np.concatenate(idx_all, axis=0) if idx_all else np.zeros((0,), np.int64)
        joints = np.concatenate(joints_all, axis=0) if joints_all else np.zeros((0, 4), np.int64)
        weights = np.zeros((joints.shape[0], 4), np.float32)
        if weights.shape[0]:
            weights[:, 0] = 1.0
        mesh_kind = "stick_capsules"

    pos_min = verts.min(axis=0) if verts.shape[0] else np.zeros(3)
    pos_max = verts.max(axis=0) if verts.shape[0] else np.zeros(3)
    pos_view = b.add_view(verts.astype(np.float32).tobytes(), target=34962)
    pos_acc = b.add_accessor(pos_view, FLOAT, verts.shape[0], "VEC3", pos_min, pos_max)
    jnt_view = b.add_view(joints.astype(np.uint16).tobytes(), target=34962)
    jnt_acc = b.add_accessor(jnt_view, UNSIGNED_SHORT, joints.shape[0], "VEC4")
    wgt_view = b.add_view(weights.astype(np.float32).tobytes(), target=34962)
    wgt_acc = b.add_accessor(wgt_view, FLOAT, weights.shape[0], "VEC4")
    idx_view = b.add_view(idxs.astype(np.uint32).tobytes(), target=34963)
    idx_acc = b.add_accessor(idx_view, 5125, idxs.shape[0], "SCALAR")

    # ---- animation accessors --------------------------------------------
    times = (np.arange(T, dtype=np.float64) / max(anim.fps, 1e-6)).astype(np.float32)
    time_view = b.add_view(times.tobytes())
    time_acc = b.add_accessor(time_view, FLOAT, T, "SCALAR", [float(times.min()) if T else 0.0],
                              [float(times.max()) if T else 0.0])

    channels = []
    samplers = []
    # rotacoes
    for bone in mx.ANIMATED_BONES:
        rots = anim.rotations[bone].astype(np.float32)
        n = np.linalg.norm(rots, axis=1, keepdims=True)
        rots = rots / np.where(n < 1e-9, 1.0, n)
        view = b.add_view(rots.tobytes())
        acc = b.add_accessor(view, FLOAT, T, "VEC4")
        samplers.append({"input": time_acc, "interpolation": "LINEAR", "output": acc})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": mx.BONE_INDEX[bone], "path": "rotation"}})
    # translacao do root (Hips)
    root_tr = anim.root_translation.astype(np.float32)
    tr_view = b.add_view(root_tr.tobytes())
    tr_acc = b.add_accessor(tr_view, FLOAT, T, "VEC3",
                            root_tr.min(axis=0) if T else None, root_tr.max(axis=0) if T else None)
    samplers.append({"input": time_acc, "interpolation": "LINEAR", "output": tr_acc})
    channels.append({"sampler": len(samplers) - 1, "target": {"node": mx.BONE_INDEX["Hips"], "path": "translation"}})

    # ---- nodes -----------------------------------------------------------
    mesh_node = n_bones
    nodes = []
    for name in bones:
        node = {
            "name": mx.PREFIX + name,
            "translation": [float(x) for x in mx.BONE_OFFSET[name]],
            "rotation": [0.0, 0.0, 0.0, 1.0],
        }
        kids = [mx.BONE_INDEX[k] for k in mx._CHILDREN.get(name, [])]
        if kids:
            node["children"] = kids
        nodes.append(node)
    nodes.append({"name": "Mesh", "mesh": 0, "skin": 0})

    gltf = {
        "asset": {"version": "2.0", "generator": "video2mixamo"},
        "scene": 0,
        "scenes": [{"nodes": [mx.BONE_INDEX["Hips"], mesh_node]}],
        "nodes": nodes,
        "skins": [{
            "name": "MixamoRig",
            "joints": list(range(n_bones)),
            "skeleton": mx.BONE_INDEX["Hips"],
            "inverseBindMatrices": ibm_acc,
        }],
        "meshes": [{
            "name": "Mesh",
            "primitives": [{
                "attributes": {"POSITION": pos_acc, "JOINTS_0": jnt_acc, "WEIGHTS_0": wgt_acc},
                "indices": idx_acc,
                "mode": 4,
            }],
        }],
        "animations": [{
            "name": "Take001",
            "channels": channels,
            "samplers": samplers,
        }],
        "bufferViews": b.buffer_views,
        "accessors": b.accessors,
        "buffers": [{"byteLength": len(b.blob())}],
    }

    blob = b.blob()
    _write_glb(out_path, gltf, blob)
    return {
        "path": str(out_path),
        "bytes": int(Path(out_path).stat().st_size),
        "bones": n_bones,
        "frames": T,
        "vertices": int(verts.shape[0]),
        "channels": len(channels),
        "mesh": mesh_kind,
    }


def _write_glb(path: str | Path, gltf: dict, blob: bytes) -> None:
    json_bytes = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    json_bytes += b" " * ((-len(json_bytes)) % 4)
    bin_bytes = blob + b"\x00" * ((-len(blob)) % 4)
    total = 12 + 8 + len(json_bytes) + (8 + len(bin_bytes) if bin_bytes else 0)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", GLB_MAGIC, 2, total))
        fh.write(struct.pack("<II", len(json_bytes), CHUNK_JSON))
        fh.write(json_bytes)
        if bin_bytes:
            fh.write(struct.pack("<II", len(bin_bytes), CHUNK_BIN))
            fh.write(bin_bytes)
