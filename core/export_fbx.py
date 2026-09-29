"""Exportador FBX ASCII 7.4 (7400) em Python puro.

Gera um arquivo FBX ASCII com a hierarquia completa do esqueleto Mixamo
(65 LimbNodes), BindPose e uma AnimationStack com curvas de rotacao por osso
(e translacao do Hips). Nao depende de Blender nem do FBX SDK.

Limitacao conhecida: nao inclui malha (skinning) nem materiais; e um FBX de
esqueleto+animacao. Blender/Unity/Unreal importam o rig e a animacao.
"""
from __future__ import annotations

from pathlib import Path
from typing import TextIO

import numpy as np

from . import mixamo as mx
from .retarget import Animation

_FBX_TICKS_PER_SECOND = 46186158000


def _euler_xyz_deg(q: np.ndarray) -> tuple[float, float, float]:
    """Quaternion (x,y,z,w) -> Euler XYZ em graus (ordem usada pelo FBX como XYZ)."""
    x, y, z, w = q
    # matriz de rotacao
    m00 = 1 - 2 * (y * y + z * z)
    m01 = 2 * (x * y - w * z)
    m02 = 2 * (x * z + w * y)
    m12 = 2 * (y * z - w * x)
    m22 = 1 - 2 * (x * x + y * y)
    sy = -m02
    sy = max(-1.0, min(1.0, sy))
    ry = float(np.arcsin(sy))
    if abs(sy) < 0.999999:
        rx = float(np.arctan2(m12, m22))
        rz = float(np.arctan2(m01, m00))
    else:
        rx = 0.0
        rz = float(np.arctan2(-m01, m00)) if sy > 0 else float(np.arctan2(m01, m00))
    return float(np.degrees(rx)), float(np.degrees(ry)), float(np.degrees(rz))


class _FbxId:
    def __init__(self) -> None:
        self._n = 100000

    def next(self) -> int:
        self._n += 1
        return self._n


def _p(name: str, type_: str, subtype: str, flags: str, *values) -> str:
    vals = ",".join(str(v) for v in values)
    return f'        P: "{name}", "{type_}", "{subtype}", "{flags}",{vals}\n'


def build_fbx(anim: Animation, out_path: str | Path) -> dict:
    path = Path(out_path)
    ids = _FbxId()
    bone_ids = {name: ids.next() for name in mx.BONE_NAMES}
    stack_id = ids.next()
    layer_id = ids.next()
    curve_nodes: dict[str, int] = {}
    curve_ids: list[int] = []

    T = anim.num_frames
    times = [int(round(i / max(anim.fps, 1e-6) * _FBX_TICKS_PER_SECOND)) for i in range(T)]

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        _write_header(fh, anim)
        _write_definition_counts(fh, n_bones=len(mx.BONE_NAMES), n_curve_nodes=len(mx.ANIMATED_BONES) + 1)
        fh.write("Objects:  {\n")
        for name in mx.BONE_NAMES:
            fh.write(f'    Model: {bone_ids[name]}, "Model::{mx.PREFIX}{name}", "LimbNode" {{\n')
            fh.write("        Version: 232\n")
            fh.write("        Properties70:  {\n")
            off = mx.BONE_OFFSET[name]
            fh.write(_p("Lcl Translation", "Lcl Translation", "", "A", off[0], off[1], off[2]))
            fh.write(_p("Lcl Rotation", "Lcl Rotation", "", "A", 0.0, 0.0, 0.0))
            fh.write(_p("Lcl Scaling", "Lcl Scaling", "", "A", 1.0, 1.0, 1.0))
            fh.write("        }\n")
            fh.write("        Shading: T\n        Culling: \"CullingOff\"\n")
            fh.write("    }\n")
        fh.write(f'    AnimationStack: {stack_id}, "AnimStack::Take 001", "" {{\n')
        fh.write("        Properties70:  {\n")
        fh.write(_p("LocalStop", "KTime", "Time", "", times[-1] if times else 0))
        fh.write(_p("ReferenceStop", "KTime", "Time", "", times[-1] if times else 0))
        fh.write("        }\n    }\n")
        fh.write(f'    AnimationLayer: {layer_id}, "AnimLayer::BaseLayer", "" {{\n    }}\n')
        for name in mx.ANIMATED_BONES:
            cn = ids.next()
            curve_nodes[name] = cn
            fh.write(f'    AnimationCurveNode: {cn}, "AnimCurveNode::T", "" {{\n')
            fh.write("        Properties70:  {\n")
            fh.write(_p("d|X", "Number", "", "A", 0.0))
            fh.write(_p("d|Y", "Number", "", "A", 0.0))
            fh.write(_p("d|Z", "Number", "", "A", 0.0))
            fh.write("        }\n    }\n")
        # curvas: 3 por osso (rotacao) + 3 do Hips (translacao)
        for name in mx.ANIMATED_BONES:
            for axis in range(3):
                cid = ids.next()
                curve_ids.append(cid)
                vals = [_euler_xyz_deg(anim.rotations[name][t])[axis] for t in range(T)]
                fh.write(f'    AnimationCurve: {cid}, "AnimCurve::", "" {{\n')
                fh.write(f"        Default: {vals[0] if vals else 0}\n        KeyVer: 4009\n")
                fh.write(f"        KeyTime: *{T} {{\n            a: {','.join(str(t) for t in times)}\n        }}\n")
                fh.write(f"        KeyValueFloat: *{T} {{\n            a: {','.join(f'{v:.6f}' for v in vals)}\n        }}\n")
                fh.write(f"        KeyAttrFlags: *{T} {{\n            a: {','.join('24836' for _ in range(T))}\n        }}\n")
                fh.write(f"        KeyAttrDataFloat: *{T} {{\n            a: {','.join('0,0,0,0' for _ in range(T))}\n        }}\n")
                fh.write(f"        KeyAttrRefCount: *{T} {{\n            a: {','.join('1' for _ in range(T))}\n        }}\n")
                fh.write("    }\n")
        for axis in range(3):
            cid = ids.next()
            curve_ids.append(cid)
            vals = [float(anim.root_translation[t][axis]) for t in range(T)]
            fh.write(f'    AnimationCurve: {cid}, "AnimCurve::", "" {{\n')
            fh.write(f"        Default: {vals[0] if vals else 0}\n        KeyVer: 4009\n")
            fh.write(f"        KeyTime: *{T} {{\n            a: {','.join(str(t) for t in times)}\n        }}\n")
            fh.write(f"        KeyValueFloat: *{T} {{\n            a: {','.join(f'{v:.6f}' for v in vals)}\n        }}\n")
            fh.write(f"        KeyAttrFlags: *{T} {{\n            a: {','.join('24836' for _ in range(T))}\n        }}\n")
            fh.write(f"        KeyAttrDataFloat: *{T} {{\n            a: {','.join('0,0,0,0' for _ in range(T))}\n        }}\n")
            fh.write(f"        KeyAttrRefCount: *{T} {{\n            a: {','.join('1' for _ in range(T))}\n        }}\n")
            fh.write("    }\n")
        fh.write("}\n")

        # ---- Connections -------------------------------------------------
        fh.write("Connections:  {\n")
        for name in mx.BONE_NAMES:
            parent = mx.BONE_PARENT[name]
            if parent is None:
                fh.write(f'    C: "OO",{bone_ids[name]},0\n')
            else:
                fh.write(f'    C: "OO",{bone_ids[name]},{bone_ids[parent]}\n')
        fh.write(f'    C: "OO",{layer_id},{stack_id}\n')
        for name in mx.ANIMATED_BONES:
            cn = curve_nodes[name]
            fh.write(f'    C: "OO",{cn},{layer_id}\n')
            fh.write(f'    C: "OP",{cn},{bone_ids[name]}, "Lcl Rotation"\n')
        fh.write(f'    C: "OP",{curve_nodes["Hips"]},{bone_ids["Hips"]}, "Lcl Translation"\n')
        ci = 0
        for name in mx.ANIMATED_BONES:
            for axis in range(3):
                fh.write(f'    C: "OP",{curve_ids[ci]},{curve_nodes[name]}, "d|{"XYZ"[axis]}"\n')
                ci += 1
        for axis in range(3):
            fh.write(f'    C: "OP",{curve_ids[ci]},{curve_nodes["Hips"]}, "d|{"XYZ"[axis]}"\n')
            ci += 1
        fh.write("}\n")

    return {
        "path": str(path),
        "bytes": int(path.stat().st_size),
        "bones": len(mx.BONE_NAMES),
        "frames": T,
        "curves": len(curve_ids),
    }


