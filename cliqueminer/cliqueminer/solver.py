"""ctypes binding for the native solver (solver/libcliquesolver.so)."""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass, field

import numpy as np

_LIB = None
_LIB_ERR: str | None = None


def _load():
    global _LIB, _LIB_ERR
    if _LIB is not None or _LIB_ERR is not None:
        return _LIB
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.environ.get("CLIQUE_SOLVER_LIB", ""), os.path.join(here, "..", "solver", "libcliquesolver.so"),
                  os.path.join(here, "libcliquesolver.so")]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                lib = ctypes.CDLL(os.path.abspath(path))
                lib.clq_solve.restype = ctypes.c_int
                lib.clq_solve.argtypes = [ctypes.c_char_p, ctypes.c_double, ctypes.c_int, ctypes.c_uint64,
                                          ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                          ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,
                                          ctypes.POINTER(ctypes.c_double)]
                _LIB = lib
                return _LIB
            except OSError as e:          # wrong architecture, missing libstdc++ ...
                _LIB_ERR = f"{path}: {e}"
    _LIB_ERR = _LIB_ERR or "libcliquesolver.so not found (run `make -C solver`)"
    return None


def native_available() -> bool:
    return _load() is not None


def native_error() -> str | None:
    _load()
    return _LIB_ERR


@dataclass
class SolveResult:
    best: int                                   # size of the largest clique found
    max_cliques: list[tuple[int, ...]]          # distinct maximum cliques (sorted vertex tuples)
    max_hits: list[int]                         # independent search episodes that reached each one (0 = closure only)
    sub_cliques: list[tuple[int, ...]] = field(default_factory=list)   # distinct MAXIMAL cliques of size best-1
    time_to_best_ms: float = 0.0
    native: bool = True


def solve_native(encoded: str, seconds: float, threads: int, seed: int, max_cliques: int = 6000, max_sub: int = 1000) -> SolveResult:
    """Run the native solver for `seconds` of wall-clock time on `threads` threads. Releases the GIL while running.
    Returns at most `max_cliques` maximum cliques and `max_sub` best-1 cliques (in order of discovery).
    Raises RuntimeError if the library is missing or the solver reports an error."""
    lib = _load()
    if lib is None:
        raise RuntimeError(f"native solver unavailable: {_LIB_ERR}")
    if not isinstance(encoded, str) or len(encoded) < 5 or "\x00" in encoded or not encoded.isascii():
        raise RuntimeError("encoded matrix is not a valid base92 string")
    cap_cliques = max_cliques + max_sub
    flat_cap = cap_cliques * 160
    flat = np.zeros(flat_cap, dtype=np.int32)
    counts = np.zeros(cap_cliques, dtype=np.uint32)
    best, n_max, n_sub, t2b = ctypes.c_int(0), ctypes.c_int(0), ctypes.c_int(0), ctypes.c_double(0.0)
    rc = lib.clq_solve(encoded.encode("ascii"), float(seconds), int(threads), int(seed) & 0xFFFFFFFFFFFFFFFF,
                       ctypes.byref(best), ctypes.byref(n_max), ctypes.byref(n_sub),
                       flat.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), flat_cap,
                       counts.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32)), cap_cliques, ctypes.byref(t2b))
    if rc != 0:
        raise RuntimeError(f"native solver failed with code {rc}")
    b, nm, ns = best.value, n_max.value, n_sub.value
    mx = [tuple(row) for row in flat[:nm * b].reshape(nm, b).tolist()]
    sub = [tuple(row) for row in flat[nm * b:nm * b + ns * (b - 1)].reshape(ns, b - 1).tolist()] if b > 1 and ns else []
    if len(sub) > max_sub:
        sub = sub[:max_sub]
    return SolveResult(best=b, max_cliques=mx, max_hits=counts[:nm].tolist(), sub_cliques=sub, time_to_best_ms=t2b.value)
