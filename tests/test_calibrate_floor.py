"""scripts/calibrate_floor.py: the gap it prints, and the guard against a silently truncated run.

score_topics keeps only the best k topics, dropping the lowest scores first, so a corpus larger
than the cap could make `min(on_topic)` look higher than it is. The script asks for one topic
more than the cap: getting it back proves topics were dropped, and it must stop instead of
printing a gap it cannot trust. A corpus of exactly the cap is complete and must not stop.
"""
import pytest

import scripts.calibrate_floor as cf
from tests.test_topic_expansion import FixedEmbedder, _crop, _doc, mix, session  # noqa: F401


@pytest.fixture
def five_topics(session, monkeypatch):
    crop = _crop("basil")
    for name, score in (("optimal-temperature", 0.9), ("t1", 0.7), ("t2", 0.5), ("t3", 0.3), ("t4", 0.1)):
        _doc(crop, name, 0, [mix(score)])
    monkeypatch.setattr(cf, "get_embedder", lambda: FixedEmbedder())
    monkeypatch.setattr(cf, "QUESTIONS", [("q", "optimal-temperature", "basil")])


def test_prints_the_gap_between_on_topic_and_off_topic_scores(five_topics, capsys):
    cf.main()
    out = capsys.readouterr().out
    assert "gap (lowest on-topic - highest off-topic): 0.2000" in out
    assert "a floor between them separates the two" in out


def test_stops_when_topics_exceed_the_cap_instead_of_printing_a_truncated_gap(five_topics, monkeypatch, capsys):
    monkeypatch.setattr(cf, "TOPIC_CAP", 3)          # 5 topics, so the lowest two would be dropped
    with pytest.raises(SystemExit) as stopped:
        cf.main()
    assert "TOPIC_CAP" in str(stopped.value)
    assert "gap (lowest" not in capsys.readouterr().out


def test_exactly_the_cap_is_a_complete_run_and_one_over_is_not(five_topics, monkeypatch, capsys):
    monkeypatch.setattr(cf, "TOPIC_CAP", 5)         # 5 topics: nothing is dropped
    cf.main()
    assert "gap (lowest" in capsys.readouterr().out
    monkeypatch.setattr(cf, "TOPIC_CAP", 4)         # 5 topics: one is dropped
    with pytest.raises(SystemExit):
        cf.main()


def test_another_crops_same_named_topic_is_not_counted_as_on_topic(session, monkeypatch, capsys):
    basil, crop_b = _crop("basil"), _crop("crop-b")
    _doc(basil, "optimal-temperature", 0, [mix(0.9)])
    _doc(basil, "watering-needs", 0, [mix(0.4)])
    _doc(crop_b, "optimal-temperature", 0, [mix(0.1)])    # same topic name, other crop
    _doc(crop_b, "watering-needs", 0, [mix(0.05)])
    monkeypatch.setattr(cf, "get_embedder", lambda: FixedEmbedder())
    monkeypatch.setattr(cf, "QUESTIONS", [("q", "optimal-temperature", "basil")])
    cf.main()
    out = capsys.readouterr().out
    assert "on-topic : n=1 min=0.9000" in out                 # not 2, and not min 0.1000
    assert "gap (lowest on-topic - highest off-topic): 0.5000" in out


def test_an_unknown_crop_in_the_questions_stops_the_run(session, monkeypatch):
    monkeypatch.setattr(cf, "get_embedder", lambda: FixedEmbedder())
    monkeypatch.setattr(cf, "QUESTIONS", [("q", "optimal-temperature", "no-such-crop")])
    with pytest.raises(SystemExit) as stopped:
        cf.main()
    assert "no-such-crop" in str(stopped.value)

