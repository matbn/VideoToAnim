"""Inspecao de malha enviada pelo usuario e checagem de compatibilidade do rig.

Objetivo: permitir que o usuario forneca uma mesh (personagem) e usar a malha
dele no lugar dos "capsule sticks" do preview. O software precisa **detectar**
se o esqueleto da malha e compativel com o rig Mixamo que geramos e **avisar**
quando nao for.

Formatos suportados:
  * **FBX binario** (padrao do Mixamo) — compatibilidade E anexo, com leitor proprio
    em Python puro (`core/fbx.py`); nao exige Blender nem o FBX SDK.
  * **GLB / glTF** — compatibilidade E anexo (re-skin via `pygltflib`).
  * **FBX ASCII** — compatibilidade por leitura dos nomes de osso; exporte
    'FBX Binary' (padrao do Mixamo) ou glTF/GLB para anexar.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core import mixamo as mx

EXPECTED = set(mx.BONE_NAMES)

_COMPONENT_DTYPE = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
_TYPE_SIZE = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


@dataclass
class MeshReport:
    format: str = "unknown"
    bones_found: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    compatible: bool = False
    attachable: bool = False
    messages: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "format": self.format,
            "bones_found": len(self.bones_found),
            "matched": len(self.matched),
            "missing": self.missing[:20],
            "missing_count": len(self.missing),
            "extra": self.extra[:20],
            "extra_count": len(self.extra),
            "compatible": self.compatible,
            "attachable": self.attachable,
            "messages": self.messages,
            "stats": self.stats,
        }


def detect_format(path: str | Path) -> str:
    path = Path(path)
    head = path.read_bytes()[:32]
    if head[:4] == b"glTF":
        return "glb"
    if head[:4] == b"Kaydara" or head[:20].startswith(b"Kaydara FBX Binary"):
        return "fbx-binary"
    low = path.read_bytes()[:4096].lower()
    if b"fbxheaderextension" in low:
        return "fbx-ascii"
    if head.lstrip()[:1] in (b"{", b"["):
        return "gltf"
    return "unknown"


def _strip_prefix(name: str) -> str:
    for pre in ("mixamorig:", "mixamorig", "mixamorig_"):
        if name.startswith(pre):
            return name[len(pre):].lstrip(":_")
    return name


def extract_bone_names(path: str | Path, fmt: str | None = None) -> list[str]:
    path = Path(path)
    fmt = fmt or detect_format(path)
    if fmt in ("fbx-binary", "fbx-ascii"):
        data = path.read_bytes()
        found = {_strip_prefix(m.group(0).decode("ascii", "replace"))
                 for m in re.finditer(rb"mixamorig[:_][A-Za-z0-9_]+", data)}
        # remove artefatos de string (ex.: "Hips_skin" em paths de skin)
        return sorted(n for n in found if n in EXPECTED or n in EXPECTED or not n.endswith("_skin"))
    if fmt in ("glb", "gltf"):
        from pygltflib import GLTF2

        g = GLTF2().load(str(path))
        names = []
        if g.skins:
            # so as juntas do esqueleto importam (ignora nodes de malha, ex.: "Mesh")
            for j in g.skins[0].joints:
                nm = g.nodes[j].name
                if nm:
                    names.append(_strip_prefix(nm))
        else:
            names = [_strip_prefix(n.name) for n in g.nodes if n.name]
        return sorted(set(names))
    return []


def check_compatibility(path: str | Path) -> MeshReport:
    """Compara o esqueleto da malha com o contrato Mixamo de 65 ossos."""
    path = Path(path)
    rep = MeshReport()
    rep.format = detect_format(path)
    if rep.format == "unknown":
        rep.messages.append("formato nao reconhecido: use GLB/glTF ou FBX do Mixamo.")
        return rep

    bones = extract_bone_names(path, rep.format)
    rep.bones_found = bones
    boneset = set(bones)
    rep.matched = sorted(boneset & EXPECTED)
    rep.missing = sorted(EXPECTED - boneset)
    rep.extra = sorted(boneset - EXPECTED)

    # 65/65 -> compativel. Toleramos ausencia apenas de end-caps (nao animados).
    missing_animated = [b for b in rep.missing if not mx.BONE_IS_END.get(b, False)]
    rep.compatible = len(missing_animated) == 0 and len(rep.matched) >= 45

    if rep.format in ("glb", "gltf", "fbx-binary", "fbx-ascii"):
        rep.attachable = rep.compatible
    else:
        rep.attachable = False
        rep.messages.append("formato de malha nao suportado para anexo (use FBX ou GLB).")

    if not rep.compatible:
        rep.messages.append(
            f"esqueleto incompativel: {len(missing_animated)} ossos exigidos ausentes "
            f"(ex.: {', '.join(missing_animated[:6])})."
        )
    if rep.extra:
        rep.messages.append(f"{len(rep.extra)} ossos fora do padrao Mixamo (ignorados).")
    return rep


# ---------------------------------------------------------------------------
# Leitura de malha GLB para anexo
# ---------------------------------------------------------------------------
def _read_accessor(g, idx: int) -> np.ndarray:
    acc = g.accessors[idx]
    view = g.bufferViews[acc.bufferView]
    blob = g.binary_blob()
    dtype = _COMPONENT_DTYPE[acc.componentType]
    n = acc.count * _TYPE_SIZE[acc.type]
    off = (view.byteOffset or 0) + (acc.byteOffset or 0)
    arr = np.frombuffer(blob, dtype=dtype, count=n, offset=off)
    if acc.type == "SCALAR":
        return arr
    return arr.reshape(acc.count, _TYPE_SIZE[acc.type])


def load_mesh(path: str | Path, report: MeshReport | None = None) -> dict:
    """Dispatcher: anexa a malha no formato que o arquivo tiver (GLB ou FBX binario)."""
    fmt = report.format if report is not None else detect_format(path)
    if fmt in ("glb", "gltf"):
        return load_glb_mesh(path)
    if fmt in ("fbx-binary", "fbx-ascii"):
        return load_fbx_mesh(path)
    raise ValueError(f"formato de malha nao suportado para anexo: {fmt}")


def _fbx_name(node) -> str:
    """Nome do objeto FBX: 'Nome\x00\x01Classe' -> 'Nome'."""
    raw = node.p(1)
    if isinstance(raw, bytes):
        raw = raw.split(b"\x00", 1)[0]
        return _strip_prefix(raw.decode("utf-8", "replace"))
    return ""


def load_fbx_mesh(path: str | Path) -> dict:
    """Extrai geometria + skinning de um FBX binario e remapeia para o nosso rig.

    Convencoes do FBX usadas aqui:
      * Vertex/Cluster Indexes referem-se a **control points** (base Vertices);
      * o Skin aponta para a Geometry (`OO`, skin, geometry);
      * cada Cluster aponta para a Skin e para o osso (`OO`, cluster, model);
      * um Cluster **sem** `Indexes` influencia todos os control points da malha;
      * unidade: FBX e em cm por padrao -> metros = v * (UnitScaleFactor / 100).
    """
    from core.fbx import read_fbx, unit_scale_factor

    root, _version = read_fbx(path)
    obj = root.child("Objects")
    if obj is None:
        raise ValueError("FBX sem secao Objects")
    connections = root.child("Connections")
    conns = [(c.p(1), c.p(2)) for c in (connections.all("C") if connections else [])]

    models = {m.p(0): m for m in obj.all("Model")}
    geoms = {g.p(0): g for g in obj.all("Geometry")}
    defs = {d.p(0): d for d in obj.all("Deformer")}

    by_src: dict[int, list[int]] = {}
    by_dst: dict[int, list[int]] = {}
    for s, d in conns:
        by_src.setdefault(s, []).append(d)
        by_dst.setdefault(d, []).append(s)

    scale = unit_scale_factor(root) / 100.0  # FBX base (cm) -> metros
    if scale <= 0:
        scale = 0.01

    pos_all, jnt_all, wgt_all, idx_all = [], [], [], []
    vbase = 0
    meshes = unweighted = 0
    skins_seen = 0
    empty_clusters = 0
    for skin_uid, d in defs.items():
        if d.p(2) != b"Skin":
            continue
        skins_seen += 1
        geo_uids = [x for x in by_src.get(skin_uid, []) if x in geoms]
        if not geo_uids:
            continue
        g = geoms[geo_uids[0]]
        v_node = g.child("Vertices")
        poly_node = g.child("PolygonVertexIndex")
        if v_node is None or poly_node is None or not v_node.props:
            continue
        verts = np.asarray(v_node.props[0], dtype=np.float64).reshape(-1, 3) * scale
        poly = np.asarray(poly_node.props[0], dtype=np.int64)
        n = len(verts)

        # triangulacao por leque (indice negativo fecha o poligono)
        tris: list[int] = []
        face: list[int] = []
        for raw in poly:
            i = int(raw)
            if i < 0:
                face.append(~i)
                for k in range(1, len(face) - 1):
                    tris += [face[0], face[k], face[k + 1]]
                face = []
            else:
                face.append(i)

        # pesos por control point
        per_vertex: list[dict[int, float]] = [dict() for _ in range(n)]
        for cl in by_dst.get(skin_uid, []):
            cd = defs.get(cl)
            if cd is None or cd.p(2) != b"Cluster":
                continue
            # o osso e ligado ao cluster como Model -> Cluster (C: "OO", model, cluster)
            bones = [x for x in by_dst.get(cl, []) if x in models]
            if not bones:
                bones = [x for x in by_src.get(cl, []) if x in models]
            if not bones:
                continue
            bone_name = _fbx_name(models[bones[0]])
            if bone_name not in mx.BONE_INDEX:
                continue
            bidx = mx.BONE_INDEX[bone_name]
            ix_node, w_node = cd.child("Indexes"), cd.child("Weights")
            if ix_node is not None and w_node is not None and ix_node.props and w_node.props:
                ids = np.asarray(ix_node.props[0], dtype=np.int64)
                ws = np.asarray(w_node.props[0], dtype=np.float64)
                for i, wv in zip(ids.tolist(), ws.tolist()):
                    if 0 <= i < n:
                        per_vertex[i][bidx] = per_vertex[i].get(bidx, 0.0) + float(wv)
            # cluster SEM Indexes/Weights = osso que nao influencia este mesh.
            # (o Mixamo emite um cluster para TODO osso do esqueleto; os que nao
            # pesam em nada vem vazios. Trata-los como "influencia todos" era o bug
            # que prendia o corpo inteiro a pontas de dedo/end-caps.)
            else:
                empty_clusters += 1

        joints = np.zeros((n, 4), np.uint16)
        weights = np.zeros((n, 4), np.float32)
        for i, wmap in enumerate(per_vertex):
            if not wmap:
                unweighted += 1
                joints[i, 0] = mx.BONE_INDEX["Hips"]
                weights[i, 0] = 1.0
                continue
            top = sorted(wmap.items(), key=lambda kv: -kv[1])[:4]
            tot = sum(w for _b, w in top) or 1.0
            for slot, (b, w) in enumerate(top):
                joints[i, slot] = b
                weights[i, slot] = w / tot

        pos_all.append(verts.astype(np.float32))
        jnt_all.append(joints)
        wgt_all.append(weights)
        idx_all.append(np.asarray(tris, dtype=np.uint32) + vbase)
        vbase += n
        meshes += 1

    if not pos_all:
        if skins_seen == 0:
            raise ValueError(
                "o FBX nao tem skin (esqueleto com pesos). No Mixamo, baixe com "
                "'Skin: With Skin' — sem isso o esqueleto nao pode receber a animacao."
            )
        raise ValueError("FBX com skin, mas sem geometria utilizavel (Vertices/PolygonVertexIndex ausentes).")
    return {
        "positions": np.concatenate(pos_all, 0),
        "joints": np.concatenate(jnt_all, 0),
        "weights": np.concatenate(wgt_all, 0),
        "indices": np.concatenate(idx_all, 0),
        "bone_names": [],
        "source": "fbx",
        "stats": {"meshes": meshes, "vertices": int(vbase), "unweighted": unweighted,
                  "empty_clusters": empty_clusters},
    }


def load_glb_mesh(path: str | Path) -> dict:
    """Extrai geometria + skinning de um GLB, remapeando as juntas para o nosso rig.

    Retorna dict com: positions (V,3) float32, indices (F*3,) uint32,
    joints (V,4) uint16 (nosso indice de osso), weights (V,4) float32,
    bone_names (list[str]) — ou levanta ValueError se incompativel.
    """
    from pygltflib import GLTF2

    g = GLTF2().load(str(path))
    if not g.skins:
        raise ValueError("GLB sem skin (esqueleto) — nao da para reancorar no rig.")

    skin = g.skins[0]
    joint_names = [_strip_prefix(g.nodes[j].name or "") for j in skin.joints]
    remap = {}
    for i, name in enumerate(joint_names):
        if name in mx.BONE_INDEX:
            remap[i] = mx.BONE_INDEX[name]
    missing = [n for n in mx.BONE_NAMES if n not in joint_names and not mx.BONE_IS_END.get(n, False)]
    if missing:
        raise ValueError(f"esqueleto incompativel: faltam {len(missing)} ossos (ex.: {missing[:5]})")

    pos_all, jnt_all, wgt_all, idx_all = [], [], [], []
    vbase = 0
    for mesh in g.meshes or []:
        for prim in mesh.primitives:
            attrs = prim.attributes
            if attrs.POSITION is None:
                continue
            pos = _read_accessor(g, attrs.POSITION).astype(np.float32)
            idx = _read_accessor(g, prim.indices).astype(np.uint32) if prim.indices is not None \
                else np.arange(len(pos), dtype=np.uint32)
            if attrs.JOINTS_0 is not None and attrs.WEIGHTS_0 is not None:
                jnt = _read_accessor(g, attrs.JOINTS_0).astype(np.int64)
                wgt = _read_accessor(g, attrs.WEIGHTS_0).astype(np.float32)
                mapped = np.zeros_like(jnt)
                unknown = set()
                for a in range(jnt.shape[0]):
                    for b in range(jnt.shape[1]):
                        src = int(jnt[a, b])
                        if src in remap:
                            mapped[a, b] = remap[src]
                        else:
                            mapped[a, b] = 0
                            if wgt[a, b] > 0:
                                unknown.add(src)
                if unknown:
                    raise ValueError(f"juntas do GLB sem correspondencia no rig: {sorted(unknown)[:6]}")
            else:
                mapped = np.zeros((len(pos), 4), np.int64)
                wgt = np.zeros((len(pos), 4), np.float32)
                wgt[:, 0] = 1.0
            pos_all.append(pos)
            jnt_all.append(mapped.astype(np.uint16))
            wgt_all.append(wgt)
            idx_all.append(idx + vbase)
            vbase += len(pos)

    if not pos_all:
        raise ValueError("GLB sem geometria com POSITION.")
    return {
        "positions": np.concatenate(pos_all, 0),
        "joints": np.concatenate(jnt_all, 0),
        "weights": np.concatenate(wgt_all, 0),
        "indices": np.concatenate(idx_all, 0),
        "bone_names": joint_names,
    }
