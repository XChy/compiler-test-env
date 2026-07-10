# compiler-test-env

A quick helper for setting up testing environments for the **latest** C
compilers across **multiple architectures**.

It builds bleeding-edge toolchains from source — LLVM/Clang `main` and GCC
`trunk` — and a recent stable **QEMU**, so you can compile for many targets and
run the results without juggling distro packages. Everything is installed into a
local prefix and is **never added to your `PATH` automatically**.

## Why

When you're testing compilers you need *trunk*, not whatever the distro ships,
and you usually need it for several architectures at once. This tool wraps the
clone/configure/build/install dance behind a small, configurable CLI and keeps
the toolchains isolated so they can't shadow your system compilers.

## Highlights

- **Latest by design** — LLVM `main`, GCC `master`; `cte update` re-syncs to
  the newest trunk and rebuilds.
- **Multi-arch** — pick architectures in config; a single Clang build covers all
  of them, GCC builds one cross compiler per target.
- **Stable QEMU** — auto-resolves the latest stable QEMU release (or pin one)
  and builds the `linux-user` / `system` targets for your architectures.
- **Sanitizers by default** — LLVM builds `compiler-rt`, and GCC builds
  `libsanitizer` for the native target (and for cross targets with a sysroot),
  so asan/ubsan/tsan and friends are available out of the box.
- **Presets** — drop-in configs in `configs/` for x86-64, a common multi-arch
  set, or everything.
- **Never on PATH** — toolchains live under `toolchains/`; opt in per-shell with
  a generated `activate.sh`, or use the `CLANG` / `GCC_<arch>` / `QEMU_<arch>`
  variables it exports.
- **Configurable** — one `config.toml`; sensible defaults.

## Requirements

Host tools (checked at build time, install with your package manager):

- `git`, `cmake`, `ninja`, `make`, a host C/C++ compiler, `python3`
- Python **3.9+** (`tomli` is pulled in automatically on < 3.11)
- A **GitHub SSH key** — the default LLVM/GCC repos use SSH
  (`git@github.com:...`). Switch any `repo =` to its `https://` form if you'd
  rather use HTTPS.

### Cross executable requirements

Selecting an architecture builds its code generator; that alone does **not**
make a Linux executable linkable.  By default CTE now builds and owns a musl
sysroot for every non-native target, following the crosstool-NG stages:
target `as`/`ld`, Linux UAPI headers, a C library, its development
headers/libraries, the dynamic loader, and `crt1.o`, `crti.o`, `crtn.o`.
This produces `aarch64-linux-musl` and `riscv64-linux-musl` executables. Set
`[sysroot].enabled = false` only when supplying an external sysroot and target
binutils from the same target distribution and ABI.
For example, an `aarch64-linux-gnu` compiler must not be paired with an
`aarch64-musl` or RISC-V sysroot.

For an external sysroot, configure paths per architecture (a single shared
`--with-sysroot` argument is unsafe for a multi-arch build):

```toml
[gcc.sysroots]
aarch64 = "/opt/sysroots/aarch64-linux-gnu"
riscv64 = "/opt/sysroots/riscv64-linux-gnu"

[gcc.binutils]
aarch64 = "/opt/cross/aarch64-linux-gnu/bin"
riscv64 = "/opt/cross/riscv64-linux-gnu/bin"
```

Then build from a clean GCC tree and prove the result links:

```bash
cte -c config.toml clean gcc llvm
cte -c config.toml install sysroot gcc llvm qemu
cte -c config.toml verify
```

`verify` links a minimal executable with every installed GCC/Clang target.
It intentionally skips a cross target without a configured sysroot rather
than claiming compile-only support is executable support.  To run dynamically
linked results with QEMU, use the same sysroot as its loader prefix, e.g.
`$QEMU_AARCH64 -L /opt/sysroots/aarch64-linux-gnu ./hello`.

## Install

```bash
git clone <this-repo> && cd compiler-test-env
pip install -e .            # provides the `cte` command
# ...or run without installing:
python3 -m cte --help
```

## Quick start

It runs with zero setup — without a `config.toml` it uses built-in defaults
(LLVM `main` + latest stable QEMU, targeting `x86_64`):

```bash
cte status                              # works out of the box
cte list-arch                           # see supported architectures
cte install                             # build everything that's enabled
```

To customise (architectures, enable GCC, etc.), drop in a config:

```bash
cp config.example.toml config.toml
$EDITOR config.toml
```

### Ready-made presets

The `configs/` folder has presets you can point `-c` at directly (all enable
Clang + GCC trunk with sanitizers via `compiler-rt`):

