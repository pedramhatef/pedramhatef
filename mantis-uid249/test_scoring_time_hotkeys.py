"""Offline tests for scoring_time_hotkeys.py with a fake chain client (no network)."""
import json

import pytest

import scoring_time_hotkeys as s


class Fake:
    def __init__(self, by_block):
        self.by_block, self.calls = by_block, []

    def query_map(self, item, params, block=None):
        self.calls.append((item, tuple(params), block))
        return iter(self.by_block.get(block, []))


def test_scoring_block_and_key_formats(tmp_path):
    f1 = 5 * 1874000 + 50400 + 500
    full = 5 * 1882000 + 50400 + 500
    fake = Fake({f1: [((123, 0), "hkA"), ((123, 1), "hkB")], full: [(0, "hkA"), (2, "hkC")]})
    r = s.main(["--fold", "F1:1874000", "--fold", "FULL:1882000", "--maturity", "50400",
                "--out", str(tmp_path / "a.json")], client=fake, item="KEYS")
    assert [c[2] for c in fake.calls] == [f1, full] and all(c[1] == (123,) for c in fake.calls)
    assert r["active"] == {"F1": ["hkA", "hkB"], "FULL": ["hkA", "hkC"]}
    assert json.load(open(tmp_path / "a.json"))["folds"]["F1"]["scoring_block"] == f1


def test_empty_answer_stops(tmp_path):
    with pytest.raises(RuntimeError, match="no hotkeys"):
        s.main(["--fold", "F2:1878000", "--maturity", "50400", "--out", str(tmp_path / "a.json")], client=Fake({}), item="K")
