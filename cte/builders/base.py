"""Common building blocks for the component builders."""

from __future__ import annotations

from pathlib import Path

from .. import log
from ..config import Config


class Builder:
    """Base class. A component is built from source under ``build_dir`` and
    installed into its own subdirectory of ``prefix`` -- never onto PATH.
    """

    name = "component"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    # --- locations -------------------------------------------------------
    @property
    def src(self) -> Path:
        return self.cfg.src_dir / self.name

    @property
    def build(self) -> Path:
        return self.cfg.build_dir / self.name

    @property
    def install_dir(self) -> Path:
        return self.cfg.prefix / self.name

    # --- lifecycle (subclasses override) ---------------------------------
    def install(self) -> None:
        raise NotImplementedError

    def update(self) -> None:
        raise NotImplementedError

    def status(self) -> str:
        if (self.install_dir / "bin").exists():
            return f"installed at {self.install_dir}"
        if self.src.exists():
            return "source fetched, not installed"
        return "not installed"

    def clean(self) -> None:
        import shutil

        for p in (self.build, self.install_dir):
            if p.exists():
                log.step(f"removing {p}")
                shutil.rmtree(p)

    # --- shared git helpers ---------------------------------------------
    # Git options that make large clones over flaky links survive better:
    # a big send buffer, and a low-speed timeout so a stalled transfer fails
    # fast (and is retried) instead of hanging.
    _GIT_TUNING = [
        "-c", "http.postBuffer=1048576000",
        "-c", "http.lowSpeedLimit=1000",
        "-c", "http.lowSpeedTime=60",
    ]

    def _git_sync(self, repo: str, ref: str, shallow: bool) -> None:
        """Clone (if needed) and fast-forward the source tree to ``ref``."""
        log.require("git")
        import shutil

        if not (self.src / ".git").exists():
            log.info(f"cloning {self.name} ({ref})")
            self.src.parent.mkdir(parents=True, exist_ok=True)

            def _cleanup_partial() -> None:
                if self.src.exists():
                    log.step(f"removing partial clone {self.src}")
                    shutil.rmtree(self.src, ignore_errors=True)

            cmd = ["git", *self._GIT_TUNING, "clone"]
            if shallow:
                cmd += ["--depth", "1", "--branch", ref]
            cmd += [repo, str(self.src)]
            log.run_retry(cmd, on_retry=_cleanup_partial)
            if not shallow:
                log.run(["git", "checkout", ref], cwd=self.src)
        else:
            log.info(f"updating {self.name} to latest {ref}")
            depth = ["--depth", "1"] if shallow else []
            log.run_retry(
                ["git", *self._GIT_TUNING, "fetch", *depth, "origin", ref],
                cwd=self.src,
            )
            log.run(["git", "checkout", ref], cwd=self.src)
            log.run(["git", "reset", "--hard", f"origin/{ref}"], cwd=self.src)

    def _describe_src(self) -> str:
        try:
            return log.capture(
                ["git", "describe", "--always", "--dirty"], cwd=self.src
            )
        except Exception:
            return "unknown"
