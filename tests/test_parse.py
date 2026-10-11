import pytest

from f1board.parse import (
    PartialSeason,
    parse_driver_standings,
    parse_results,
    parse_schedule,
    parse_team_standings,
)


DRIVERS = {
    "MRData": {
        "StandingsTable": {
            "StandingsLists": [
                {
                    "DriverStandings": [
                        {
                            "position": "1",
                            "points": "320",
                            "wins": "6",
                            "Driver": {"givenName": "Kimi", "familyName": "Antonelli"},
                            "Constructors": [{"name": "Mercedes"}],
                        },
                        {
                            "position": "2",
                            "points": "25.5",
                            "wins": "0",
                            "Driver": {"givenName": "Alex", "familyName": "Dunne"},
                            "Constructors": [{"name": "Cadillac"}],
                        },
                    ]
                }
            ]
        }
    }
}

TEAMS = {
    "MRData": {
        "StandingsTable": {
            "StandingsLists": [
                {
                    "ConstructorStandings": [
                        {
                            "position": "1",
                            "points": "500",
                            "wins": "10",
                            "Constructor": {"name": "Mercedes"},
                        }
                    ]
                }
            ]
        }
    }
}

RACES = {
    "MRData": {
        "RaceTable": {
            "Races": [
                {
                    "round": "1",
                    "raceName": "Australian Grand Prix",
                    "date": "2026-03-08",
                    "time": "04:00:00Z",
                    "Circuit": {"circuitName": "Albert Park"},
                    "Sprint": {"date": "2026-03-07"},
                },
                {
                    "round": "2",
                    "raceName": "Chinese Grand Prix",
                    "date": "2026-03-15",
                    "Circuit": {"circuitName": "Shanghai"},
                },
            ]
        }
    }
}

RESULTS = {
    "MRData": {
        "RaceTable": {
            "Races": [
                {
                    "round": "1",
                    "raceName": "Australian Grand Prix",
                    "Results": [
                        {
                            "position": "1",
                            "Driver": {"givenName": "Kimi", "familyName": "Antonelli"},
                            "Constructor": {"name": "Mercedes"},
                            "points": "25",
                            "Time": {"time": "1:30:00.000"},
                        },
                        {
                            "position": "11",
                            "Driver": {"givenName": "Back", "familyName": "Marker"},
                            "Constructor": {"name": "Haas"},
                            "status": "Retired",
                        },
                    ],
                }
            ]
        }
    }
}

SPRINT = {
    "MRData": {
        "RaceTable": {
            "Races": [
                {
                    "round": "1",
                    "raceName": "Australian Grand Prix",
                    "SprintResults": [
                        {
                            "position": "1",
                            "Driver": {"givenName": "Alex", "familyName": "Dunne"},
                            "Constructor": {"name": "Cadillac"},
                            "points": "8",
                            "Time": {"time": "25:00.000"},
                        }
                    ],
                }
            ]
        }
    }
}


def test_parse_drivers_and_half_points():
    rows = parse_driver_standings(DRIVERS)
    assert rows[0] == {
        "position": 1,
        "name": "Kimi Antonelli",
        "team": "Mercedes",
        "points": 320,
        "wins": 6,
    }
    assert rows[1]["points"] == 25.5
    assert rows[1]["team"] == "Cadillac"


def test_parse_teams():
    assert parse_team_standings(TEAMS) == [
        {"position": 1, "name": "Mercedes", "points": 500, "wins": 10}
    ]


def test_schedule_marks_sprint_weekends_only():
    rows = parse_schedule(RACES)
    assert rows[0]["hasSprint"] is True
    assert rows[0]["circuit"] == "Albert Park"
    assert rows[1]["hasSprint"] is False


def test_schedule_keeps_the_race_start_time_when_there_is_one():
    rows = parse_schedule(RACES)
    assert rows[0]["time"] == "04:00:00Z"
    assert "time" not in rows[1]


def test_results_keep_top_10_and_attach_sprint():
    rows = parse_results(RESULTS, SPRINT)
    assert rows[0]["grandPrix"] == [
        {
            "position": 1,
            "driver": "Kimi Antonelli",
            "team": "Mercedes",
            "points": 25,
            "time": "1:30:00.000",
        }
    ]
    assert rows[0]["hasSprint"] is True
    assert rows[0]["sprint"][0]["driver"] == "Alex Dunne"
    assert rows[0]["sprint"][0]["time"] == "25:00.000"
    assert rows[0]["sprint"][0]["points"] == 8


def test_missing_standings_list_is_partial():
    with pytest.raises(PartialSeason):
        parse_driver_standings({"MRData": {"StandingsTable": {"StandingsLists": []}}})


def test_schedule_keeps_weekend_session_times_in_order():
    races = {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "round": "17",
                        "raceName": "Singapore Grand Prix",
                        "date": "2026-10-11",
                        "time": "12:00:00Z",
                        "Circuit": {"circuitName": "Marina Bay"},
                        "FirstPractice": {"date": "2026-10-09", "time": "08:30:00Z"},
                        "Qualifying": {"date": "2026-10-10", "time": "13:00:00Z"},
                        "Sprint": {"date": "2026-10-10", "time": "09:00:00Z"},
                        "SprintQualifying": {"date": "2026-10-09", "time": "12:30:00Z"},
                    }
                ]
            }
        }
    }
    row = parse_schedule(races)[0]
    assert row["sessions"] == [
        {"name": "Sprint qualifying", "date": "2026-10-09", "time": "12:30:00Z"},
        {"name": "Sprint", "date": "2026-10-10", "time": "09:00:00Z"},
        {"name": "Qualifying", "date": "2026-10-10", "time": "13:00:00Z"},
    ]


def test_schedule_skips_sessions_without_a_time():
    rows = parse_schedule(RACES)
    assert "sessions" not in rows[0]
    assert "sessions" not in rows[1]
