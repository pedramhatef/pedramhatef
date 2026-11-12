"""Fast decoder for the validator's base92 adjacency encoding (CliqueAI/graph/codec.py), plus graph checks.

The reference decoder walks the matrix bit by bit in Python (~0.3 s for n=900). This one converts each chunk to an
integer and lets numpy unpack the bits (~10 ms). The native solver has its own decoder; this module is used for the
final safety check on the answer and by the pure-Python fallback solver.
"""
from __future__ import annotations

import numpy as np

ALPHABET = "".join(chr(c) for c in range(32, 127) if c not in (34, 39, 92))
_INDEX = {ch: i for i, ch in enumerate(ALPHABET)}
HDR_DIGITS, CHUNK_BITS, CHUNK_DIGITS = 5, 1246, 191


def _min_digits_for_bits(r_bits: int) -> int:
    if r_bits <= 0:
        return 0
    m = 1
    while 92 ** m < (1 << r_bits):
        m += 1
    return m


def _to_int(s: str) -> int:
    v = 0
    for ch in s:
        v = v * 92 + _INDEX[ch]          # KeyError on an invalid character
    return v


def decode_matrix(encoded: str) -> np.ndarray:
    """base92 string -> symmetric boolean adjacency matrix with a zero diagonal. Raises ValueError on malformed input."""
    try:
        if len(encoded) < HDR_DIGITS:
            raise ValueError("truncated header")
        n = _to_int(encoded[:HDR_DIGITS])
        if not 0 < n <= 20000:
            raise ValueError(f"unreasonable node count {n}")
        total = n * (n - 1) // 2
        full, rem = divmod(total, CHUNK_BITS)
        tail = _min_digits_for_bits(rem)
        if len(encoded) != HDR_DIGITS + full * CHUNK_DIGITS + tail:
            raise ValueError("length mismatch")
        pos, big = HDR_DIGITS, 0
        for _ in range(full):
            big = (big << CHUNK_BITS) | _to_int(encoded[pos:pos + CHUNK_DIGITS])
            pos += CHUNK_DIGITS
        if rem:
            big = (big << rem) | _to_int(encoded[pos:pos + tail])
    except KeyError as e:                 # invalid base92 character
        raise ValueError(f"invalid base92 character {e}") from None
    adj = np.zeros((n, n), dtype=bool)
    if total:
        nbytes = (total + 7) // 8
        raw = (big << (nbytes * 8 - total)).to_bytes(nbytes, "big")
        adj[np.triu_indices(n, 1)] = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))[:total].astype(bool)
        adj |= adj.T
    return adj


def is_clique(adj: np.ndarray, nodes) -> bool:
    nodes = list(nodes)
    n = adj.shape[0]
    if not nodes or len(set(nodes)) != len(nodes):
        return False
    if any((not isinstance(v, (int, np.integer))) or isinstance(v, bool) or v < 0 or v >= n for v in nodes):
        return False
    k = len(nodes)
    return int(adj[np.ix_(nodes, nodes)].sum()) == k * (k - 1)


def is_maximal_clique(adj: np.ndarray, nodes) -> bool:
    """True iff `nodes` is a clique that no other vertex can extend — exactly what the validator requires
    (CliqueScoreCalculator.is_valid_maximum_clique)."""
    if not is_clique(adj, nodes):
        return False
    return not bool(adj[list(nodes)].all(axis=0).any())


def extend_to_maximal(adj: np.ndarray, nodes, rng=None) -> list[int]:
    """Greedily extend a clique until no vertex can be added."""
    nodes = list(nodes)
    cand = adj[nodes].all(axis=0) if nodes else np.ones(adj.shape[0], dtype=bool)
    while cand.any():
        choices = np.flatnonzero(cand)
        v = int(choices[rng.integers(len(choices))]) if rng is not None else int(choices[0])
        nodes.append(v)
        cand &= adj[v]
    return sorted(int(v) for v in nodes)


def greedy_clique(adj: np.ndarray, rng, restarts: int = 64) -> list[int]:
    """Pure-Python fallback: best of several randomized greedy maximal cliques. Always returns a valid maximal clique."""
    best: list[int] = []
    for _ in range(max(1, restarts)):
        c = extend_to_maximal(adj, [], rng)
        if len(c) > len(best):
            best = c
    return best
