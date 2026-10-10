import json
from urllib.request import urlopen


def merge_race_pages(pages):
    by_round = {}
    order = []
    for page in pages:
        for race in page["MRData"]["RaceTable"]["Races"]:
            key = int(race["round"])
            if key not in by_round:
                stored = dict(race)
                stored["Results"] = list(race.get("Results") or [])
                stored["SprintResults"] = list(race.get("SprintResults") or [])
                stored["QualifyingResults"] = list(race.get("QualifyingResults") or [])
                by_round[key] = stored
                order.append(key)
                continue
            by_round[key]["Results"].extend(race.get("Results") or [])
            by_round[key]["SprintResults"].extend(race.get("SprintResults") or [])
            by_round[key]["QualifyingResults"].extend(race.get("QualifyingResults") or [])
    return {"MRData": {"RaceTable": {"Races": [by_round[key] for key in order]}}}


def fetch_all_races(url):
    limit = 100
    offset = 0
    pages = []
    total = None
    while total is None or offset < total:
        page = fetch_json(f"{url}?limit={limit}&offset={offset}")
        pages.append(page)
        total = int(page["MRData"]["total"])
        offset += limit
    return merge_race_pages(pages)


def fetch_json(url):
    with urlopen(url, timeout=20) as response:
        if response.status != 200:
            raise OSError(f"jolpica status {response.status}")
        try:
            return json.load(response)
        except json.JSONDecodeError as exc:
            raise OSError("invalid json") from exc
