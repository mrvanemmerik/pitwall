import json
from datetime import datetime, timezone
from types import SimpleNamespace

from f1board import ingest_handler, recap
from f1board.season_view import season_view


def items():
    return {
        "drivers": {"rows": [
            {"position": 1, "name": "Andrea Kimi Antonelli", "team": "Mercedes", "points": 320, "wins": 8},
            {"position": 2, "name": "George Russell", "team": "Mercedes", "points": 236, "wins": 3},
            {"position": 3, "name": "Max Verstappen", "team": "Red Bull", "points": 188, "wins": 1},
            {"position": 4, "name": "Lando Norris", "team": "McLaren", "points": 188, "wins": 2},
        ]},
        "teams": {"rows": [{"position": 1, "name": "Mercedes", "points": 556, "wins": 11}, {"position": 2, "name": "Ferrari", "points": 405, "wins": 2}]},
        "schedule": {"rows": [{"round": 16, "name": "Bahrain Grand Prix", "circuit": "Sepang", "date": "2026-10-04", "hasSprint": False}]},
        "results": {"rows": [
            {"round": 15, "name": "Azerbaijan Grand Prix", "hasSprint": False, "grandPrix": [], "sprint": []},
            {"round": 16, "name": "Bahrain Grand Prix", "hasSprint": True,
             "grandPrix": [
                 {"position": 1, "driver": "Max Verstappen", "team": "Red Bull", "points": 25, "time": "1:47:14.808"},
                 {"position": 2, "driver": "Andrea Kimi Antonelli", "team": "Mercedes", "points": 18, "time": "+2.307"},
             ],
             "sprint": [{"position": 1, "driver": "Lando Norris", "team": "McLaren", "points": 8, "time": "+0.5"}]},
        ]},
        "meta": {"updatedAt": "2026-10-10T16:48:37Z"},
    }


class FakeClaude:
    def __init__(self, text="Verstappen won in Bahrain.", stop="end_turn"):
        self.calls = []
        self.messages = self
        self.text = text
        self.stop = stop

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], stop_reason=self.stop)


def test_facts_cover_the_newest_round_only():
    data = items()
    facts = recap.build_facts(data, data["results"]["rows"][1])
    assert facts["round"] == 16 and facts["circuit"] == "Sepang"
    assert facts["grandPrix"][0]["driver"] == "Max Verstappen"
    assert facts["sprint"][0]["driver"] == "Lando Norris"
    assert [row["driver"] for row in facts["standingsAfter"]][:2] == ["Andrea Kimi Antonelli", "George Russell"]
    norris = next(row for row in facts["standingsAfter"] if row["driver"] == "Lando Norris")
    assert norris["pointsThisRound"] == 8
    assert facts["teamsTop3"][0]["team"] == "Mercedes"


def test_places_moved_counts_this_rounds_points():
    data = items()
    gains = recap._round_gains(data["results"]["rows"][1])
    moved = recap._places_moved(data["drivers"]["rows"], gains)
    assert moved["Max Verstappen"] == 1  # 163 points before this round (fourth), 188 now (third)
    assert moved["Lando Norris"] == -1  # third on 180 before, fourth now
    assert moved["George Russell"] == 0


def test_a_new_round_gets_one_recap_and_is_saved():
    saved = {}
    fake = FakeClaude()
    entry = recap.refresh_recaps(
        "table", items(), now=datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc), client=fake,
        loader=lambda table, season: [{"round": 15, "name": "Azerbaijan Grand Prix", "text": "Old."}],
        writer=lambda table, item: saved.update(item),
    )
    assert [(row["round"], row["kind"]) for row in entry] == [(16, "race")]
    assert entry[0]["text"] == "Verstappen won in Bahrain."
    assert [row["round"] for row in saved["rows"]] == [15, 16]
    assert saved["rows"][-1]["kind"] == "race"
    assert saved["item"] == "recaps" and saved["season"] == "2026"
    assert len(fake.calls) == 1
    prompt = json.loads(fake.calls[0]["messages"][0]["content"])
    assert prompt["round"] == 16 and "Do not invent" in fake.calls[0]["system"]


def test_an_existing_recap_means_no_model_call():
    fake = FakeClaude()
    result = recap.refresh_recaps("table", items(), client=fake, loader=lambda t, s: [{"round": 16}], writer=lambda t, i: None)
    assert result == [] and fake.calls == []


def test_only_the_latest_recaps_are_kept():
    saved = {}
    old = [{"round": n, "name": "x", "text": "t"} for n in range(1, 10)]
    recap.refresh_recaps("table", items(), client=FakeClaude(), loader=lambda t, s: old, writer=lambda t, i: saved.update(i))
    assert [row["round"] for row in saved["rows"]] == [3, 4, 5, 6, 7, 8, 9, 16]


def test_a_declined_or_empty_recap_is_an_error():
    for fake in (FakeClaude(stop="refusal"), FakeClaude(text="  ")):
        try:
            recap.write_recap({"round": 1}, fake)
        except RuntimeError:
            continue
        raise AssertionError("expected RuntimeError")


def test_the_season_view_carries_recaps_only_when_present():
    base = items()
    assert "recaps" not in season_view(base)
    base["recaps"] = {"rows": [{"round": 16, "name": "Bahrain Grand Prix", "text": "Verstappen won."}]}
    assert season_view(base)["recaps"][0]["round"] == 16


