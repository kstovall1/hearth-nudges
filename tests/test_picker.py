"""Tests for the nudge picker rules. Run with: uv run pytest tests/test_picker.py
The simulation test takes about 30 seconds."""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "hearth_ml"))
import picker as P  # noqa: E402


def effects_for(nudge_uplifts, opt_out_ratio=1.0, se=0.005):
    """A test readout with one pooled cell per nudge."""
    return pd.DataFrame([{
        "nudge_type": n, "dimension": "all", "segment": "all", "n_treated": 10_000, "n_holdout": 2_000,
        "return_treated": 0.4 + u, "return_holdout": 0.4, "uplift": u, "se": se, "uplift_low": u - 1.645 * se,
        "opt_out_treated": 0.01, "opt_out_ratio": opt_out_ratio,
    } for n, u in nudge_uplifts.items()])


def member(**kw):
    row = {"member_id": "m_000001", "tenure_days": 200, "age_band": "25-29", "market_size": "large",
           "days_away": 4.5, "hours_since_last_nudge": np.nan, "risk_band": "high", "lapse_risk": 0.4,
           "pushes_7d": 0, "eligible_new_likes": False, "eligible_match_waiting": False, "preferred_hour_local": 20}
    row.update(kw)
    return row


CHOSEN_ALL = {n: "all" for n in P.NUDGES}
FATIGUE = {"0-1": 0.006, "2-3": 0.011, "4+": 0.018}


def test_hold_reasons_in_order():
    e = effects_for({"new_likes": 0.05, "match_waiting": 0.07, "profile_tip": 0.0, "new_people_nearby": 0.0,
                     "we_miss_you": 0.0})
    members = pd.DataFrame([
        member(member_id="active", days_away=1.0, hours_since_last_nudge=10),  # active wins over cooldown
        member(member_id="cooldown", hours_since_last_nudge=10, risk_band="low"),  # cooldown wins over low risk
        member(member_id="low_risk", risk_band="low", eligible_match_waiting=True),
        member(member_id="nothing_eligible"),  # only zero-uplift nudges are open to them
        member(member_id="send", eligible_match_waiting=True, eligible_new_likes=True),
    ])
    d = P.decide(members, e, CHOSEN_ALL, FATIGUE).set_index("member_id")
    assert d.loc["active", "hold_reason"] == "active"
    assert d.loc["cooldown", "hold_reason"] == "cooldown"
    assert d.loc["low_risk", "hold_reason"] == "likely_back"
    assert d.loc["nothing_eligible", "hold_reason"] == "not_worth_it"
    assert d.loc["send", "action"] == "send"
    assert d.loc["send", "nudge_type"] == "match_waiting"  # the higher net value of the two it qualifies for
    assert d.loc["send", "send_hour_local"] == 20


def test_unscored_members_are_not_held_as_low_risk():
    e = effects_for({n: 0.05 for n in P.NUDGES})
    d = P.decide(pd.DataFrame([member(days_away=40, risk_band=np.nan, lapse_risk=np.nan)]), e, CHOSEN_ALL, FATIGUE)
    assert d["action"].iloc[0] == "send"


def test_opt_out_risk_can_outweigh_uplift():
    # 2 points of uplift: worth it at low fatigue (2 - 2 x 0.6 > 0), not at 4+ pushes (2 - 2 x 1.8 < 0)
    e = effects_for({"we_miss_you": 0.02, "new_likes": 0.0, "match_waiting": 0.0, "profile_tip": 0.0,
                     "new_people_nearby": 0.0})
    members = pd.DataFrame([member(member_id="fresh", pushes_7d=0), member(member_id="tired", pushes_7d=5)])
    d = P.decide(members, e, CHOSEN_ALL, FATIGUE).set_index("member_id")
    assert d.loc["fresh", "nudge_type"] == "we_miss_you"
    assert d.loc["tired", "hold_reason"] == "not_worth_it"


def test_uncertain_uplift_is_not_sent():
    e = effects_for({n: 0.01 for n in P.NUDGES}, se=0.02)  # lower bound below zero
    d = P.decide(pd.DataFrame([member()]), e, CHOSEN_ALL, FATIGUE)
    assert d["hold_reason"].iloc[0] == "not_worth_it"


def test_preferred_hours_stay_in_the_send_window():
    counts = pd.DataFrame({
        "member_id": ["late"] * 3 + ["early"] * 2 + ["few"],
        "hour": [22, 23, 23, 6, 7, 20],
        "sessions": [2, 5, 4, 3, 4, 1],
    })
    hours, default = P.preferred_hours(counts)
    h = hours.set_index("member_id")
    assert h.loc["late", "preferred_hour_local"] == 22  # 23:00 is a night send
    assert h.loc["early", "preferred_hour_local"] == 8
    assert h.loc["few", "hour_source"] == "default"
    assert 8 <= default <= 22


def test_send_times_follow_daylight_saving():
    before, after = P.send_times(date(2026, 10, 30), [20], ["America/New_York"]), \
        P.send_times(date(2026, 11, 2), [20], ["America/New_York"])
    assert before[0].hour == 0 and after[0].hour == 1  # 20:00 EDT is 00:00 UTC, 20:00 EST is 01:00 UTC


@pytest.mark.slow
def test_readout_finds_the_planted_segments():
    from hearth_data.check import returned_within_72h
    from hearth_data.simulate import DAY, Simulation

    sim = Simulation(7, date(2026, 8, 28))
    f = sim.run(60)
    members = sim.members()
    d = f["decisions"]
    o = returned_within_72h(d[d["campaign_type"] == "nudge_test"], f["sessions"], members).merge(members, on="member_idx")
    opted = f["notifications"].query("event_type in ['push_disabled', 'uninstalled']")["decision_idx"].unique()
    o = o[o["decided_s"] + 72 * 3600 <= sim.t0 + 60 * DAY].assign(
        treated=lambda x: x["arm"] == "treatment", returned_72h=lambda x: x["returned"],
        opted_out=lambda x: x["decision_idx"].isin(opted), tenure_days=lambda x: (x["decided_s"] - x["signup_s"]) / DAY,
        days_away_bucket=lambda x: P.days_away_bucket(x["days_away"]))
    chosen, _ = P.pick_dimensions(P.segment_effects(o))
    assert chosen["new_likes"] == "age_band"
    assert chosen["profile_tip"] == "tenure_group"
    assert chosen["new_people_nearby"] == "market_size"
    assert chosen["we_miss_you"] == "days_away_bucket"
