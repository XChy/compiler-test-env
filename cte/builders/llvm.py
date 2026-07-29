"""LLVM/Clang trunk builder.

A single clang build serves every selected architecture, so the architecture
list only decides which backends are compiled into LLVM_TARGETS_TO_BUILD.
"""

from __future__ import annotations

from pathlib import Path
import shutil

from .. import arch, log
from .base import Builder


class LLVMBuilder(Builder):
    name = "llvm"

    def _sync(self) -> None:
        c = self.cfg.llvm
        self._git_sync(c.repo, c.source_ref, c.shallow)

    def _build_dir(self, coverage: bool):
        return self.cfg.build_dir / ("llvm-coverage" if coverage else "llvm")

    def _install_dir(self, coverage: bool):
        return self.cfg.prefix / ("llvm-coverage" if coverage else "llvm")

    def _variants(self) -> list[bool]:
        return [False, *([True] if self.cfg.llvm.coverage else [])]

    @staticmethod
    def _has_profile_runtime(clangxx: Path) -> bool:
        try:
            resource_dir = Path(log.capture([str(clangxx), "--print-resource-dir"]))
        except Exception:
            return False
        return bool(list((resource_dir / "lib").glob("**/libclang_rt.profile.a")))

    def _coverage_compiler_args(self) -> list[str]:
        """Return CMake compiler args for an instrumented coverage build.

        LLVM's coverage build links build-time utilities such as
        llvm-min-tblgen with -fprofile-instr-generate.  The Clang used for that
        build must provide libclang_rt.profile.a; stale normal installs can have
        a clang binary whose resource-dir version no longer matches the
        installed compiler-rt tree.
        """
        normal_bin = self._install_dir(False) / "bin"
        candidates: list[tuple[Path, Path]] = []
        normal_clang = normal_bin / "clang"
        normal_clangxx = normal_bin / "clang++"
        if normal_clang.is_file() and normal_clangxx.is_file():
            candidates.append((normal_clang, normal_clangxx))

        path_clang = shutil.which("clang")
        path_clangxx = shutil.which("clang++")
        if path_clang and path_clangxx:
            candidates.append((Path(path_clang), Path(path_clangxx)))

        for clang, clangxx in candidates:
            if self._has_profile_runtime(clangxx):
                return [
                    f"-DCMAKE_C_COMPILER={clang}",
                    f"-DCMAKE_CXX_COMPILER={clangxx}",
                ]
            log.warn(
                "skipping coverage bootstrap compiler without "
                f"libclang_rt.profile.a: {clangxx}"
            )

        raise RuntimeError(
            "LLVM coverage build requires a Clang with libclang_rt.profile.a. "
            "Clean/reinstall the normal LLVM tree, or put a working host "
            "clang/clang++ on PATH."
        )

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
            cmake_args += self._coverage_compiler_args()
            cmake_args += [
                "-DLLVM_BUILD_INSTRUMENTED_COVERAGE=ON",
                "-DLLVM_BUILD_LLVM_DYLIB=ON",
                "-DLLVM_LINK_LLVM_DYLIB=ON",
                "-DCLANG_LINK_CLANG_DYLIB=ON",
            ]
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
        log.info(f"installing LLVM/Clang ({self.cfg.llvm.source_ref})")
        self._sync_and_build()
        log.info(f"LLVM installed at {self.install_dir} ({self._describe_src()})")

    def update(self) -> None:
        log.info(f"updating LLVM/Clang ({self.cfg.llvm.source_ref})")
        self._sync_and_build()
        log.info(f"LLVM updated to {self._describe_src()}")