def _write_header(fh: TextIO, anim: Animation) -> None:
    fh.write("; FBX 7.4.0 project file\n")
    fh.write("; Gerado por video2mixamo (exportador FBX ASCII puro)\n")
    fh.write("FBXHeaderExtension:  {\n")
    fh.write("    FBXHeaderVersion: 1003\n")
    fh.write("    FBXVersion: 7400\n")
    fh.write("    CreationTimeStamp:  {\n        Version: 1000\n        Year: 2026\n        Month: 1\n        Day: 1\n")
    fh.write("        Hour: 0\n        Minute: 0\n        Second: 0\n        Millisecond: 0\n    }\n")
    fh.write('    Creator: "video2mixamo"\n')
    fh.write("}\n")
    fh.write("GlobalSettings:  {\n")
    fh.write("    Version: 1000\n    Properties70:  {\n")
    fh.write(_p("UpAxis", "int", "Integer", "", 1))
    fh.write(_p("UpAxisSign", "int", "Integer", "", 1))
    fh.write(_p("FrontAxis", "int", "Integer", "", 2))
    fh.write(_p("FrontAxisSign", "int", "Integer", "", 1))
    fh.write(_p("CoordAxis", "int", "Integer", "", 0))
    fh.write(_p("CoordAxisSign", "int", "Integer", "", 1))
    fh.write(_p("UnitScaleFactor", "double", "Number", "", 100.0))
    fh.write("    }\n}\n")


def _write_definition_counts(fh: TextIO, n_bones: int, n_curve_nodes: int) -> None:
    fh.write("Definitions:  {\n")
    fh.write("    Version: 100\n")
    fh.write(f"    Count: {n_bones + 2 + n_curve_nodes + (n_curve_nodes * 3)}\n")
    fh.write(f'    ObjectType: "Model" {{\n        Count: {n_bones}\n    }}\n')
    fh.write('    ObjectType: "AnimationStack" {\n        Count: 1\n    }\n')
    fh.write('    ObjectType: "AnimationLayer" {\n        Count: 1\n    }\n')
    fh.write(f'    ObjectType: "AnimationCurveNode" {{\n        Count: {n_curve_nodes}\n    }}\n')
    fh.write(f'    ObjectType: "AnimationCurve" {{\n        Count: {n_curve_nodes * 3}\n    }}\n')
    fh.write("}\n")
