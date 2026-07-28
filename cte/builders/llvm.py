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

    def _build_dir(self, coverage: bool):
        return self.cfg.build_dir / ("llvm-coverage" if coverage else "llvm")

    def _install_dir(self, coverage: bool):
        return self.cfg.prefix / ("llvm-coverage" if coverage else "llvm")

    def _variants(self) -> list[bool]:
        return [False, *([True] if self.cfg.llvm.coverage else [])]

    def _configure_and_build(self, coverage: bool = False) -> None:
        log.require("cmake", "ninja")
        c = self.cfg.llvm
        targets = arch.llvm_targets(self.cfg.arches)
        build = self._build_dir(coverage)
        install_dir = self._install_dir(coverage)
        build.mkdir(parents=True, exist_ok=True)

        cmake_args = [
            "cmake", "-G", "Ninja",
            "-S", str(self.src / "llvm"),
            "-B", str(build),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={install_dir}",
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
        if coverage:
            normal_bin = self._install_dir(False) / "bin"
            clang = normal_bin / "clang"
            clangxx = normal_bin / "clang++"
            if clang.is_file() and clangxx.is_file():
                cmake_args += [
                    f"-DCMAKE_C_COMPILER={clang}",
                    f"-DCMAKE_CXX_COMPILER={clangxx}",
                ]
            else:
                log.warn(
                    "LLVM coverage build works best with Clang; "
                    f"expected {clang} and {clangxx}"
                )
            cmake_args.append("-DLLVM_BUILD_INSTRUMENTED_COVERAGE=ON")
        cmake_args += c.extra_cmake_args

        label = "LLVM coverage" if coverage else "LLVM"
        log.info(f"configuring {label} (details: configure.log)")
        self._run_logged(cmake_args, "configure", cwd=build)
        log.info(f"building {label} (details: build.log)")
        self._run_logged(["ninja", "-C", str(build), f"-j{self.cfg.jobs}"], "build", cwd=build)
        log.info(f"installing {label} (details: install.log)")
        self._run_logged(["ninja", "-C", str(build), "install"], "install", cwd=build)

    def clean(self) -> None:
        for coverage in self._variants():
            self._clean_paths(self._build_dir(coverage), self._install_dir(coverage))

    def status(self) -> str:
        states = []
        for coverage in self._variants():
            label = "coverage" if coverage else "normal"
            install_dir = self._install_dir(coverage)
            build = self._build_dir(coverage)
            if (install_dir / "bin").exists():
                states.append(f"{label}: installed at {install_dir}")
            elif build.exists():
                states.append(f"{label}: build tree exists, not installed")
            elif self.src.exists():
                states.append(f"{label}: source fetched, not installed")
            else:
                states.append(f"{label}: not installed")
        return "; ".join(states)

    def _sync_and_build(self) -> None:
        self._sync()
        for coverage in self._variants():
            self._configure_and_build(coverage)

    def install(self) -> None:
        log.info(f"installing LLVM/Clang ({self.cfg.llvm.ref})")
        self._sync_and_build()
        log.info(f"LLVM installed at {self.install_dir} ({self._describe_src()})")

    def update(self) -> None:
        log.info("updating LLVM/Clang to trunk")
        self._sync_and_build()
        log.info(f"LLVM updated to {self._describe_src()}")
