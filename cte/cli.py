"""Command-line interface for cte (compiler-test-env)."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from . import arch, builders, config as config_mod, env, log
from .config import Config


def _select(cfg: Config, components: list[str]) -> list[str]:
    """Resolve a user component selection against what is enabled."""
    if not components or components == ["all"]:
        return cfg.enabled_components()
    for c in components:
        if c not in builders.all_names():
            raise SystemExit(f"unknown component: {c}")
    if "gcc" in components and cfg.sysroot.enabled and "sysroot" not in components:
        return ["sysroot", *components]
    return components


def cmd_install(cfg: Config, args) -> None:
    for name in _select(cfg, args.components):
        builders.get(name, cfg).install()
    path = env.write(cfg)
    log.info(f"wrote {path} -- run `source {path}` to use the tools")


def cmd_update(cfg: Config, args) -> None:
    for name in _select(cfg, args.components):
        builders.get(name, cfg).update()
    env.write(cfg)


def cmd_clean(cfg: Config, args) -> None:
    for name in _select(cfg, args.components):
        builders.get(name, cfg).clean()


def cmd_status(cfg: Config, args) -> None:
    print(f"work dir:      {cfg.work_dir}")
    print(f"prefix:        {cfg.prefix}")
    print(f"src dir:       {cfg.src_dir}")
    print(f"build dir:     {cfg.build_dir}")
    print(f"architectures: {', '.join(cfg.architectures)}")
    print(f"jobs:          {cfg.jobs}")
    print("components:")
    for name in builders.all_names():
        enabled = name in cfg.enabled_components()
        mark = "x" if enabled else " "
        state = builders.get(name, cfg).status() if enabled else "disabled"
        print(f"  [{mark}] {name:5} {state}")


def cmd_env(cfg: Config, args) -> None:
    if args.write:
        path = env.write(cfg)
        log.info(f"wrote {path}")
    else:
        sys.stdout.write(env.render(cfg))


def cmd_list_arch(cfg: Config, args) -> None:
    print(f"{'name':14} {'llvm':10} {'gcc triple':24} {'qemu-user'}")
    for a in arch.ARCHES.values():
        print(f"{a.name:14} {a.llvm_target:10} {a.gcc_triple:24} {a.qemu_user}")


def cmd_verify(cfg: Config, args) -> None:
    """Prove configured cross targets can link a minimal Linux executable."""
    source = "int main(void) { return 0; }\n"
    checked = 0
    with tempfile.TemporaryDirectory(prefix="cte-verify-") as tmp:
        tmpdir = Path(tmp)
        for target in cfg.arches:
            if target.name == "x86_64" and not cfg.sysroot_for(target):
                # A native compiler can use host headers; preserve that useful
                # default while requiring explicit sysroots for cross targets.
                pass
            elif not cfg.sysroot_for(target):
                print(f"SKIP {target.name}: no [gcc.sysroots].{target.name} configured")
                continue
            sysroot = cfg.sysroot_for(target)
            if sysroot and not sysroot.is_dir():
                raise FileNotFoundError(f"{target.name}: sysroot does not exist: {sysroot}")
            commands: list[tuple[str, list[str]]] = []
            triple = cfg.target_triple(target)
            for label, root in (
                ("gcc", cfg.prefix / "gcc"),
                ("gcc-coverage", cfg.prefix / "gcc-coverage"),
            ):
                if label == "gcc-coverage" and not cfg.gcc.coverage:
                    continue
                gcc = root / triple / "bin" / f"{triple}-gcc"
                if gcc.is_file():
                    if target.name == "x86_64" or cfg.sysroot.enabled or cfg.binutils_for(target):
                        commands.append((label, [str(gcc), "-static"]))
                    else:
                        print(
                            f"SKIP {target.name} ({label}): "
                            f"no [gcc.binutils].{target.name} configured"
                        )
            for label, root in (
                ("clang", cfg.prefix / "llvm"),
                ("clang-coverage", cfg.prefix / "llvm-coverage"),
            ):
                if label == "clang-coverage" and not cfg.llvm.coverage:
                    continue
                clang = root / "bin" / "clang"
                if not clang.is_file():
                    continue
                command = [str(clang), f"--target={triple}", "-fuse-ld=lld", "-static"]
                if sysroot:
                    command.append(f"--sysroot={sysroot}")
                gcc_root = cfg.prefix / "gcc" / triple
                bootstrap_root = cfg.prefix / "sysroot" / "bootstrap" / triple
                if gcc_root.is_dir():
                    command.append(f"--gcc-toolchain={gcc_root}")
                elif bootstrap_root.is_dir():
                    command.append(f"--gcc-toolchain={bootstrap_root}")
                commands.append((label, command))
            if not commands:
                print(f"SKIP {target.name}: no installed compiler")
                continue
            for name, command in commands:
                output = tmpdir / f"{target.name}-{name}"
                subprocess.run(
                    [*command, "-x", "c", "-", "-o", str(output)],
                    input=source, text=True, check=True,
                )
                runner = cfg.prefix / "qemu" / "bin" / f"qemu-{target.qemu_user}"
                if target.name == cfg.host_architecture:
                    subprocess.run([str(output)], check=True)
                    result = "linked and ran natively"
                elif runner.is_file():
                    subprocess.run([str(runner), str(output)], check=True)
                    result = "linked and ran with QEMU"
                else:
                    result = "linked (QEMU not installed; not run)"
                print(f"OK   {target.name} ({name}): {result}")
                checked += 1
    if not checked:
        raise RuntimeError("no target was verified; configure sysroots and install a compiler")


def cmd_set(args) -> None:
    resolved = config_mod.set_default_config(args.config_path)
    log.info(f"default config set to {resolved}")


def cmd_config(args) -> None:
    path = config_mod.resolve_config_path(args.config)
    source = "command line" if args.config else (
        "saved default" if config_mod.config_pointer_path().exists() else "built-in default"
    )
    print(f"{path} ({source})")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cte",
        description="Set up testing environments for the latest C compilers "
        "across multiple architectures.",
    )
    p.add_argument(
        "-c", "--config", type=Path, default=None,
        help="path to config file (overrides saved/default config)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    comp_help = "components to act on (sysroot/llvm/gcc/qemu/all); default: enabled ones"

    s = sub.add_parser("install", help="build and install components from source")
    s.add_argument("components", nargs="*", help=comp_help)
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("update", help="pull latest sources and rebuild")
    s.add_argument("components", nargs="*", help=comp_help)
    s.set_defaults(func=cmd_update)

    s = sub.add_parser("clean", help="remove build and install trees")
    s.add_argument("components", nargs="*", help=comp_help)
    s.set_defaults(func=cmd_clean)

    s = sub.add_parser("status", help="show install state")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("env", help="print (or write) the activation script")
    s.add_argument("--write", action="store_true", help="write prefix/activate.sh")
    s.set_defaults(func=cmd_env)

    s = sub.add_parser("list-arch", help="list known architectures")
    s.set_defaults(func=cmd_list_arch)

    s = sub.add_parser("verify", help="link and, when available, run each configured target")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("set", help="persist the default config for future commands")
    s.add_argument("config_path", type=Path, help="config file to use by default")
    s.set_defaults(func=cmd_set, needs_config=False)

    s = sub.add_parser("config", help="show the config path selected by default")
    s.set_defaults(func=cmd_config, needs_config=False)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if getattr(args, "needs_config", True):
            cfg = config_mod.load(config_mod.resolve_config_path(args.config))
            args.func(cfg, args)
        else:
            args.func(args)
    except KeyboardInterrupt:
        log.error("interrupted")
        return 130
    except subprocess.CalledProcessError as exc:
        # A failed compile/clone often means a half-finished tree from an
        # interrupted run; resuming on top of it can fail confusingly.
        log.error(f"command failed: {' '.join(map(str, exc.cmd))}")
        log.warn("if a previous build was interrupted, the build tree may be "
                 "stale -- try `cte clean <component>` and reinstall")
        return 1
    except Exception as exc:  # surface a clean message, not a traceback
        log.error(str(exc))
        return 1
    return 0
