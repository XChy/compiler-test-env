"""Architecture registry.

Maps a friendly architecture name to the identifiers each backend needs:

* ``llvm_target``  -- value for ``LLVM_TARGETS_TO_BUILD``
* ``gcc_triple``   -- GNU target triple for a GCC cross toolchain
* ``qemu_user``    -- qemu ``linux-user`` target name (``qemu-<name>``)
* ``qemu_system``  -- qemu ``softmmu`` target name (``qemu-system-<name>``)

A single LLVM/clang build can target every ``llvm_target`` at once, so for
LLVM the architecture list only selects which backends get compiled in. GCC,
by contrast, needs one cross compiler per ``gcc_triple``.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class Arch:
    name: str
    llvm_target: str
    gcc_triple: str
    qemu_user: str
    qemu_system: str


# Keep this table small but representative; extend as needed.
_ARCHES = [
    Arch("x86_64", "X86", "x86_64-linux-gnu", "x86_64", "x86_64"),
    Arch("aarch64", "AArch64", "aarch64-linux-gnu", "aarch64", "aarch64"),
    Arch("arm", "ARM", "arm-linux-gnueabihf", "arm", "arm"),
    Arch("riscv64", "RISCV", "riscv64-linux-gnu", "riscv64", "riscv64"),
    Arch("riscv32", "RISCV", "riscv32-linux-gnu", "riscv32", "riscv32"),
    Arch("mips64", "Mips", "mips64-linux-gnuabi64", "mips64", "mips64"),
    Arch("mips", "Mips", "mipsel-linux-gnu", "mipsel", "mipsel"),
    Arch("powerpc64", "PowerPC", "powerpc64-linux-gnu", "ppc64", "ppc64"),
    Arch("powerpc64le", "PowerPC", "powerpc64le-linux-gnu", "ppc64le", "ppc64"),
    Arch("s390x", "SystemZ", "s390x-linux-gnu", "s390x", "s390x"),
    Arch("loongarch64", "LoongArch", "loongarch64-linux-gnu", "loongarch64", "loongarch64"),
]

ARCHES = {a.name: a for a in _ARCHES}


def resolve(names: list[str]) -> list[Arch]:
    """Resolve a list of architecture names, raising on unknown entries."""
    unknown = [n for n in names if n not in ARCHES]
    if unknown:
        raise KeyError(
            f"unknown architecture(s): {', '.join(unknown)}. "
            f"Known: {', '.join(ARCHES)}"
        )
    return [ARCHES[n] for n in names]


def llvm_targets(arches: list[Arch]) -> str:
    """De-duplicated, stable-ordered value for LLVM_TARGETS_TO_BUILD."""
    seen: list[str] = []
    for a in arches:
        if a.llvm_target not in seen:
            seen.append(a.llvm_target)
    return ";".join(seen)
