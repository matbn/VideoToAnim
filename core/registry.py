"""Registro e descoberta automatica de backends de pose.

Descoberta:
  1. Modulos embutidos em ``backends/``.
  2. Plugins externos em ``plugins/``: arquivos ``*.py`` que exponham
     ``BACKEND`` (classe ou instancia) ou ``BACKENDS`` (lista), e arquivos
     ``*.yaml`` descritivos com ``entrypoint`` opcional.

Um backend novo passa a aparecer no dropdown sem editar o nucleo nem o
frontend: basta soltar o arquivo em ``plugins/``.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .adapter import AdapterConfig, PoseBackend


@dataclass
class BackendRecord:
    name: str
    display_name: str
    backend: PoseBackend
    source: str = "builtin"
    available: bool = True
    reason: str = ""
    is_default: bool = False
    hidden: bool = False
    license_category: str = "livre"
    license: str = ""
    metadata: dict = field(default_factory=dict)

    def as_public_dict(self) -> dict:
        from .provision import KIND_LABEL, plan_for

        plan = plan_for(self.name)
        try:
            from .gpu import compute_target
            t = compute_target(self.name)
            compute = {"device": t.device, "provider": t.provider,
                       "gpu": (t.gpu.name if t.gpu else ""), "reason": t.reason,
                       "reason_code": t.reason_code,
                       "reason_vars": t.reason_vars or {}}
        except Exception:  # noqa: BLE001
            compute = {}
        install = {
            "compute": compute,
            "kind": plan.kind,
            "label": KIND_LABEL.get(plan.kind, plan.kind),
            "manual_reason": plan.manual_reason,
            "manual_code": plan.manual_code,
            "total_mb": round(sum(s.size_mb or 0 for s in plan.steps), 1),
            "installable": bool(plan.kind != "manual" and plan.steps),
        }
        return {
            "name": self.name,
            "display_name": self.display_name,
            "source": self.source,
            "available": bool(self.available),
            "reason": self.reason,
            "is_default": bool(self.is_default),
            "hidden": bool(self.hidden),
            "license_category": self.license_category,
            "license": self.license,
            "install": install,
            "metadata": self.metadata,
        }


def _load_module_from_path(path: Path):
    mod_name = f"v2m_plugin_{path.stem}_{abs(hash(str(path))) % 100000}"
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"nao foi possivel carregar {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


class BackendRegistry:
    def __init__(self) -> None:
        self._records: dict[str, BackendRecord] = {}
        self.errors: list[str] = []
        self.disabled: list[str] = []

    # ---- registro ------------------------------------------------------
    def register(self, backend: PoseBackend, source: str = "builtin", metadata: dict | None = None) -> BackendRecord:
        name = backend.name
        try:
            available = bool(backend.is_available())
            reason = "" if available else (backend.availability_reason() or "dependencias ausentes")
        except Exception as exc:  # pragma: no cover - robustez
            available = False
            reason = f"erro ao checar disponibilidade: {exc}"
        cfg = getattr(backend, "adapter_config", None)
        cat = (getattr(cfg, "license_category", None) if cfg is not None else None) \
            or getattr(backend, "license_category", None) or "livre"
        lic = (getattr(cfg, "license", "") if cfg is not None else "") \
            or getattr(backend, "license", "")
        rec = BackendRecord(
            name=name,
            display_name=getattr(backend, "display_name", name),
            backend=backend,
            source=source,
            available=available,
            reason=reason,
            is_default=bool(getattr(backend, "is_default", False)),
            hidden=bool(getattr(backend, "hidden_from_ui", False)),
            license_category=cat,
            license=lic,
            metadata=metadata or {},
        )
        self._records[name] = rec
        return rec

    def register_class(self, cls, source: str = "builtin", metadata: dict | None = None) -> BackendRecord:
        return self.register(cls(), source=source, metadata=metadata)

    def unregister(self, name: str) -> None:
        self._records.pop(name, None)

    # ---- consulta ------------------------------------------------------
    def get(self, name: str) -> BackendRecord | None:
        return self._records.get(name)

    def names(self) -> list[str]:
        return list(self._records.keys())

    def list(self) -> list[BackendRecord]:
        return list(self._records.values())

    def default(self) -> BackendRecord | None:
        """Backend padrao EFETIVO.

        Preferencia: o marcado como padrao (vitpose) **se estiver disponivel**.
        Caso contrario, devolve o primeiro backend disponivel — assim um
        ambiente sem os pesos do ViTPose nao abre a interface apontando para
        um backend que daria ModuleNotFoundError.
        """
        preferred = [r for r in self._records.values() if r.is_default]
        visible = [r for r in self._records.values() if not r.hidden]
        for rec in preferred:
            if rec.available and not rec.hidden:
                return rec
        for rec in visible:
            if rec.available:
                return rec
        for rec in preferred:
            if rec.available:
                return rec
        return (visible or preferred or list(self._records.values()) or [None])[0]

    def preferred_default_name(self) -> str | None:
        """Nome do backend preferido como padrao, independente de disponibilidade."""
        for rec in self._records.values():
            if rec.is_default:
                return rec.name
        return None

    def as_public_list(self) -> list[dict]:
        return [r.as_public_dict() for r in self._records.values()]

    def by_license_category(self) -> dict[str, list[str]]:
        """Agrupa os backends visiveis por categoria de licenca."""
        out: dict[str, list[str]] = {}
        for rec in self._records.values():
            if rec.hidden:
                continue
            out.setdefault(rec.license_category, []).append(rec.name)
        return out

    # ---- descoberta ----------------------------------------------------
    def discover_plugins(self, plugins_dir: Path) -> None:
        plugins_dir = Path(plugins_dir)
        if not plugins_dir.exists():
            return
        for path in sorted(plugins_dir.rglob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                module = _load_module_from_path(path)
            except Exception as exc:
                self.errors.append(f"{path.name}: {exc}")
                continue
            if getattr(module, "PLUGIN_DISABLED", False):
                # plugin existe como exemplo, mas foi desativado de proposito
                self.disabled.append(f"{path.name}: desativado (PLUGIN_DISABLED = True)")
                continue
            found = False
            if hasattr(module, "BACKEND"):
                obj = getattr(module, "BACKEND")
                self._register_object(obj, source=f"plugin:{path.name}")
                found = True
            if hasattr(module, "BACKENDS"):
                for obj in getattr(module, "BACKENDS"):
                    self._register_object(obj, source=f"plugin:{path.name}")
                    found = True
            if not found:
                self.errors.append(f"{path.name}: nenhum BACKEND/BACKENDS encontrado")
        for path in sorted(plugins_dir.rglob("*.y*ml")):
            try:
                spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                self.errors.append(f"{path.name}: yaml invalido: {exc}")
                continue
            entry = spec.get("entrypoint")
            if entry:
                try:
                    module = _load_module_from_path(Path(entry)) if entry.endswith(".py") else __import__(entry, fromlist=["*"])
                except Exception as exc:
                    self.errors.append(f"{path.name}: entrypoint falhou: {exc}")
                    continue
                obj = getattr(module, "BACKEND", None)
                if obj is not None:
                    self._register_object(obj, source=f"plugin:{path.name}", metadata=spec.get("metadata"))
            else:
                self.errors.append(
                    f"{path.name}: descritor sem 'entrypoint' (apenas metadados) - nao registrado"
                )

    def _register_object(self, obj, source: str, metadata: dict | None = None) -> None:
        if isinstance(obj, type):
            self.register_class(obj, source=source, metadata=metadata)
        else:
            self.register(obj, source=source, metadata=metadata)


def load_builtin_backends(registry: BackendRegistry) -> None:
    """Importa backends embutidos. Falhas de import nao derrubam o registry."""
    builtin_specs = [
        ("backends.synthetic", "SyntheticBackend"),
        ("backends.vitpose", "ViTPoseBackend"),
        ("backends.mediapipe_backend", "MediaPipeBackend"),
        ("backends.rtmpose", "RTMPoseBackend"),
        ("backends.motionbert", "MotionBERTBackend"),
        ("backends.yolopose", "YOLOPoseBackend"),
        ("backends.sam3dbody", "SAM3DBodyBackend"),
        ("backends.wham", "WHAMBackend"),
        ("backends.openpose", "OpenPoseBackend"),
        ("backends.simplebaseline", "SimpleBaselineBackend"),
        ("backends.mhformer", "MHFormerBackend"),
    ]
    for mod_name, cls_name in builtin_specs:
        try:
            module = __import__(mod_name, fromlist=[cls_name])
            cls = getattr(module, cls_name)
            registry.register_class(cls, source="builtin")
        except Exception as exc:  # pragma: no cover
            registry.errors.append(f"{mod_name}: {exc}")


def build_registry(plugins_dir: Path | None = None) -> BackendRegistry:
    reg = BackendRegistry()
    load_builtin_backends(reg)
    if plugins_dir is not None:
        reg.discover_plugins(plugins_dir)
    return reg
