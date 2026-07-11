"""LLVM/Clang trunk builder.

A single clang build serves every selected architecture, so the architecture
list only decides which backends are compiled into LLVM_TARGETS_TO_BUILD.
"""

from __future__ import annotations

from .. import arch, log
from .base import Builder


class LLVMBuilder(Builder):
    name = "llvm"

    def _sync(self) -> None:
        c = self.cfg.llvm
        self._git_sync(c.repo, c.ref, c.shallow)

    def _configure_and_build(self) -> None:
        log.require("cmake", "ninja")
        c = self.cfg.llvm
        targets = arch.llvm_targets(self.cfg.arches)
        self.build.mkdir(parents=True, exist_ok=True)

        cmake_args = [
            "cmake", "-G", "Ninja",
            "-S", str(self.src / "llvm"),
            "-B", str(self.build),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={self.install_dir}",
            f"-DLLVM_ENABLE_PROJECTS={';'.join(c.projects)}",
            f"-DLLVM_TARGETS_TO_BUILD={targets}",
            "-DLLVM_ENABLE_ASSERTIONS=ON",
            "-DLLVM_INCLUDE_TESTS=OFF",
            "-DLLVM_INCLUDE_EXAMPLES=OFF",
            "-DLLVM_INCLUDE_BENCHMARKS=OFF",
            "-DLLVM_BUILD_UTILS=OFF",
            "-DLLVM_ENABLE_BINDINGS=OFF",
        ]
        # ccache makes subsequent trunk updates substantially cheaper while
        # remaining optional for hosts that do not provide it.
        import shutil
        if shutil.which("ccache"):
            cmake_args += [
                "-DCMAKE_C_COMPILER_LAUNCHER=ccache",
                "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
            ]
        if c.runtimes:
            cmake_args.append(f"-DLLVM_ENABLE_RUNTIMES={';'.join(c.runtimes)}")
        cmake_args += c.extra_cmake_args

        log.info("configuring LLVM (details: configure.log)")
        self._run_logged(cmake_args, "configure")
        log.info("building LLVM (details: build.log)")
        self._run_logged(["ninja", "-C", str(self.build), f"-j{self.cfg.jobs}"], "build")
        log.info("installing LLVM (details: install.log)")
        self._run_logged(["ninja", "-C", str(self.build), "install"], "install")

    def install(self) -> None:
        log.info(f"installing LLVM/Clang ({self.cfg.llvm.ref})")
        self._sync()
        self._configure_and_build()
        log.info(f"LLVM installed at {self.install_dir} ({self._describe_src()})")

    def update(self) -> None:
        log.info("updating LLVM/Clang to trunk")
        self._sync()
        self._configure_and_build()
        log.info(f"LLVM updated to {self._describe_src()}")