def test_a_recap_failure_never_fails_the_refresh(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_SECRET_ID", "key")
    monkeypatch.setenv("TABLE_NAME", "table")
    monkeypatch.setattr(recap, "refresh_recaps", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    assert ingest_handler._recap(items()) is None
    assert "recap_error" in capsys.readouterr().out


def test_no_key_means_no_recap(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_SECRET_ID", raising=False)
    monkeypatch.setattr(recap, "refresh_recaps", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")))
    assert ingest_handler._recap(items()) is None


def quali_rows(round_number=17):
    return [{"round": round_number, "name": "Singapore Grand Prix", "grid": [
        {"position": 1, "driver": "Max Verstappen", "team": "Red Bull", "q1": "1:30.100", "q2": "1:29.500", "q3": "1:28.900"},
        {"position": 2, "driver": "Andrea Kimi Antonelli", "team": "Mercedes", "q1": "1:30.200", "q2": "1:29.600", "q3": "1:29.042"},
        {"position": 3, "driver": "Lando Norris", "team": "McLaren", "q1": "1:30.300", "q2": "1:29.700", "q3": None},
    ]}]


def test_qualifying_facts_have_computed_gaps_and_championship_positions():
    data = items()
    facts = recap.build_qualifying_facts(data, quali_rows()[0])
    assert facts["finalSession"] == "Q3" and facts["top10"][0]["bestTime"] == "1:28.900"
    assert "gapToPole" not in facts["top10"][0]
    assert facts["top10"][1]["gapToPole"] == "+0.142"
    assert facts["top10"][1]["championshipPosition"] == 1
    assert facts["top10"][2]["bestTime"] == "1:29.700"
    assert "gapToPole" not in facts["top10"][2]  # no Q3 time, so no like-for-like gap


def test_a_qualifying_recap_is_made_between_qualifying_and_the_race():
    saved = {}
    fake = FakeClaude(text="Verstappen took pole.")
    made = recap.refresh_recaps(
        "table", items(), qualifying=lambda: quali_rows(17), client=fake,
        loader=lambda t, s: [{"round": 16, "kind": "race", "name": "Bahrain Grand Prix", "text": "Done."}],
        writer=lambda t, i: saved.update(i),
    )
    assert [(row["round"], row["kind"]) for row in made] == [(17, "qualifying")]
    assert len(fake.calls) == 1 and "qualifying classification" in fake.calls[0]["system"]
    assert [(row["round"], row["kind"]) for row in saved["rows"]] == [(16, "race"), (17, "qualifying")]


def test_no_qualifying_recap_for_a_round_that_already_has_race_results():
    fake = FakeClaude()
    made = recap.refresh_recaps(
        "table", items(), qualifying=lambda: quali_rows(16), client=fake,
        loader=lambda t, s: [{"round": 16, "kind": "race"}], writer=lambda t, i: None,
    )
    assert made == [] and fake.calls == []


def test_qualifying_recap_is_not_repeated():
    fake = FakeClaude()
    made = recap.refresh_recaps(
        "table", items(), qualifying=lambda: quali_rows(17), client=fake,
        loader=lambda t, s: [{"round": 16, "kind": "race"}, {"round": 17, "kind": "qualifying"}], writer=lambda t, i: None,
    )
    assert made == [] and fake.calls == []


def test_a_qualifying_fetch_failure_does_not_block_the_race_recap(capsys):
    saved = {}

    def broken():
        raise OSError("jolpica down")

    made = recap.refresh_recaps("table", items(), qualifying=broken, client=FakeClaude(), loader=lambda t, s: [], writer=lambda t, i: saved.update(i))
    assert [(row["round"], row["kind"]) for row in made] == [(16, "race")]
    assert "recap_error" in capsys.readouterr().out


def test_old_recaps_without_a_kind_count_as_race_recaps():
    fake = FakeClaude()
    made = recap.refresh_recaps("table", items(), client=fake, loader=lambda t, s: [{"round": 16, "name": "Bahrain Grand Prix", "text": "Old."}], writer=lambda t, i: None)
    assert made == [] and fake.calls == []


def test_parse_qualifying_keeps_the_top_ten_with_session_times():
    from f1board.parse import parse_qualifying

    payload = {"MRData": {"RaceTable": {"Races": [{"round": "17", "raceName": "Singapore Grand Prix", "QualifyingResults": [
        {"position": "1", "Driver": {"givenName": "Max", "familyName": "Verstappen"}, "Constructor": {"name": "Red Bull"}, "Q1": "1:30.1", "Q2": "1:29.5", "Q3": "1:28.9"},
        {"position": "11", "Driver": {"givenName": "A", "familyName": "B"}, "Constructor": {"name": "X"}, "Q1": "1:31.0", "Q2": "1:30.9"},
    ]}, {"round": "18", "raceName": "Later", "QualifyingResults": []}]}}}
    rows = parse_qualifying(payload)
    assert [r["round"] for r in rows] == [17]
    assert rows[0]["grid"] == [{"position": 1, "driver": "Max Verstappen", "team": "Red Bull", "q1": "1:30.1", "q2": "1:29.5", "q3": "1:28.9"}]


def test_paged_qualifying_rows_are_merged_by_round():
    from f1board.http_client import merge_race_pages

    page = lambda rows: {"MRData": {"RaceTable": {"Races": [{"round": "1", "QualifyingResults": rows}]}}}
    merged = merge_race_pages([page([{"position": "1"}]), page([{"position": "2"}])])
    assert [r["position"] for r in merged["MRData"]["RaceTable"]["Races"][0]["QualifyingResults"]] == ["1", "2"]
