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

    def _sync(self) -> None:
        c = self.cfg.gcc
        self._git_sync(c.repo, c.ref, c.shallow)
        log.info("downloading GCC prerequisites (gmp/mpfr/mpc/isl)")
        log.run(["./contrib/download_prerequisites"], cwd=self.src)

    def _build_one(self, a: Arch) -> None:
        log.require("make")
        c = self.cfg.gcc
        triple = self.cfg.target_triple(a)
        bdir = self.build / triple
        idir = self.install_dir / triple
        bdir.mkdir(parents=True, exist_ok=True)

        native = a.name == _host_arch_name()
        sysroot = self.cfg.sysroot_for(a)
        binutils = self.cfg.binutils_for(a)
        if binutils is None and self.cfg.sysroot.enabled and not native:
            binutils = self.cfg.prefix / "sysroot" / "tools" / triple / "bin"
        tool_env = None
        if binutils:
            # GCC invokes ar/ranlib/nm by target-prefixed name while building
            # runtime libraries.  --with-as/--with-ld alone is insufficient.
            tool_env = {"PATH": f"{binutils}{os.pathsep}{os.environ['PATH']}"}
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
                assembler = binutils / f"{triple}-as"
                linker = binutils / f"{triple}-ld"
                if not assembler.is_file() or not linker.is_file():
                    raise FileNotFoundError(
                        f"{a.name}: expected {assembler} and {linker}"
                    )
                configure += [f"--with-as={assembler}", f"--with-ld={linker}"]
            configure.append(
                "--enable-libsanitizer" if c.sanitizers
                else "--disable-libsanitizer"
            )
            configure += c.extra_configure_args
            log.run(configure, cwd=bdir, env=tool_env)
            log.run(["make", f"-j{self.cfg.jobs}"], cwd=bdir, env=tool_env)
            log.run(["make", "install"], cwd=bdir, env=tool_env)
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
            log.run(configure, cwd=bdir, env=tool_env)
            # all-gcc / install-gcc gives a working compiler without a full libc.
            log.run(["make", f"-j{self.cfg.jobs}", "all-gcc"], cwd=bdir, env=tool_env)
            log.run(["make", "install-gcc"], cwd=bdir, env=tool_env)

    def build_bootstrap(self, a: Arch, binutils: Path, sysroot: Path) -> Path:
        """Build the headerless GCC needed to compile musl for ``a``."""
        log.require("make")
        triple = self.cfg.target_triple(a)
        bdir = self.build / f"{triple}-bootstrap"
        prefix = self.cfg.prefix / "sysroot" / "bootstrap" / triple
        bdir.mkdir(parents=True, exist_ok=True)
        assembler = binutils / f"{triple}-as"
        linker = binutils / f"{triple}-ld"
        if not assembler.is_file() or not linker.is_file():
            raise FileNotFoundError(f"expected {assembler} and {linker}")
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
        tool_env = {"PATH": f"{binutils}{os.pathsep}{os.environ['PATH']}"}
        log.run(configure, cwd=bdir, env=tool_env)
        log.run(["make", f"-j{self.cfg.jobs}", "all-gcc"], cwd=bdir, env=tool_env)
        log.run(["make", "install-gcc"], cwd=bdir, env=tool_env)
        return prefix

    def build_bootstrap_libgcc(self, a: Arch) -> None:
        """Install libgcc after musl's headers have populated the sysroot."""
        triple = self.cfg.target_triple(a)
        bdir = self.build / f"{triple}-bootstrap"
        binutils = self.cfg.prefix / "sysroot" / "tools" / triple / "bin"
        tool_env = {"PATH": f"{binutils}{os.pathsep}{os.environ['PATH']}"}
        # musl itself can require compiler helper routines (for example the
        # RISC-V long-double __addtf3 family).  A headerless GCC stage still
        # needs musl's public headers before it can build libgcc.
        log.run(["make", f"-j{self.cfg.jobs}", "all-target-libgcc"], cwd=bdir, env=tool_env)
        log.run(["make", "install-target-libgcc"], cwd=bdir, env=tool_env)

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
