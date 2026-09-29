"""Leitor de FBX binario (Kaydara, versao 7.x) em Python puro.

Por que existe: o Mixamo exporta FBX por padrao; exigir conversao para GLB a cada
uso seria um passo extra. Este modulo le a arvore de nos do FBX binario e extrai
o necessario para anexar a malha ao nosso rig (geometria + clusters de skinning),
sem depender de Blender nem do FBX SDK.

Formato binario 7.x:
  * registro de no: EndOffset / NumProperties / PropertyListLen / NameLen / Name
    (offsets de 64 bits quando a versao >= 7500);
  * tipos de propriedade Y C I F D L f d l i b S R, com arrays deflate-opcionais;
  * registro nulo (13 bytes, ou 25 na versao 7500+) como terminador de lista.

O parser e defensivo por construcao: cada iteracao so avanca se `end > start`,
os filhos ficam confinados ao intervalo do pai (`end`), e ha um teto de nos.
Assim um arquivo corrompido gera erro em vez de loop.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"
MAX_NODES = 3_000_000

_ARRAY_DTYPES = {"f": "<f4", "d": "<f8", "l": "<i8", "i": "<i4", "b": "u1"}
_ARRAY_SIZES = {"f": 4, "d": 8, "l": 8, "i": 4, "b": 1}


class FBXError(RuntimeError):
    pass


@dataclass
class Node:
    name: str
    props: list = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)

    def child(self, name: str) -> "Node | None":
        for c in self.children:
            if c.name == name:
                return c
        return None

    def all(self, name: str) -> list["Node"]:
        return [c for c in self.children if c.name == name]

    def p(self, i: int, default=None):
        return self.props[i] if i < len(self.props) else default

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Node {self.name} props={len(self.props)} children={len(self.children)}>"


class _Reader:
    __slots__ = ("d", "p")

    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def take(self, n: int) -> bytes:
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self) -> int:
        v = self.d[self.p]
        self.p += 1
        return v

    def i16(self) -> int:
        return int(np.frombuffer(self.take(2), dtype="<i2")[0])

    def u32(self) -> int:
        return int(np.frombuffer(self.take(4), dtype="<u4")[0])

    def i32(self) -> int:
        return int(np.frombuffer(self.take(4), dtype="<i4")[0])

    def u64(self) -> int:
        return int(np.frombuffer(self.take(8), dtype="<u8")[0])

    def i64(self) -> int:
        return int(np.frombuffer(self.take(8), dtype="<i8")[0])

    def f32(self) -> float:
        return float(np.frombuffer(self.take(4), dtype="<f4")[0])

    def f64(self) -> float:
        return float(np.frombuffer(self.take(8), dtype="<f8")[0])


def _read_property(r: _Reader):
    t = chr(r.u8())
    if t == "Y":
        return r.i16()
    if t == "C":
        return bool(r.u8())
    if t == "I":
        return r.i32()
    if t == "F":
        return r.f32()
    if t == "D":
        return r.f64()
    if t == "L":
        return r.i64()
    if t in _ARRAY_DTYPES:
        n = r.u32()
        enc = r.u32()
        clen = r.u32()
        raw = r.take(clen)
        if enc == 1:
            raw = zlib.decompress(raw)
        expect = n * _ARRAY_SIZES[t]
        if len(raw) < expect:
            raise FBXError(f"array truncado ({len(raw)} < {expect})")
        return np.frombuffer(raw[:expect], dtype=_ARRAY_DTYPES[t])
    if t in ("S", "R"):
        n = r.u32()
        return r.take(n)
    raise FBXError(f"tipo de propriedade FBX desconhecido: {t!r}")


def _read_node(r: _Reader, is64: bool, limit: int, budget: list) -> Node | None:
    start = r.p
    if is64:
        end, nprops, plen = r.u64(), r.u64(), r.u64()
    else:
        end, nprops, plen = r.u32(), r.u32(), r.u32()
    null_size = 25 if is64 else 13

    if end == 0:                      # registro nulo: fim da lista de irmaos
        r.p = start + null_size
        return None
    if end > limit or end <= start:   # garante progresso e confinamento
        raise FBXError(f"registro invalido em {start}: end={end} limit={limit}")
    budget[0] -= 1
    if budget[0] < 0:
        raise FBXError("limite de nos excedido (arquivo suspeito)")

    nlen = r.u8()
    name = r.take(nlen).decode("utf-8", "replace")
    prop_start = r.p
    props = [_read_property(r) for _ in range(nprops)]
    r.p = prop_start + plen
    if r.p > end:
        raise FBXError(f"lista de propriedades passa do fim do no {name!r}")

    node = Node(name, props)
    while r.p < end - 1:
        child = _read_node(r, is64, end, budget)
        if child is None:
            break
        node.children.append(child)
    r.p = end
    return node


def read_fbx(path: str | Path) -> tuple[Node, int]:
    """Le um FBX e devolve (raiz, versao). Aceita binario e ASCII."""
    data = Path(path).read_bytes()
    if len(data) >= 27 and data.startswith(MAGIC):
        return _read_fbx_binary(data)
    head = data[:4096].decode("utf-8", "replace").lower()
    if "fbxheaderextension" in head or "objects:" in head:
        return _read_fbx_ascii(data.decode("utf-8", "replace"))
    raise FBXError("arquivo nao reconhecido como FBX (binario ou ASCII).")


def _read_fbx_binary(data: bytes) -> tuple[Node, int]:
    version = int(np.frombuffer(data[23:27], dtype="<u4")[0])
    is64 = version >= 7500
    null_size = 25 if is64 else 13
    r = _Reader(data)
    r.p = 27
    budget = [MAX_NODES]
    root = Node("FBXRoot")
    while r.p + null_size <= len(data):
        node = _read_node(r, is64, len(data), budget)
        if node is None:
            break
        root.children.append(node)
    return root, version


# ---------------------------------------------------------------------------
# FBX ASCII (texto) — parseado para a MESMA arvore de nos, de modo que toda a
# extracao de geometria/skinning em core/mesh.py vale para os dois formatos.
# ---------------------------------------------------------------------------
_OBJ_RE = re.compile(r'^([A-Za-z_]\w*):\s*(-?\d+)\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\{\s*$')
_ARR_RE = re.compile(r'^([A-Za-z_]\w*):\s*\*\d+\s*\{\s*$')
_NODE_OPEN_RE = re.compile(r'^([A-Za-z_]\w*):\s*(.*?)\{\s*$')
_NODE_SIMPLE_RE = re.compile(r'^([A-Za-z_]\w*):\s*(.+?)\s*$')
_CONN_RE = re.compile(r'^C:\s*"([^"]*)"\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*(?:,\s*"([^"]*)")?')


def _ascii_tokens(raw: str) -> list:
    """Separa uma linha de propriedades FBX ASCII em strings e numeros."""
    out: list = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch in ", \t":
            i += 1
            continue
        if ch == '"':
            j = raw.find('"', i + 1)
            j = n if j < 0 else j
            out.append(raw[i + 1:j].encode("utf-8"))
            i = j + 1
            continue
        j = i
        while j < n and raw[j] not in ",\t":
            j += 1
        tok = raw[i:j].strip()
        i = j
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError:
            try:
                out.append(float(tok))
            except ValueError:
                out.append(tok.encode("utf-8"))
    return out


def _read_fbx_ascii(text: str) -> tuple[Node, int]:
    lines = text.splitlines()
    root = Node("FBXRoot")
    stack = [root]
    version = 7400
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        i += 1
        if not s or s.startswith(";"):
            continue
        if s.startswith("}"):
            if len(stack) > 1:
                stack.pop()
            continue

        m = _OBJ_RE.match(s)
        if m:
            node = Node(m.group(1), [int(m.group(2)), m.group(3).encode(), m.group(4).encode()])
            stack[-1].children.append(node)
            stack.append(node)
            continue

        m = _CONN_RE.match(s)
        if m:
            props = [m.group(1).encode(), int(m.group(2)), int(m.group(3))]
            if m.group(4) is not None:
                props.append(m.group(4).encode())
            stack[-1].children.append(Node("C", props))
            continue

        m = _ARR_RE.match(s)
        if m:
            name = m.group(1)
            chunks: list[str] = []
            while i < n:
                t = lines[i].strip()
                i += 1
                if t.startswith("}"):
                    break
                if t.startswith("a:"):
                    chunks.append(t[2:].strip())
            blob = "".join(chunks).strip().strip(",")
            vals = [v for v in blob.split(",") if v.strip()]
            if name in ("Indexes", "PolygonVertexIndex", "Materials"):
                arr = np.array([int(float(v)) for v in vals], dtype=np.int64) if vals else np.zeros(0, np.int64)
            else:
                arr = np.array([float(v) for v in vals], dtype=np.float64) if vals else np.zeros(0, np.float64)
            stack[-1].children.append(Node(name, [arr]))
            continue

        if s.startswith("P:"):
            stack[-1].children.append(Node("P", _ascii_tokens(s[2:])))
            continue

        m = _NODE_OPEN_RE.match(s)
        if m:
            node = Node(m.group(1), _ascii_tokens(m.group(2)))
            stack[-1].children.append(node)
            stack.append(node)
            continue

        m = _NODE_SIMPLE_RE.match(s)
        if m:
            node = Node(m.group(1), _ascii_tokens(m.group(2)))
            stack[-1].children.append(node)
            if m.group(1) == "FBXVersion" and node.props:
                try:
                    version = int(node.props[0])
                except (TypeError, ValueError):
                    version = 7400
    return root, version


def unit_scale_factor(root: Node) -> float:
    """UnitScaleFactor do GlobalSettings (FBX: fator para centimetros)."""
    gs = root.child("GlobalSettings")
    if gs is None:
        return 1.0
    p70 = gs.child("Properties70")
    if p70 is None:
        return 1.0
    for p in p70.all("P"):
        name = p.p(0)
        if isinstance(name, bytes):
            name = name.decode("utf-8", "replace")
        if name == "UnitScaleFactor":
            try:
                return float(p.props[-1])
            except (TypeError, ValueError):
                return 1.0
    return 1.0
