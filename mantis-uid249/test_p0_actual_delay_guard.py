import numpy as np
import p0_funding_provenance as p0

def test_post_settlement_rejects_hour_late_archive_samples():
    times = np.arange(40, dtype=float) * 28_800 + 1_700_000_000
    rates = np.sin(np.arange(40) * .33) * .0001 + .00011
    # Every sample is 60 minutes AFTER funding settlement: beyond max delay (30 minutes).
    archive_t = times + 3600
    result = p0.match(rates, archive_t, list(zip(times.tolist(), rates.tolist())))
    assert not p0.passes(result["post_settlement"])
    assert result["post_settlement"]["n"] == 0


def test_post_settlement_accepts_near_settlement_archive_samples():
    times = np.arange(40, dtype=float) * 28_800 + 1_700_000_000
    rates = np.sin(np.arange(40) * .33) * .0001 + .00011
    archive_t = times + 120  # 2-minute publication delay
    result = p0.match(rates, archive_t, list(zip(times.tolist(), rates.tolist())))
    assert p0.passes(result["post_settlement"])
