"""Common building blocks for the component builders."""

from __future__ import annotations

import shutil
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
        self._clean_paths(self.build, self.install_dir)

    def _clean_paths(self, *paths: Path) -> None:
        for path in paths:
            if path.exists():
                log.step(f"removing {path}")
                shutil.rmtree(path)

    def _run_logged(
        self, cmd: list[str], phase: str, cwd: Path | None = None, env: dict | None = None
    ) -> None:
        """Run a noisy build stage with its full output kept beside the build."""
        directory = cwd or self.build
        log.run_to_log(cmd, directory / f"{phase}.log", cwd=directory, env=env)

    # --- shared git helpers ---------------------------------------------
    # Git options shared by large source syncs.
    _GIT_TUNING = [
        "-c", "http.postBuffer=1048576000",
    ]

    def _git_sync(self, repo: str, ref: str, shallow: bool) -> None:
        """Clone or fetch the source tree, then checkout ``ref``.

        ``ref`` may be a branch, tag, or commit-ish.  In particular, pinned
        release tags do not have an ``origin/<ref>`` tracking branch, so always
        checkout the fetched object instead of assuming branch semantics.
        """
        log.require("git")
        import shutil

        if not (self.src / ".git").exists():
            log.info(f"cloning {self.name} ({ref})")
            self.src.parent.mkdir(parents=True, exist_ok=True)

            def _cleanup_partial() -> None:
                if self.src.exists():
                    log.step(f"removing partial clone {self.src}")
                    shutil.rmtree(self.src, ignore_errors=True)

            cmd = ["git", *self._GIT_TUNING, "clone", "--no-checkout"]
            if shallow:
                cmd += ["--depth", "1"]
            cmd += [repo, str(self.src)]
            log.run_retry(cmd, on_retry=_cleanup_partial)
        else:
            log.info(f"syncing {self.name} ({ref})")

        depth = ["--depth", "1"] if shallow else []
        log.run_retry(["git", *self._GIT_TUNING, "fetch", *depth, "origin", ref], cwd=self.src)
        log.run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=self.src)
        log.run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=self.src)

    def _describe_src(self) -> str:
        try:
            return log.capture(
                ["git", "describe", "--always", "--dirty"], cwd=self.src
            )
        except Exception:
            return "unknown"
