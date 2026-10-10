import pytest

from f1board.season import IncompleteSeason, assemble_items
from tests.test_parse import DRIVERS, RACES, RESULTS, SPRINT, TEAMS


PAYLOADS = {
    "drivers": DRIVERS,
    "teams": TEAMS,
    "races": RACES,
    "results": RESULTS,
    "sprint": SPRINT,
}


def test_assemble_builds_five_items():
    items = assemble_items(PAYLOADS, "2026-10-10T18:00:00Z", "https://api.jolpi.ca/ergast/f1/2026/")
    assert set(items) == {"drivers", "teams", "schedule", "results", "meta"}
    assert items["drivers"]["season"] == "2026"
    assert items["drivers"]["item"] == "drivers"
    assert items["drivers"]["rows"][0]["name"] == "Kimi Antonelli"
    assert items["schedule"]["rows"][0]["hasSprint"] is True
    assert items["meta"]["updatedAt"] == "2026-10-10T18:00:00Z"
    assert items["meta"]["source"] == "https://api.jolpi.ca/ergast/f1/2026/"


def test_missing_payload_does_not_return_items():
    broken = dict(PAYLOADS)
    del broken["sprint"]
    with pytest.raises(IncompleteSeason):
        assemble_items(broken, "2026-10-10T18:00:00Z", "https://api.jolpi.ca/ergast/f1/2026/")