| Preset | Architectures | QEMU |
| --- | --- | --- |
| [`configs/x86_64.toml`](configs/x86_64.toml) | `x86_64` | off (runs natively) |
| [`configs/common.toml`](configs/common.toml) | `x86_64`, `riscv64`, `aarch64` | linux-user |
| [`configs/all.toml`](configs/all.toml) | every supported architecture | linux-user |

```bash
cte -c configs/common.toml install
```

Use the freshly built tools in your current shell only (this is the *opt-in*
PATH step — nothing is global):

```bash
source toolchains/activate.sh

"$CLANG" --target=aarch64-linux-musl --sysroot="$SYSROOT_AARCH64" -O2 hello.c -o hello
"$QEMU_AARCH64" -L "$SYSROOT_AARCH64" ./hello
```

Or skip `PATH` entirely and call tools by their exported variables:

```bash
"$CLANG" --target=riscv64-linux-musl --sysroot="$SYSROOT_RISCV64" -c foo.c
"$GCC_RISCV64" -S foo.c            # if GCC is enabled
"$QEMU_RISCV64" ./a.out
```

## Commands

| Command | Description |
| --- | --- |
| `cte install [sysroot\|llvm\|gcc\|qemu\|all]` | Clone/fetch sources, configure, build, install into the prefix. |
| `cte update  [sysroot\|llvm\|gcc\|qemu\|all]` | Re-sync to latest trunk / newest stable QEMU and rebuild. |
| `cte status` | Show enabled components and install state. |
| `cte env [--write]` | Print the activation script (or write `toolchains/activate.sh`). |
| `cte clean  [sysroot\|llvm\|gcc\|qemu\|all]` | Remove build and install trees. |
| `cte list-arch` | List supported architectures and their target identifiers. |
| `cte verify` | Link a minimal executable for every configured target with an installed compiler. |

With no component argument, commands act on whatever is `enabled` in the config.
Pass `-c/--config` to use a config file other than `./config.toml`.

## Configuration

Everything lives in `config.toml` (copy from `config.example.toml`). Key knobs:

```toml
[general]
prefix = "toolchains"                       # install root — never on PATH
jobs = 0                                     # 0 = autodetect CPUs
architectures = ["x86_64", "aarch64", "riscv64"]

[sysroot]
enabled = true                              # managed Linux headers + musl
libc = "musl"                               # cross triples become *-linux-musl

[llvm]
enabled = true
ref = "main"                                 # trunk
projects = ["clang", "lld"]

[gcc]
enabled = true                               # one cross compiler per arch
ref = "master"                               # trunk

[gcc.sysroots]
# aarch64 = "/opt/sysroots/aarch64-linux-gnu"
# riscv64 = "/opt/sysroots/riscv64-linux-gnu"

[gcc.binutils]
# aarch64 = "/opt/cross/aarch64-linux-gnu/bin"
# riscv64 = "/opt/cross/riscv64-linux-gnu/bin"

[qemu]
enabled = true
version = "latest"                           # newest stable, or e.g. "9.1.0"
modes = ["user"]                             # "user" and/or "system"
```

Supported architectures (`cte list-arch`): `x86_64`, `aarch64`, `arm`,
`riscv64`, `riscv32`, `mips64`, `mips`, `powerpc64`, `powerpc64le`, `s390x`,
`loongarch64`. Add more by extending the table in
[`cte/arch.py`](cte/arch.py).

## How it's laid out

```
cte/
  cli.py            # argparse entry point (cte ...)
  config.py         # config.toml loading + validation
  arch.py           # architecture registry (llvm/gcc/qemu identifiers)
  env.py            # activate.sh generation (opt-in PATH)
  log.py            # logging + subprocess helpers
  builders/
    base.py         # shared clone/build/install lifecycle
    llvm.py         # LLVM/Clang trunk (one build, many targets)
    gcc.py          # GCC trunk cross compilers (one per target)
    qemu.py         # latest-stable QEMU (auto version resolution)
```

Sources are cloned into `src/`, built in `build/`, and installed into
`toolchains/<component>/`. The first three are git-ignored.

## Notes & caveats

- **GCC builds one cross compiler per architecture**, which is slow — set
  `enabled = false` under `[gcc]` to skip it. The **native** target gets a full
  build with `libsanitizer`; **cross** targets default to a compiler-only build
  (`--without-headers`, good for `-S` / compile-only testing). Point
  `[gcc.sysroots]` and `[gcc.binutils]` to matching per-target paths to get a
  full libc-linked build with sanitizers for that cross target too.  Existing
  compiler-only installations must be rebuilt after adding them.
- **Disk and time.** Trunk LLVM and GCC are large; expect tens of GB and a long
  first build. Subsequent `update`s reuse the source and build trees.
- **Reproducibility.** Trunk moves fast by design. `cte status` reports the
  exact `git describe` of each installed source.
