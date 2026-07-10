"""cte -- compiler-test-env.

A helper for setting up testing environments for the latest C compilers
(LLVM/Clang trunk, GCC trunk) across multiple architectures, plus a recent
stable QEMU for running the results. Toolchains are installed into a local
prefix and are never added to PATH automatically.
"""

__version__ = "0.1.0"
