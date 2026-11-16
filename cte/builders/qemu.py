"""Builders for stable upstream QEMU and RuyiSDK P-extension QEMU."""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from .. import log
from .base import Builder

_BASE_URL = "https://download.qemu.org/"
_RUYISDK_PATCH = (
    Path(__file__).resolve().parent.parent
    / "patches"
    / "ruyisdk-qemu-predsum-rs2.patch"
)


def latest_stable_version() -> str:
    """Scrape download.qemu.org for the highest released version."""
    with urllib.request.urlopen(_BASE_URL, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    versions = re.findall(r"qemu-(\d+\.\d+\.\d+)\.tar\.xz", html)
    if not versions:
        raise RuntimeError("could not determine latest QEMU version")
    return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))


class _QEMUBuilderBase(Builder):
    @property
    def options(self):
        raise NotImplementedError

    def _target_list(self) -> str:
        targets: list[str] = []
        for target in self.cfg.arches:
            if "user" in self.options.modes:
                targets.append(f"{target.qemu_user}-linux-user")
            if "system" in self.options.modes:
                targets.append(f"{target.qemu_system}-softmmu")
        return ",".join(dict.fromkeys(targets))

    def _build(self) -> None:
        log.require("ninja", "make", "python3")
        self.build.mkdir(parents=True, exist_ok=True)
        configure = [
            str(self.src / "configure"),
            f"--prefix={self.install_dir}",
            f"--target-list={self._target_list()}",
            *self.options.extra_configure_args,
        ]
        log.info(f"configuring {self.name} (details: configure.log)")
        self._run_logged(configure, "configure")
        log.info(f"building {self.name} (detailed compiler output is in build.log)")
        self._run_logged(["make", f"-j{self.cfg.jobs}"], "build")
        log.info(f"installing {self.name}")
        self._run_logged(["make", "install"], "install")


class QEMUBuilder(_QEMUBuilderBase):
    """Build a stable upstream QEMU release."""

    name = "qemu"

    @property
    def options(self):
        return self.cfg.qemu

    def _resolve_version(self) -> str:
        version = self.options.version
        if version == "latest":
            version = latest_stable_version()
            log.info(f"latest stable QEMU resolved to {version}")
        return version

    def _fetch(self, version: str) -> Path:
        tarball = f"qemu-{version}.tar.xz"
        url = f"{_BASE_URL}{tarball}"
        self.cfg.src_dir.mkdir(parents=True, exist_ok=True)
        dest = self.cfg.src_dir / tarball
        if not dest.exists():
            log.info(f"downloading {url}")
            urllib.request.urlretrieve(url, dest)
        marker = self.src / ".cte-upstream-version"
        if self.src.exists() and (
            not marker.exists() or marker.read_text().strip() != version
        ):
            log.info("replacing QEMU source tree for the selected version")
            shutil.rmtree(self.src)
        if not (self.src / "configure").exists():
            log.info(f"extracting {tarball}")
            with tarfile.open(dest) as archive:
                archive.extractall(self.cfg.src_dir)
            extracted = self.cfg.src_dir / f"qemu-{version}"
            if self.src.exists():
                shutil.rmtree(self.src)
            extracted.rename(self.src)
            marker.write_text(version + "\n")
        return self.src

    def install(self) -> None:
        version = self._resolve_version()
        log.info(f"installing QEMU {version}")
        self._fetch(version)
        self._build()
        log.info(f"QEMU {version} installed at {self.install_dir}")

    def update(self) -> None:
        version = self._resolve_version()
        log.info(f"updating QEMU to {version}")
        if self.build.exists():
            shutil.rmtree(self.build)
        self._fetch(version)
        self._build()
        log.info(f"QEMU updated to {version}")


class RuyiQEMUBuilder(_QEMUBuilderBase):
    """Build RuyiSDK QEMU with RISC-V P support and the bundled fix."""

    name = "qemu-ruyisdk"

    @property
    def options(self):
        return self.cfg.qemu_ruyisdk

    def _apply_patch(self) -> None:
        if not _RUYISDK_PATCH.is_file():
            raise FileNotFoundError(
                f"bundled RuyiSDK QEMU patch not found: {_RUYISDK_PATCH}"
            )
        check = subprocess.run(
            ["git", "apply", "--check", str(_RUYISDK_PATCH)],
            cwd=self.src,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if check.returncode == 0:
            log.info("applying RuyiSDK QEMU predsum RV64 fix")
            log.run(["git", "apply", str(_RUYISDK_PATCH)], cwd=self.src)
            return
        reverse = subprocess.run(
            ["git", "apply", "--reverse", "--check", str(_RUYISDK_PATCH)],
            cwd=self.src,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if reverse.returncode == 0:
            log.info("RuyiSDK QEMU predsum RV64 fix is already present")
            return
        log.run(["git", "apply", "--check", str(_RUYISDK_PATCH)], cwd=self.src)

    def _fetch(self) -> None:
        self._git_sync(self.options.repo, self.options.ref, self.options.shallow)
        self._apply_patch()

    def _write_p_wrappers(self) -> None:
        bindir = self.install_dir / "bin"
        for target in self.cfg.arches:
            if not target.name.startswith("riscv"):
                continue
            qemu = f"qemu-{target.qemu_user}"
            wrapper = bindir / f"{qemu}-p"
            wrapper.write_text(
                "#!/bin/sh\n"
                f'exec "$(dirname "$0")/{qemu}" -cpu max,x-p=true "$@"\n'
            )
            wrapper.chmod(0o755)

    def install(self) -> None:
        log.info(f"installing RuyiSDK QEMU ({self.options.ref})")
        self._fetch()
        self._build()
        self._write_p_wrappers()
        log.info(f"RuyiSDK QEMU installed at {self.install_dir}")

    def update(self) -> None:
        log.info(f"updating RuyiSDK QEMU ({self.options.ref})")
        self._fetch()
        if self.build.exists():
            shutil.rmtree(self.build)
        self._build()
        self._write_p_wrappers()
        log.info("RuyiSDK QEMU updated")
