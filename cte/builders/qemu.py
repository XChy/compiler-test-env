"""QEMU builder.

Installs the latest *stable* QEMU release (or a pinned version) so the freshly
built compilers can be exercised across architectures via ``qemu-<arch>``
(linux-user) and/or ``qemu-system-<arch>``.
"""

from __future__ import annotations

import re
import shutil
import tarfile
import urllib.request
from pathlib import Path

from .. import log
from .base import Builder

_BASE_URL = "https://download.qemu.org/"


def latest_stable_version() -> str:
    """Scrape download.qemu.org for the highest released version."""
    with urllib.request.urlopen(_BASE_URL, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    versions = re.findall(r"qemu-(\d+\.\d+\.\d+)\.tar\.xz", html)
    if not versions:
        raise RuntimeError("could not determine latest QEMU version")
    return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))


class QEMUBuilder(Builder):
    name = "qemu"

    def _resolve_version(self) -> str:
        v = self.cfg.qemu.version
        if v == "latest":
            v = latest_stable_version()
            log.info(f"latest stable QEMU resolved to {v}")
        return v

    def _fetch(self, version: str) -> Path:
        tarball = f"qemu-{version}.tar.xz"
        url = f"{_BASE_URL}{tarball}"
        self.cfg.src_dir.mkdir(parents=True, exist_ok=True)
        dest = self.cfg.src_dir / tarball
        if not dest.exists():
            log.info(f"downloading {url}")
            urllib.request.urlretrieve(url, dest)
        srcdir = self.src
        if not (srcdir / "configure").exists():
            log.info(f"extracting {tarball}")
            with tarfile.open(dest) as tf:
                tf.extractall(self.cfg.src_dir)
            extracted = self.cfg.src_dir / f"qemu-{version}"
            if srcdir.exists():
                shutil.rmtree(srcdir)
            extracted.rename(srcdir)
        return srcdir

    def _target_list(self) -> str:
        targets: list[str] = []
        modes = self.cfg.qemu.modes
        for a in self.cfg.arches:
            if "user" in modes:
                targets.append(f"{a.qemu_user}-linux-user")
            if "system" in modes:
                targets.append(f"{a.qemu_system}-softmmu")
        # de-dup, keep order
        seen: list[str] = []
        for t in targets:
            if t not in seen:
                seen.append(t)
        return ",".join(seen)

    def _build(self) -> None:
        log.require("ninja", "make", "python3")
        self.build.mkdir(parents=True, exist_ok=True)
        configure = [
            str(self.src / "configure"),
            f"--prefix={self.install_dir}",
            f"--target-list={self._target_list()}",
        ]
        configure += self.cfg.qemu.extra_configure_args
        log.info("configuring QEMU (details: configure.log)")
        self._run_logged(configure, "configure")
        log.info("building QEMU targets (detailed compiler output is in build.log)")
        self._run_logged(["make", f"-j{self.cfg.jobs}"], "build")
        log.info("installing QEMU")
        self._run_logged(["make", "install"], "install")

    def install(self) -> None:
        version = self._resolve_version()
        log.info(f"installing QEMU {version}")
        self._fetch(version)
        self._build()
        log.info(f"QEMU {version} installed at {self.install_dir}")

    def update(self) -> None:
        # For a stable release, "update" means rebuild against the newest stable.
        version = self._resolve_version()
        log.info(f"updating QEMU to {version}")
        if self.build.exists():
            shutil.rmtree(self.build)
        self._fetch(version)
        self._build()
        log.info(f"QEMU updated to {version}")
