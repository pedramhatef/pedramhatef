"""The fast decoder must agree bit-for-bit with the validator's reference codec (re-implemented here, verbatim logic)."""
import math
import random

import numpy as np
import pytest

from cliqueminer import codec

ALPH = codec.ALPHABET


def ref_min_digits(r_bits):
    if r_bits <= 0:
        return 0
    m = math.ceil(r_bits / math.log2(92))
    while (92 ** (m - 1) if m > 0 else 0) >= (1 << r_bits):
        m -= 1
    while 92 ** m < (1 << r_bits):
        m += 1
    return m


def ref_enc_fixed(val, digits):
    out = []
    for _ in range(digits):
        val, rem = divmod(val, 92)
        out.append(ALPH[rem])
    assert val == 0
    return "".join(reversed(out))


def ref_encode(adj):
    """Port of GraphCodec.encode_matrix."""
    n = len(adj)
    parts = [ref_enc_fixed(n, 5)]
    chunk_val = chunk_len = 0
    for i in range(n):
        for j in range(i + 1, n):
            chunk_val = (chunk_val << 1) | (1 if adj[i][j] else 0)
            chunk_len += 1
            if chunk_len == 1246:
                parts.append(ref_enc_fixed(chunk_val, 191))
                chunk_val = chunk_len = 0
    if chunk_len:
        parts.append(ref_enc_fixed(chunk_val, ref_min_digits(chunk_len)))
    return "".join(parts)


def random_graph(n, p, seed):
    rng = random.Random(seed)
    adj = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < p:
                adj[i][j] = adj[j][i] = 1
    return adj


@pytest.mark.parametrize("n,p", [(1, 0.5), (2, 1.0), (3, 1.0), (7, 0.3), (50, 0.5), (51, 0.9), (90, 0.8), (300, 0.75), (497, 0.95)])
def test_decode_matches_reference_encoding(n, p):
    adj = random_graph(n, p, seed=n * 1000 + int(p * 100))
    got = codec.decode_matrix(ref_encode(adj))
    assert got.shape == (n, n)
    assert np.array_equal(got, np.array(adj, dtype=bool))


def test_chunk_boundaries():
    # sizes whose bit counts land exactly on / just around the 1246-bit chunk size
    for n in range(48, 56):
        adj = random_graph(n, 0.6, seed=n)
        assert np.array_equal(codec.decode_matrix(ref_encode(adj)), np.array(adj, dtype=bool))


@pytest.mark.parametrize("bad", ["", "abc", "    $", "    $)x", "\"    $)", "    $\\"])
def test_malformed_input_raises_value_error(bad):
    with pytest.raises(ValueError):
        codec.decode_matrix(bad)


def test_maximality_matches_validator_rule():
    adj = np.array(random_graph(40, 0.7, seed=5), dtype=bool)
    rng = np.random.default_rng(0)
    c = codec.extend_to_maximal(adj, [], rng)
    assert codec.is_clique(adj, c) and codec.is_maximal_clique(adj, c)
    assert not codec.is_maximal_clique(adj, c[:-1])            # a strict subset is extendable
    assert not codec.is_clique(adj, c + [c[0]])                # duplicate vertex
    assert not codec.is_clique(adj, c[:-1] + [40])             # out of range
    assert not codec.is_clique(adj, [])                        # empty answer scores zero
    assert not codec.is_clique(adj, [True, 1])                 # bools are not vertex ids
