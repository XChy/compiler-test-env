"""Build project-managed Linux/musl sysroots for cross targets.

The stages deliberately mirror a small crosstool-NG build: target binutils,
Linux UAPI headers, a headerless bootstrap GCC, then musl.  The final GCC build
is performed by :class:`GCCBuilder` after this component has completed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import log
from ..arch import Arch
from .base import Builder
from .gcc import GCCBuilder


_LINUX_ARCH = {
    "x86_64": "x86", "aarch64": "arm64", "arm": "arm",
    "riscv64": "riscv", "riscv32": "riscv", "mips64": "mips",
    "mips": "mips", "powerpc64": "powerpc", "powerpc64le": "powerpc",
    "s390x": "s390", "loongarch64": "loongarch",
}


class SysrootBuilder(Builder):
    name = "sysroot"

    def _source(self, name: str) -> Path:
        return self.cfg.src_dir / "sysroot" / name

    def status(self) -> str:
        if (self.install_dir / "tools").exists():
            return f"installed at {self.install_dir}"
        if (self.cfg.src_dir / "sysroot").exists():
            return "source fetched, not installed"
        return "not installed"

    def _sync_tree(self, name: str, repo: str, ref: str) -> Path:
        path = self._source(name)
        if not (path / ".git").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            cmd = ["git", *self._GIT_TUNING, "clone"]
            if self.cfg.sysroot.shallow:
                cmd += ["--depth", "1", "--branch", ref]
            cmd += [repo, str(path)]
            log.run_retry(cmd, on_retry=lambda: shutil.rmtree(path, ignore_errors=True))
        else:
            depth = ["--depth", "1"] if self.cfg.sysroot.shallow else []
            log.run_retry(["git", *self._GIT_TUNING, "fetch", *depth, "origin", ref], cwd=path)
            log.run(["git", "checkout", ref], cwd=path)
            log.run(["git", "reset", "--hard", f"origin/{ref}"], cwd=path)
        return path

    def _build_binutils(self, target: Arch) -> Path:
        triple = self.cfg.target_triple(target)
        source = self._source("binutils")
        build = self.build / "binutils" / triple
        prefix = self.install_dir / "tools" / triple
        build.mkdir(parents=True, exist_ok=True)
        log.run([
            str(source / "configure"), f"--target={triple}", f"--prefix={prefix}",
            "--disable-nls", "--disable-werror", "--disable-multilib",
        ], cwd=build)
        log.run(["make", f"-j{self.cfg.jobs}"], cwd=build)
        log.run(["make", "install"], cwd=build)
        return prefix / "bin"

    def _install_linux_headers(self, target: Arch, sysroot: Path) -> None:
        try:
            linux_arch = _LINUX_ARCH[target.name]
        except KeyError as exc:
            raise RuntimeError(
                f"managed musl sysroots currently support: {', '.join(_LINUX_ARCH)}; "
                f"use [sysroot] enabled = false with an external sysroot for {target.name}"
            ) from exc
        log.run([
            "make", f"ARCH={linux_arch}", "headers_install",
            f"INSTALL_HDR_PATH={sysroot / 'usr'}",
        ], cwd=self._source("linux"))

    def _configure_musl(self, target: Arch, bootstrap: Path, sysroot: Path) -> Path:
        triple = self.cfg.target_triple(target)
        build = self.build / "musl" / triple
        build.mkdir(parents=True, exist_ok=True)
        cross = bootstrap / "bin" / f"{triple}-"
        log.run([
            str(self._source("musl") / "configure"), f"--target={triple}",
            "--prefix=/usr", "--syslibdir=/lib",
        ], cwd=build, env={"CROSS_COMPILE": str(cross)})
        # This is the bridge between the headerless bootstrap compiler and
        # libgcc: GCC's libgcc sources include standard C headers.
        log.run(["make", f"DESTDIR={sysroot}", "install-headers"], cwd=build,
                env={"CROSS_COMPILE": str(cross)})
        return build

    def _build_musl(self, bootstrap: Path, sysroot: Path, build: Path) -> None:
        triple = build.name
        cross = bootstrap / "bin" / f"{triple}-"
        compiler = bootstrap / "bin" / f"{triple}-gcc"
        libgcc = Path(log.capture([str(compiler), "-print-libgcc-file-name"]))
        if not libgcc.is_file():
            raise FileNotFoundError(
                f"bootstrap libgcc is missing: {libgcc}; "
                "the target libgcc stage did not complete"
            )
        # musl defaults to LIBCC=-lgcc.  Pass the resolved archive explicitly:
        # the bootstrap compiler's library search path differs from the final
        # sysroot and is easily lost in recursive make invocations.
        log.run(["make", f"-j{self.cfg.jobs}", f"LIBCC={libgcc}"], cwd=build,
                env={"CROSS_COMPILE": str(cross)})
        log.run(["make", f"DESTDIR={sysroot}", "install"], cwd=build,
                env={"CROSS_COMPILE": str(cross)})

    def install(self) -> None:
        if self.cfg.sysroot.libc != "musl":
            raise RuntimeError("only the managed musl sysroot is implemented")
        log.require("git", "make")
        c = self.cfg.sysroot
        self._sync_tree("binutils", c.binutils_repo, c.binutils_ref)
        self._sync_tree("linux", c.linux_repo, c.linux_ref)
        self._sync_tree("musl", c.musl_repo, c.musl_ref)
        gcc = GCCBuilder(self.cfg)
        gcc._sync()
        for target in self.cfg.arches:
            if target.name == self.cfg.host_architecture:
                continue
            triple = self.cfg.target_triple(target)
            log.info(f"building managed musl sysroot for {target.name} ({triple})")
            tools = self._build_binutils(target)
            sysroot = self.install_dir / triple
            self._install_linux_headers(target, sysroot)
            bootstrap = gcc.build_bootstrap(target, tools, sysroot)
            musl_build = self._configure_musl(target, bootstrap, sysroot)
            gcc.build_bootstrap_libgcc(target)
            self._build_musl(bootstrap, sysroot, musl_build)
        log.info(f"managed sysroots installed at {self.install_dir}")

    def update(self) -> None:
        self.install()
