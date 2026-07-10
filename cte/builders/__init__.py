"""Component builders."""

from __future__ import annotations

from ..config import Config
from .base import Builder
from .gcc import GCCBuilder
from .llvm import LLVMBuilder
from .qemu import QEMUBuilder
from .sysroot import SysrootBuilder

_REGISTRY = {
    "sysroot": SysrootBuilder,
    "llvm": LLVMBuilder,
    "gcc": GCCBuilder,
    "qemu": QEMUBuilder,
}


def get(name: str, cfg: Config) -> Builder:
    if name not in _REGISTRY:
        raise KeyError(f"unknown component: {name}. Known: {', '.join(_REGISTRY)}")
    return _REGISTRY[name](cfg)


def all_names() -> list[str]:
    return list(_REGISTRY)
