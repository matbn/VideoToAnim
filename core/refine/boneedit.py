"""Editor de bone com rebake por keyframe.

A animacao e **toda bakeada** (uma pose por frame, sem curvas). Entao uma edicao
"de keyframe" precisa reescrever **cada frame** do intervalo afetado:

    anchor_before ......... [start ...... frame ...... end] ......... anchor_after
      (intacto)              ^----------- afetado -----------^        (intacto)

* no `frame` alvo o valor passa a ser o valor editado;
* de `anchor_before` (= start-1) ate o frame alvo, interpola com **ease-in-out**
  (smoothstep: derivada zero nas pontas, portanto sem "salto");
* do frame alvo ate `anchor_after` (= end+1) o mesmo, na volta;
* **frames fora de [start, end] ficam bit-exatamente iguais aos originais.**

Isso e verificado por teste de diff numerico.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from core.mixamo import ANIMATED_BONES, BONE_NAMES, quat_normalize
from core.refine.constraints import euler_xyz_deg_to_quat, quat_to_euler_xyz_deg, slerp


def smoothstep(u: float) -> float:
    u = float(np.clip(u, 0.0, 1.0))
    return u * u * (3.0 - 2.0 * u)


@dataclass
class BoneEdit:
    bone: str
    frame: int
    rotation: list[float] | None = None      # quaternion alvo (x,y,z,w)
    rotation_euler_deg: list[float] | None = None   # alternativa em graus XYZ
    translation: list[float] | None = None   # so faz sentido no root (Hips)
    start: int | None = None                 # intervalo afetado (inclusivo)
    end: int | None = None
    author: str = "user"
    note: str = ""
    created_at: float = field(default_factory=lambda: time.time())

    # ---- conveniencias -------------------------------------------------
    def target_quat(self) -> np.ndarray | None:
        if self.rotation is not None:
            return quat_normalize(np.asarray(self.rotation, np.float64))
        if self.rotation_euler_deg is not None:
            return euler_xyz_deg_to_quat(np.asarray(self.rotation_euler_deg, np.float64))
        return None

    def validate(self, total_frames: int | None = None) -> list[str]:
        errs: list[str] = []
        if self.bone.replace("mixamorig:", "") not in BONE_NAMES:
            errs.append(f"osso inexistente: {self.bone!r}")
        if self.frame < 0:
            errs.append("frame negativo")
        if total_frames is not None and self.frame >= total_frames:
            errs.append(f"frame {self.frame} fora do clipe (0..{total_frames - 1})")
        s = self.start if self.start is not None else self.frame
        e = self.end if self.end is not None else self.frame
        if s > e:
            errs.append(f"intervalo invalido: start={s} > end={e}")
        if not (s <= self.frame <= e):
            errs.append(f"o frame editado ({self.frame}) precisa estar dentro de [{s}, {e}]")
        if self.rotation is None and self.rotation_euler_deg is None and self.translation is None:
            errs.append("edicao sem alvo: informe rotation, rotation_euler_deg ou translation")
        if self.rotation is not None and len(self.rotation) != 4:
            errs.append("rotation deve ter 4 valores (x,y,z,w)")
        if self.rotation_euler_deg is not None and len(self.rotation_euler_deg) != 3:
            errs.append("rotation_euler_deg deve ter 3 valores (x,y,z)")
        if self.translation is not None and len(self.translation) != 3:
            errs.append("translation deve ter 3 valores (x,y,z)")
        if self.translation is not None and self.bone.replace("mixamorig:", "") != "Hips":
            errs.append("translation so e suportada no osso Hips (root)")
        return errs

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, "", [])} | {"bone": self.bone,
                                                                         "frame": self.frame}

    @staticmethod
    def from_dict(d: dict) -> "BoneEdit":
        return BoneEdit(
            bone=str(d["bone"]), frame=int(d["frame"]),
            rotation=list(d["rotation"]) if d.get("rotation") else None,
            rotation_euler_deg=list(d["rotation_euler_deg"]) if d.get("rotation_euler_deg") else None,
            translation=list(d["translation"]) if d.get("translation") else None,
            start=int(d["start"]) if d.get("start") is not None else None,
            end=int(d["end"]) if d.get("end") is not None else None,
            author=str(d.get("author", "user")), note=str(d.get("note", "")),
            created_at=float(d.get("created_at", time.time())),
        )


# ---------------------------------------------------------------------------
# Rebake
# ---------------------------------------------------------------------------
def apply_edit(anim, edit: BoneEdit) -> tuple[object, dict]:
    """Rebakeia o intervalo afetado. Devolve (nova Animation, relatorio)."""
    from core.retarget import Animation

    errs = edit.validate(anim.num_frames)
    if errs:
        raise ValueError("edicao invalida:\n  - " + "\n  - ".join(errs))

    T = anim.num_frames
    bone = edit.bone if edit.bone in anim.rotations else edit.bone.replace("mixamorig:", "")
    if bone not in anim.rotations:
        raise ValueError(f"o osso {edit.bone!r} nao tem rotacao animada nesta animacao")
    if bone not in ANIMATED_BONES:
        raise ValueError(f"o osso {bone!r} nao e animavel (end-cap)")

    rotations = {k: np.asarray(v, np.float64).copy() for k, v in anim.rotations.items()}
    root = np.asarray(anim.root_translation, np.float64).copy()
    series = rotations[bone]

    start = max(0, int(edit.start if edit.start is not None else edit.frame))
    end = min(T - 1, int(edit.end if edit.end is not None else edit.frame))
    frame = int(np.clip(edit.frame, start, end))
    a_before = max(0, start - 1)
    a_after = min(T - 1, end + 1)

    q_edit = edit.target_quat()
    changed = 0

    if q_edit is not None:
        q_before = series[a_before].copy()
        q_after = series[a_after].copy()
        for t in range(start, frame):
            u = smoothstep((t - a_before) / max(1e-9, frame - a_before))
            series[t] = slerp(q_before, q_edit, u)
            changed += 1
        series[frame] = q_edit
        changed += 1
        for t in range(frame + 1, end + 1):
            u = smoothstep((t - frame) / max(1e-9, a_after - frame))
            series[t] = slerp(q_edit, q_after, u)
            changed += 1
        # normaliza SOMENTE o intervalo escrito: fora dele os valores precisam ficar
        # bit-exatamente iguais aos originais (criterio de aceite)
        series[start:end + 1] = np.stack(
            [quat_normalize(q) for q in series[start:end + 1]])

    t_changed = 0
    if edit.translation is not None and bone.replace("mixamorig:", "") == "Hips":
        v_before = root[a_before].copy()
        v_after = root[a_after].copy()
        v_edit = np.asarray(edit.translation, np.float64)
        for t in range(start, frame):
            u = smoothstep((t - a_before) / max(1e-9, frame - a_before))
            root[t] = v_before * (1 - u) + v_edit * u
            t_changed += 1
        root[frame] = v_edit
        t_changed += 1
        for t in range(frame + 1, end + 1):
            u = smoothstep((t - frame) / max(1e-9, a_after - frame))
            root[t] = v_edit * (1 - u) + v_after * u
            t_changed += 1

    out = Animation(
        fps=anim.fps, num_frames=T, bone_names=list(anim.bone_names),
        rotations=rotations, root_translation=root,
        meta={**dict(getattr(anim, "meta", {}) or {}),
              "edits_applied": int((getattr(anim, "meta", {}) or {}).get("edits_applied", 0)) + 1},
    )
    report = {
        "bone": bone, "frame": frame, "affected": [start, end],
        "frames_rewritten": changed + t_changed,
        "anchors": [a_before, a_after],
        "author": edit.author,
    }
    return out, report


def rebake_from_edits(anim, edits: list[BoneEdit]) -> tuple[object, list[dict]]:
    """Reaplica uma lista de edicoes em ordem (usado no undo/redo)."""
    reports = []
    cur = anim
    for e in edits:
        cur, r = apply_edit(cur, e)
        reports.append(r)
    return cur, reports


# ---------------------------------------------------------------------------
# Historico persistente
# ---------------------------------------------------------------------------
@dataclass
class EditHistory:
    edits: list[BoneEdit] = field(default_factory=list)
    undone: list[BoneEdit] = field(default_factory=list)
    source: str = ""
    session_id: str = ""
    updated_at: float = field(default_factory=lambda: time.time())

    def add(self, edit: BoneEdit) -> dict:
        self.edits.append(edit)
        self.undone.clear()
        self.updated_at = time.time()
        return {"applied": edit.to_dict(), "total": len(self.edits)}

    def undo(self) -> dict:
        if not self.edits:
            raise ValueError("nada para desfazer")
        e = self.edits.pop()
        self.undone.append(e)
        self.updated_at = time.time()
        return {"undone": e.to_dict(), "total": len(self.edits)}

    def redo(self) -> dict:
        if not self.undone:
            raise ValueError("nada para refazer")
        e = self.undone.pop()
        self.edits.append(e)
        self.updated_at = time.time()
        return {"redone": e.to_dict(), "total": len(self.edits)}

    def log(self) -> list[dict]:
        return [e.to_dict() for e in self.edits]

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id or f"session-{int(self.updated_at)}",
            "source": self.source,
            "updated_at": self.updated_at,
            "edits": [e.to_dict() for e in self.edits],
            "undone": [e.to_dict() for e in self.undone],
        }

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    @staticmethod
    def load(path: str | Path) -> "EditHistory":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        h = EditHistory(
            edits=[BoneEdit.from_dict(x) for x in d.get("edits", [])],
            undone=[BoneEdit.from_dict(x) for x in d.get("undone", [])],
            source=str(d.get("source", "")),
            session_id=str(d.get("session_id", "")),
            updated_at=float(d.get("updated_at", time.time())),
        )
        return h

    def replay(self, anim) -> tuple[object, list[dict]]:
        """Reconstroi a animacao a partir do clipe original + edicoes salvas."""
        return rebake_from_edits(anim, self.edits)
