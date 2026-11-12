"""The analysis is only as good as its re-implementation of the validator's scoring: check it against hand-computed
cases and against rewards the real validator logged."""
import math
import os

import numpy as np
import pytest

from analysis import dataset, market

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "rounds.jsonl.gz")


def test_score_hand_computed():
    # 4 miners: two share the max clique, one unique max clique, one unique smaller clique. d = 0.7
    a, b, c = (1, 2, 3), (4, 5, 6), (7, 8)
    om, dl, rw = dataset.score([a, a, b, c], [True] * 4, 0.7)
    assert om[:3].tolist() == [1.0, 1.0, 1.0]
    assert om[3] == pytest.approx(math.exp(-(3 / 4) / (2 / 3)))          # pr = 3/4 strictly larger, rel = 2/3
    assert dl.tolist() == [0.5, 0.5, 1.0, 1.0]
    assert rw[2] == pytest.approx(2.7) and rw[0] == pytest.approx(1.7 + 0.5)


def test_invalid_answers_score_zero_and_do_not_set_the_max():
    om, dl, rw = dataset.score([(1, 2, 3, 4), (5, 6)], [False, True], 1.0)
    assert rw[0] == 0 and om[1] == 1.0 and dl[1] == 1.0 and rw[1] == pytest.approx(3.0)


def test_delta_normalised_by_best_delta_in_round():
    # nobody unique: counts 2 and 3 -> deltas 1/2 and 1/3 -> normalised 1 and 2/3
    a, b = (1, 2), (3, 4)
    _, dl, _ = dataset.score([a, a, b, b, b], [True] * 5, 0.8)
    assert dl.tolist() == pytest.approx([1, 1, 2 / 3, 2 / 3, 2 / 3])


def test_empty_and_all_invalid_pools():
    assert dataset.score([], [], 0.7)[2].tolist() == []
    assert dataset.score([(1,), (2,)], [False, False], 0.7)[2].tolist() == [0, 0]


def test_reproduces_logged_rewards_on_real_rounds():
    problems = dataset.load(FIX, cache=False, workers=1)
    assert len(problems) == 3
    bad, checked = dataset.check_scoring(problems)
    assert checked == 3 and bad == 0
    for p in problems:
        assert len(p.answers) == len(p.uids) == len(p.hotkeys) == len(p.coldkeys)
        assert p.adjacency().shape == (p.n, p.n)


def test_newcomer_joining_a_crowded_clique_is_the_odd_one_out():
    problems = dataset.load(FIX, cache=False, workers=1)
    crowded = next(p for p in problems if p.n <= 300 and p.frac_at_best > 0.95)
    best_answers = [a for a, o in zip(crowded.answers, crowded.ok) if o and len(a) == crowded.best]
    rewards = [dataset.reward_if_added(crowded, a)[0] for a in set(best_answers)]
    # every maximum clique is already occupied, so no choice reaches the full score 2 + d
    assert max(rewards) < 2 + crowded.d - 1e-9
    # and a smaller unique clique is far worse when everyone else is at the best size
    assert dataset.reward_if_added(crowded, best_answers[0][:-1], answer_ok=True)[0] < min(rewards)


def test_market_metrics_on_real_rounds():
    problems = dataset.load(FIX, cache=False, workers=1)
    tags = market.operator_tags(problems, min_miners=3)
    assert len(tags) >= 3
    summ = market.operator_summary(problems, tags)
    assert all(0 <= v["valid"] <= 1 and 0 <= v["reward"] <= 3 for v in summ.values())
    kinds = market.bimodality(problems)
    assert kinds["all_at_best(>=90%)"] == pytest.approx(2 / 3) and kinds["minority_at_best(<25%)"] == pytest.approx(1 / 3)
