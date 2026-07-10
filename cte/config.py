"""Configuration loading and validation.

Config is TOML. See ``config.example.toml`` for the documented schema.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10
    import tomli as tomllib  # type: ignore

from . import arch

_MUSL_TRIPLES = {
    "x86_64": "x86_64-linux-musl", "aarch64": "aarch64-linux-musl",
    "arm": "arm-linux-musleabihf", "riscv64": "riscv64-linux-musl",
    "riscv32": "riscv32-linux-musl", "mips64": "mips64-linux-musl",
    "mips": "mipsel-linux-musl", "powerpc64": "powerpc64-linux-musl",
    "powerpc64le": "powerpc64le-linux-musl", "s390x": "s390x-linux-musl",
    "loongarch64": "loongarch64-linux-musl",
}


@dataclass
class LLVMConfig:
    enabled: bool = True
    ref: str = "main"
    repo: str = "git@github.com:llvm/llvm-project.git"
    projects: list[str] = field(default_factory=lambda: ["clang", "lld"])
    # compiler-rt provides the sanitizer runtimes (asan/ubsan/tsan/...).
    runtimes: list[str] = field(default_factory=lambda: ["compiler-rt"])
    shallow: bool = True
    extra_cmake_args: list[str] = field(default_factory=list)


@dataclass
class GCCConfig:
    enabled: bool = True
    ref: str = "master"
    repo: str = "git@github.com:gcc-mirror/gcc.git"
    languages: list[str] = field(default_factory=lambda: ["c", "c++"])
    # Build libsanitizer. Requires target libc/headers, so it only takes effect
    # for the native target or when a sysroot is given via extra_configure_args.
    sanitizers: bool = True
    shallow: bool = True
    # Per-target Linux sysroots.  A cross compiler without its target libc,
    # headers and crt objects can only emit objects/assembly, not executables.
    # Keys are names from ``architectures``; values may be relative to the
    # config file.
    sysroots: dict[str, str] = field(default_factory=dict)
    # Directories containing <triple>-as and <triple>-ld.  GCC's driver needs
    # target binutils in addition to the target sysroot to link executables.
    binutils: dict[str, str] = field(default_factory=dict)
    extra_configure_args: list[str] = field(default_factory=list)


@dataclass
class SysrootConfig:
    """Sources and options for project-managed Linux/musl sysroots."""
    enabled: bool = True
    libc: str = "musl"
    linux_repo: str = "https://github.com/torvalds/linux.git"
    linux_ref: str = "master"
    musl_repo: str = "https://git.musl-libc.org/git/musl"
    musl_ref: str = "master"
    binutils_repo: str = "https://sourceware.org/git/binutils-gdb.git"
    binutils_ref: str = "master"
    shallow: bool = True


@dataclass
class QEMUConfig:
    enabled: bool = True
    version: str = "latest"  # "latest" stable, or a pinned tag like "9.1.0"
    modes: list[str] = field(default_factory=lambda: ["user"])  # user, system
    extra_configure_args: list[str] = field(default_factory=list)


@dataclass
class Config:
    root: Path
    prefix: Path
    src_dir: Path
    build_dir: Path
    jobs: int
    architectures: list[str]
    llvm: LLVMConfig
    gcc: GCCConfig
    sysroot: SysrootConfig
    qemu: QEMUConfig

    @property
    def arches(self) -> list[arch.Arch]:
        return arch.resolve(self.architectures)

    def enabled_components(self) -> list[str]:
        out = []
        if self.sysroot.enabled:
            out.append("sysroot")
        if self.llvm.enabled:
            out.append("llvm")
        if self.gcc.enabled:
            out.append("gcc")
        if self.qemu.enabled:
            out.append("qemu")
        return out

    @property
    def host_architecture(self) -> str | None:
        names = {"amd64": "x86_64", "arm64": "aarch64"}
        return names.get(platform.machine().lower(), platform.machine().lower())

    def target_triple(self, target: arch.Arch) -> str:
        """The ABI triple selected for a target compiler invocation."""
        if self.sysroot.enabled and target.name != self.host_architecture:
            if self.sysroot.libc != "musl":
                raise ValueError(f"unsupported managed libc: {self.sysroot.libc}")
            return _MUSL_TRIPLES[target.name]
        return target.gcc_triple

    def sysroot_for(self, target: arch.Arch) -> Path | None:
        """Return the configured target sysroot, resolved from the config root."""
        if self.sysroot.enabled and target.name != self.host_architecture:
            return self.prefix / "sysroot" / self.target_triple(target)
        value = self.gcc.sysroots.get(target.name)
        return _resolve_dir(self.root, value) if value else None

    def binutils_for(self, target: arch.Arch) -> Path | None:
        """Return the directory containing the target assembler and linker."""
        value = self.gcc.binutils.get(target.name)
        return _resolve_dir(self.root, value) if value else None


def project_root(start: Path | None = None) -> Path:
    """The stable anchor for src/build/toolchains directories.

    Walk up from ``start`` (or CWD) to the nearest ``pyproject.toml``/``.git``;
    this keeps the working directories fixed regardless of where the config file
    lives or which subdirectory you invoke ``cte`` from.
    """
    p = (start or Path.cwd()).resolve()
    for d in [p, *p.parents]:
        if (d / "pyproject.toml").exists() or (d / ".git").exists():
            return d
    return p


def _resolve_dir(root: Path, value: str) -> Path:
    p = Path(os.path.expanduser(value))
    return p if p.is_absolute() else (root / p)


def from_data(data: dict, root: Path) -> Config:
    """Build a Config from already-parsed TOML data rooted at ``root``."""
    general = data.get("general", {})

    jobs = int(general.get("jobs", 0)) or (os.cpu_count() or 1)
    cfg = Config(
        root=root,
        prefix=_resolve_dir(root, general.get("prefix", "toolchains")),
        src_dir=_resolve_dir(root, general.get("src_dir", "src")),
        build_dir=_resolve_dir(root, general.get("build_dir", "build")),
        jobs=jobs,
        architectures=general.get("architectures", ["x86_64"]),
        llvm=LLVMConfig(**data.get("llvm", {})),
        gcc=GCCConfig(**data.get("gcc", {})),
        sysroot=SysrootConfig(**data.get("sysroot", {})),
        qemu=QEMUConfig(**data.get("qemu", {})),
    )
    # Validate architectures eagerly for a friendly error.
    known = {a.name for a in arch.resolve(cfg.architectures)}
    unknown_tool_paths = (set(cfg.gcc.sysroots) | set(cfg.gcc.binutils)) - known
    if unknown_tool_paths:
        raise KeyError(
            "cross-tool path(s) configured for architecture(s) not selected: "
            + ", ".join(sorted(unknown_tool_paths))
        )
    return cfg


def defaults(root: Path | None = None) -> Config:
    """Built-in defaults, used when no config file is present."""
    return from_data({}, root or project_root())


def load(path: Path) -> Config:
    """Load config from ``path``.

    A missing file is only an error if the user pointed us at a specific one;
    the default ``./config.toml`` simply falls back to built-in defaults so the
    tool works with zero setup.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"config file not found: {path}\n"
            "Copy config.example.toml to config.toml and edit it."
        )
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return from_data(data, path.resolve().parent)
