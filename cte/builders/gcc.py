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

import json
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

    def _build_root(self, coverage: bool) -> Path:
        return self.cfg.build_dir / ("gcc-coverage" if coverage else "gcc")

    def _install_root(self, coverage: bool) -> Path:
        return self.cfg.prefix / ("gcc-coverage" if coverage else "gcc")

    def _variants(self) -> list[bool]:
        return [False, *([True] if self.cfg.gcc.coverage else [])]

    def _binutils_for(self, target: Arch, triple: str, native: bool) -> Path | None:
        """Locate target binutils, preferring an explicitly configured SDK."""
        tools = self.cfg.binutils_for(target)
        if tools is None and self.cfg.sysroot.enabled and not native:
            return self.cfg.prefix / "sysroot" / "tools" / triple / "bin"
        return tools

    @staticmethod
    def _tool_env(binutils: Path | None) -> dict | None:
        env = {}
        if binutils is not None:
            env["PATH"] = f"{binutils}{os.pathsep}{os.environ['PATH']}"
        return env or None

    @staticmethod
    def _require_linker_tools(binutils: Path, triple: str) -> tuple[Path, Path]:
        assembler, linker = binutils / f"{triple}-as", binutils / f"{triple}-ld"
        if not assembler.is_file() or not linker.is_file():
            raise FileNotFoundError(f"expected {assembler} and {linker}")
        return assembler, linker

    def _make(self, bdir: Path, phase: str, target: str, env: dict | None) -> None:
        self._run_logged(["make", f"-j{self.cfg.jobs}", target], phase, cwd=bdir, env=env)

    def _make_install(self, bdir: Path, phase: str, target: str, env: dict | None) -> None:
        self._run_logged(["make", f"-j{self.cfg.jobs}", target], phase, cwd=bdir, env=env)

    @staticmethod
    def _configure_state(bdir: Path) -> Path:
        return bdir / ".cte-configure.json"

    @staticmethod
    def _build_state(bdir: Path) -> Path:
        return bdir / ".cte-build.json"

    @staticmethod
    def _env_state(env: dict | None) -> dict[str, str]:
        state = {}
        for key, value in (env or {}).items():
            if key == "PATH":
                state["BINUTILS_PATH"] = value.split(os.pathsep, 1)[0]
            else:
                state[key] = value
        return dict(sorted(state.items()))

    def _configure_if_needed(
        self, bdir: Path, configure: list[str], phase: str, env: dict | None
    ) -> None:
        state_path = self._configure_state(bdir)
        state = {
            "configure": [str(arg) for arg in configure],
            "env": self._env_state(env),
        }
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            previous = None

        if previous == state and (bdir / "config.status").is_file():
            log.info(f"reusing GCC configure for {bdir.name}")
            return

        self._run_logged(configure, phase, cwd=bdir, env=env)
        state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _source_rev(self) -> str:
        return log.capture(["git", "rev-parse", "HEAD"], cwd=self.src)

    def _build_signature(self, configure: list[str], env: dict | None) -> dict:
        return {
            "source": self._source_rev(),
            "configure": [str(arg) for arg in configure],
            "env": self._env_state(env),
        }

    def _build_is_current(
        self, bdir: Path, configure: list[str], env: dict | None, compiler: Path
    ) -> bool:
        if not compiler.is_file():
            return False
        try:
            previous = json.loads(self._build_state(bdir).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        return previous == self._build_signature(configure, env)

    def _mark_build_current(self, bdir: Path, configure: list[str], env: dict | None) -> None:
        self._build_state(bdir).write_text(
            json.dumps(self._build_signature(configure, env), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

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

    def _build_one(self, a: Arch, coverage: bool = False) -> None:
        log.require("make")
        c = self.cfg.gcc
        triple = self.cfg.target_triple(a)
        bdir = self._build_root(coverage) / triple
        idir = self._install_root(coverage) / triple
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
        if coverage:
            configure.append("--enable-coverage=opt")

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
            compiler = idir / "bin" / f"{triple}-gcc"
            if self._build_is_current(bdir, configure, tool_env, compiler):
                log.info(f"reusing installed GCC for {a.name} ({triple})")
                return
            self._configure_if_needed(bdir, configure, "configure", tool_env)
            self._make(bdir, "build", "all", tool_env)
            self._make_install(bdir, "install", "install", tool_env)
            self._mark_build_current(bdir, configure, tool_env)
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
            compiler = idir / "bin" / f"{triple}-gcc"
            if self._build_is_current(bdir, configure, tool_env, compiler):
                log.info(f"reusing installed GCC for {a.name} ({triple})")
                return
            self._configure_if_needed(bdir, configure, "configure", tool_env)
            # all-gcc / install-gcc gives a working compiler without a full libc.
            self._make(bdir, "build", "all-gcc", tool_env)
            self._make_install(bdir, "install", "install-gcc", tool_env)
            self._mark_build_current(bdir, configure, tool_env)

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
        compiler = prefix / "bin" / f"{triple}-gcc"
        if self._build_is_current(bdir, configure, tool_env, compiler):
            log.info(f"reusing bootstrap GCC for {a.name} ({triple})")
            return prefix
        self._configure_if_needed(bdir, configure, "configure", tool_env)
        self._make(bdir, "build", "all-gcc", tool_env)
        self._make_install(bdir, "install", "install-gcc", tool_env)
        self._mark_build_current(bdir, configure, tool_env)
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
        self._make_install(bdir, "libgcc-install", "install-target-libgcc", tool_env)

    def _build_all(self, coverage: bool = False) -> None:
        for a in self.cfg.arches:
            kind = "native" if a.name == _host_arch_name() else "cross"
            label = "GCC coverage" if coverage else "GCC"
            log.info(f"building {label} ({kind}) for {a.name} ({a.gcc_triple})")
            self._build_one(a, coverage)

    def clean(self) -> None:
        for coverage in self._variants():
            self._clean_paths(self._build_root(coverage), self._install_root(coverage))

    def status(self) -> str:
        states = []
        for coverage in self._variants():
            label = "coverage" if coverage else "normal"
            install_root = self._install_root(coverage)
            installed = [
                a.name
                for a in self.cfg.arches
                if (
                    install_root
                    / self.cfg.target_triple(a)
                    / "bin"
                    / f"{self.cfg.target_triple(a)}-gcc"
                ).is_file()
            ]
            if installed:
                states.append(
                    f"{label}: installed for {', '.join(installed)} at {install_root}"
                )
            elif self._build_root(coverage).exists():
                states.append(f"{label}: build tree exists, not installed")
            elif self.src.exists():
                states.append(f"{label}: source fetched, not installed")
            else:
                states.append(f"{label}: not installed")
        return "; ".join(states)

    def _sync_and_build(self) -> None:
        self._sync()
        for coverage in self._variants():
            self._build_all(coverage)

    def install(self) -> None:
        log.info(f"installing GCC ({self.cfg.gcc.ref})")
        self._sync_and_build()
        log.info(f"GCC installed at {self.install_dir} ({self._describe_src()})")

    def update(self) -> None:
        log.info("updating GCC to trunk")
        self._sync_and_build()
        log.info(f"GCC updated to {self._describe_src()}")
