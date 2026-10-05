"""The live eval script's summary maths, loop and refusals, with no model and no database.

The script itself is manual (it spends money); these pin the arithmetic its report rests on
(the headline number) and the paths that must refuse or stop cleanly.
"""
import asyncio

import pytest

from scripts import live_chat_eval as ev
from scripts.live_chat_eval import Call
from tests.routing_questions import DOCUMENT_LOOKUP, OUT_OF_SCOPE, Question

LOOK = (DOCUMENT_LOOKUP, "basil")
OOS = (OUT_OF_SCOPE, None)
Q1 = Question("q1", DOCUMENT_LOOKUP, "basil", "")
Q2 = Question("q2", OUT_OF_SCOPE, None, "")
Q3 = Question("q3", DOCUMENT_LOOKUP, "lettuce", "")
LET = (DOCUMENT_LOOKUP, "lettuce")

ROWS = [
    (Q1, [Call(LOOK, False, 100, 900, "a"), Call(LOOK, False, 100, 1100, "b")]),         # 1000, 1200
    (Q2, [Call(OOS, False, 100, None, "c"), Call((DOCUMENT_LOOKUP, None), False, 100, 800, "d")]),  # 100, 900: flips
    (Q3, [Call(LET, False, 100, 1000, "e"), Call(LET, True, 100, None, "f")]),            # 1100, 100 (refused)
]


def test_routing_rates():
    r = ev.routing_stats(ROWS)
    assert r == {"calls": 6, "correct": 5, "refused": 1, "questions": 3, "flips": 1}


def test_token_summary_and_questions_per_day():
    t = ev.token_stats(ROWS, 20000)
    assert t["total"] == 4400 and t["mean"] == pytest.approx(4400 / 6)
    assert t["median_all"] == 600 and t["per_day_all"] == pytest.approx(20000 / 600)  # medians 1100, 500, 600
    assert t["median_lookup"] == 850 and t["per_day_lookup"] == pytest.approx(20000 / 850)  # q2 is excluded
    assert t["worst_call"] == 1200 and t["per_day_worst"] == pytest.approx(20000 / 1200)
    assert ev.summarise(t["classifier"]) == "min 100 / median 100 / max 100"
    assert ev.summarise(t["generator"]) == "min 800 / median 950 / max 1100"  # only calls that generated


def test_a_zero_token_run_does_not_divide_by_zero():
    rows = [(Q1, [Call(LOOK, False, 0, 0, "")])]
    t = ev.token_stats(rows, 20000)
    assert t["per_day_all"] is None and t["per_day_lookup"] is None and t["per_day_worst"] is None
    lines = []
    ev.report(rows, None, 1, 20000, False, lines.append)
    assert "n/a (no tokens reported)" in "\n".join(lines)


def test_the_report_names_the_figure_to_decide_the_budget_on_and_counts_refusals_apart():
    lines = []
    ev.report(ROWS, None, 2, 20000, False, lines.append)
    text = "\n".join(lines)
    assert "routing accuracy: 5/6 = 83.3%" in text and "flip rate: 1/3 questions = 33.3%" in text
    assert "refused for the context budget: 1 repeats (not counted as misroutes)" in text
    assert "USE THIS FOR THE BUDGET DECISION" in text and "23.5 questions per member per day" in text


def test_a_failing_call_stops_the_run_but_the_report_keeps_what_completed():
    asked = []

    async def ask(q):
        asked.append(q.text)
        if q is Q2:
            raise RuntimeError("quota")
        return Call(LOOK, False, 10, 20, "x")

    rows, stop = asyncio.run(ev.run_questions([Q1, Q2, Q3], 2, ask))
    assert asked == ["q1", "q1", "q2"]  # stopped at the first failure
    assert [q.text for q, _ in rows] == ["q1"] and len(rows[0][1]) == 2
    assert "RuntimeError: quota" in stop and "'q2'" in stop
    lines = []
    ev.report(rows, stop, 2, 20000, False, lines.append)
    assert "STOPPED EARLY" in "\n".join(lines) and "routing accuracy: 2/2" in "\n".join(lines)


# --- refusals --------------------------------------------------------------------

def test_the_database_target_is_required():
    with pytest.raises(SystemExit) as exc:
        ev.parse_args([])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        ev.parse_args(["--db-host", "db"])


def test_repeats_must_be_positive_and_out_is_never_overwritten(tmp_path):
    base = ["--db-host", "db-test", "--db-name", "cms_test"]
    with pytest.raises(SystemExit):
        ev.parse_args(base + ["--repeats", "0"])
    existing = tmp_path / "report.txt"
    existing.write_text("keep")
    with pytest.raises(SystemExit):
        ev.parse_args(base + ["--out", str(existing)])
    assert existing.read_text() == "keep"


def test_main_refuses_without_both_keys_and_creates_no_out_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    out = tmp_path / "r.txt"
    with pytest.raises(SystemExit) as exc:
        ev.main(["--db-host", "db-test", "--db-name", "cms_test", "--out", str(out)])
    assert "ANTHROPIC_API_KEY and OPENAI_API_KEY not set" in str(exc.value)
    assert not out.exists()
