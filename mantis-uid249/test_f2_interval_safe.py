"""Isolated deterministic regression tests; no live data, prices or network."""
from dataclasses import replace
import numpy as np
import pytest
from funding_premium_projection_interval_safe import Venue, _rate, ar1_fit, projected_feed_rate, feature_scores


def terms(interval=480, **kwargs):
    d=dict(interval_min=interval, interest=.0001, clamp_c=.0005,
           floor=-.02, cap=.02, weighting='linear', semantics='estimated',
           formula='binance_post_2025_09_18', settlement_offset_min=0)
    d.update(kwargs)
    return Venue(**d)


def test_interval_formula_scaling_and_clamp():
    for n,expected in ((480,.0015),(240,.00075),(60,.0001875)):
        assert abs(_rate(.002,terms(n))-expected)<1e-14
        assert abs(_rate(.0001,terms(n))-.0001*n/480)<1e-14


def test_bound_and_interest_exception():
    assert _rate(.02,terms(cap=.001))==.001
    assert _rate(-.02,terms(floor=-.001))==-.001
    assert abs(_rate(0.0,terms(interest=0.0)))<1e-14
    assert _rate(.002,terms(interest=0.0))==.0015


def test_missing_provenance_rejected():
    with pytest.raises(ValueError): _rate(.002,terms(formula='UNVERIFIED'))
    with pytest.raises(ValueError): _rate(.002,terms(interval_min=123))
    with pytest.raises(ValueError): _rate(float('nan'),terms())
    with pytest.raises(ValueError): _rate(.002,terms(settlement_offset_min=480))


def test_complete_settled_premium_window_and_offset():
    p=np.linspace(-.002,.004,3000)
    t=2000
    v=terms(semantics='settled',settlement_offset_min=30)
    # For target t=2000 and offset=30, last settled boundary is 1950.
    ws,we=1470,1950
    w=np.arange(1,we-ws+1,dtype=float)
    exp=_rate(float(w@p[ws:we]/w.sum()),v)
    assert abs(projected_feed_rate(t,t,p,0.0,.5,v)-exp)<1e-13


def test_no_future_premium_lookahead():
    p=np.linspace(-.002,.004,6000)
    t=4000;v=terms()
    left=projected_feed_rate(t,t+480,p[:t+1],0.0,.9,v)
    altered=p.copy();altered[t+1:]=1000.0
    right=projected_feed_rate(t,t+480,altered,0.0,.9,v)
    assert left==right


def test_gap_not_compressed_into_adjacent_ar1_and_observation_missing():
    p=np.linspace(-.01,.01,150)
    p[70]=np.nan
    mu,phi=ar1_fit(np.r_[np.zeros(300),np.nan,np.ones(120)*.002])
    assert abs(mu-.002)<1e-15 and phi==0
    with pytest.raises(ValueError): ar1_fit(p)  # only 79 valid minutes after gap
    with pytest.raises(ValueError): projected_feed_rate(310,310,np.r_[np.ones(300),np.nan,np.ones(15)],0,.9,terms())


def test_true_neutral_zero_stays_missing_not_artificially_nudged():
    p=np.zeros((4,3000),dtype=float)
    z=feature_scores(2500,p,[terms()]*4)
    assert np.array_equal(z,np.zeros(4))


def test_causality_with_multiple_assets():
    rng=np.random.default_rng(8)
    p=rng.normal(0,.00015,(8,5000))
    t=2400
    a=feature_scores(t,p,[terms()]*8)
    p2=p.copy();p2[:,t+1:]+=rng.normal(0,1,(8,p.shape[1]-t-1))
    b=feature_scores(t,p2,[terms()]*8)
    assert np.array_equal(a,b)


def test_no_live_claim():
    # This test establishes arithmetic, not suitability for UID249 scoring.
    assert terms().formula=='binance_post_2025_09_18'
