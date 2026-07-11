"""GCC trunk builder.

Unlike LLVM, GCC needs a separate cross compiler per target triple. This
builds one GCC per selected architecture into ``<prefix>/gcc/<triple>``.

Sanitizers (libsanitizer) are *target* runtime libraries, so GCC can only build
them when a target libc/headers are available:

* the **native** target uses the host's own libc -> full build incl. libsanitizer;
* a **cross** target needs a sysroot and target binutils (set
  ``[gcc.sysroots]`` and ``[gcc.binutils]``) -> full build incl. libsanitizer;
* otherwise a cross target falls back to a minimal compiler-only build
  (``--without-headers``, good for ``-S`` / compile-only testing), and any
  sanitizer request is skipped with a warning.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from .. import log
from ..arch import Arch
from .base import Builder

# platform.machine() -> our arch name, for detecting the native target.
_HOST_MACHINE = {
    "x86_64": "x86_64", "amd64": "x86_64",
    "aarch64": "aarch64", "arm64": "aarch64",
    "armv7l": "arm", "armv6l": "arm", "arm": "arm",
    "riscv64": "riscv64", "riscv32": "riscv32",
    "ppc64le": "powerpc64le", "ppc64": "powerpc64",
    "s390x": "s390x", "loongarch64": "loongarch64",
    "mips64": "mips64", "mips": "mips",
}


def _host_arch_name() -> str | None:
    return _HOST_MACHINE.get(platform.machine().lower())


class GCCBuilder(Builder):
    name = "gcc"

    def _binutils_for(self, target: Arch, triple: str, native: bool) -> Path | None:
        """Locate target binutils, preferring an explicitly configured SDK."""
        tools = self.cfg.binutils_for(target)
        if tools is None and self.cfg.sysroot.enabled and not native:
            return self.cfg.prefix / "sysroot" / "tools" / triple / "bin"
        return tools

    @staticmethod
    def _tool_env(binutils: Path | None) -> dict | None:
        if binutils is None:
            return None
        return {"PATH": f"{binutils}{os.pathsep}{os.environ['PATH']}"}

    @staticmethod
    def _require_linker_tools(binutils: Path, triple: str) -> tuple[Path, Path]:
        assembler, linker = binutils / f"{triple}-as", binutils / f"{triple}-ld"
        if not assembler.is_file() or not linker.is_file():
            raise FileNotFoundError(f"expected {assembler} and {linker}")
        return assembler, linker

    def _make(self, bdir: Path, phase: str, target: str, env: dict | None) -> None:
        self._run_logged(["make", f"-j{self.cfg.jobs}", target], phase, cwd=bdir, env=env)

    def _sync(self) -> None:
        c = self.cfg.gcc
        marker = (c.repo, c.ref, c.shallow)
        if getattr(self.cfg, "_gcc_sync_marker", None) == marker:
            return
        self._git_sync(c.repo, c.ref, c.shallow)
        log.info("downloading GCC prerequisites (gmp/mpfr/mpc/isl)")
        log.run_to_log(
            ["./contrib/download_prerequisites"], self.build / "prerequisites.log", cwd=self.src
        )
        # sysroot needs GCC for its bootstrap stage, then the final GCC
        # component follows immediately in a normal install. Avoid a second
        # fetch and prerequisite pass in that one process.
        self.cfg._gcc_sync_marker = marker

    def _build_one(self, a: Arch) -> None:
        log.require("make")
        c = self.cfg.gcc
        triple = self.cfg.target_triple(a)
        bdir = self.build / triple
        idir = self.install_dir / triple
        bdir.mkdir(parents=True, exist_ok=True)

        native = a.name == _host_arch_name()
        sysroot = self.cfg.sysroot_for(a)
        binutils = self._binutils_for(a, triple, native)
        tool_env = self._tool_env(binutils)
        has_sysroot = sysroot is not None
        # A "full" build can produce target libraries (libgcc, libstdc++,
        # libsanitizer); a minimal build is just the compiler.
        full = native or has_sysroot

        configure = [
            str(self.src / "configure"),
            f"--target={triple}",
            f"--prefix={idir}",
            f"--enable-languages={','.join(c.languages)}",
            "--disable-multilib",
            "--disable-bootstrap",
        ]

        if full:
            if sysroot:
                configure.append(f"--with-sysroot={sysroot}")
            if not native:
                if not binutils:
                    raise RuntimeError(
                        f"{a.name}: a libc sysroot also requires target binutils; "
                        f"build the managed sysroot first, or set [gcc.binutils].{a.name} "
                        "to the directory containing "
                        f"{triple}-as and {triple}-ld"
                    )
                try:
                    assembler, linker = self._require_linker_tools(binutils, triple)
                except FileNotFoundError as exc:
                    raise FileNotFoundError(f"{a.name}: {exc}") from exc
                configure += [f"--with-as={assembler}", f"--with-ld={linker}"]
            configure.append(
                "--enable-libsanitizer" if c.sanitizers
                else "--disable-libsanitizer"
            )
            configure += c.extra_configure_args
            self._run_logged(configure, "configure", cwd=bdir, env=tool_env)
            self._make(bdir, "build", "all", tool_env)
            self._run_logged(["make", "install"], "install", cwd=bdir, env=tool_env)
        else:
            if c.sanitizers:
                log.warn(
                    f"{a.name}: GCC sanitizers need a target sysroot for cross "
                    "builds; building compiler only (set [gcc.sysroots] and "
                    "[gcc.binutils] to enable libc-linked binaries and libsanitizer)"
                )
            configure += [
                "--without-headers",
                "--disable-shared",
                "--disable-threads",
                "--disable-libssp",
                "--disable-libgomp",
                "--disable-libquadmath",
                "--disable-libsanitizer",
            ]
            configure += c.extra_configure_args
            self._run_logged(configure, "configure", cwd=bdir, env=tool_env)
            # all-gcc / install-gcc gives a working compiler without a full libc.
            self._make(bdir, "build", "all-gcc", tool_env)
            self._run_logged(["make", "install-gcc"], "install", cwd=bdir, env=tool_env)

    def build_bootstrap(self, a: Arch, binutils: Path, sysroot: Path) -> Path:
        """Build the headerless GCC needed to compile musl for ``a``."""
        log.require("make")
        triple = self.cfg.target_triple(a)
        bdir = self.build / f"{triple}-bootstrap"
        prefix = self.cfg.prefix / "sysroot" / "bootstrap" / triple
        bdir.mkdir(parents=True, exist_ok=True)
        assembler, linker = self._require_linker_tools(binutils, triple)
        # GCC records its bootstrap prefix as a tool search location.  Mirror
        # every target-prefixed binutils executable there as symlinks so both
        # configure-time absolute paths and later Makefiles find ar/ranlib/nm.
        bootstrap_bin = prefix / "bin"
        bootstrap_bin.mkdir(parents=True, exist_ok=True)
        for tool in binutils.glob(f"{triple}-*"):
            link = bootstrap_bin / tool.name
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(tool)
        configure = [
            str(self.src / "configure"), f"--target={triple}", f"--prefix={prefix}",
            "--enable-languages=c", "--without-headers", "--disable-multilib",
            "--disable-bootstrap", "--disable-shared", "--disable-threads",
            "--disable-libssp", "--disable-libgomp", "--disable-libquadmath",
            "--disable-libsanitizer", f"--with-as={assembler}", f"--with-ld={linker}",
            f"--with-sysroot={sysroot}",
        ]
        tool_env = self._tool_env(binutils)
        self._run_logged(configure, "configure", cwd=bdir, env=tool_env)
        self._make(bdir, "build", "all-gcc", tool_env)
        self._run_logged(["make", "install-gcc"], "install", cwd=bdir, env=tool_env)
        return prefix

    def build_bootstrap_libgcc(self, a: Arch) -> None:
        """Install libgcc after musl's headers have populated the sysroot."""
        triple = self.cfg.target_triple(a)
        bdir = self.build / f"{triple}-bootstrap"
        binutils = self.cfg.prefix / "sysroot" / "tools" / triple / "bin"
        tool_env = self._tool_env(binutils)
        # musl itself can require compiler helper routines (for example the
        # RISC-V long-double __addtf3 family).  A headerless GCC stage still
        # needs musl's public headers before it can build libgcc.
        self._make(bdir, "libgcc-build", "all-target-libgcc", tool_env)
        self._run_logged(["make", "install-target-libgcc"], "libgcc-install", cwd=bdir, env=tool_env)

    def _build_all(self) -> None:
        for a in self.cfg.arches:
            kind = "native" if a.name == _host_arch_name() else "cross"
            log.info(f"building GCC ({kind}) for {a.name} ({a.gcc_triple})")
            self._build_one(a)

    def install(self) -> None:
        log.info(f"installing GCC ({self.cfg.gcc.ref})")
        self._sync()
        self._build_all()
        log.info(f"GCC installed at {self.install_dir} ({self._describe_src()})")

    def update(self) -> None:
        log.info("updating GCC to trunk")
        self._sync()
        self._build_all()
        log.info(f"GCC updated to {self._describe_src()}")
