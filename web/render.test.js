const assert = require("node:assert/strict");
const test = require("node:test");
const { calendarFile, countdownLabel, daysUntil, formatUpdated, refreshOutcome, localTimeLabel, positionChanges, renderError, renderSeason, teamColor } = require("./render.js");

const season = {
  updatedAt: "2026-10-10T18:00:00Z",
  drivers: [
    { position: 1, name: "Kimi Antonelli", team: "Mercedes", points: 320, wins: 6 },
    { position: 2, name: "Lewis Hamilton", team: "Ferrari", points: 214, wins: 1 },
  ],
  teams: [{ position: 1, name: "Mercedes", points: 500, wins: 1 }],
  schedule: [
    { round: 1, name: "Australian Grand Prix", circuit: "Albert Park", date: "2026-03-08", hasSprint: true },
    { round: 2, name: "Chinese Grand Prix", circuit: "Shanghai", date: "2026-03-15", hasSprint: false },
  ],
  results: [
    {
      round: 1,
      name: "Australian Grand Prix",
      hasSprint: true,
      grandPrix: [{ position: 1, driver: "Kimi Antonelli", team: "Mercedes", points: 25, time: "1:30:00.000" }],
      sprint: [{ position: 1, driver: "Alex Dunne", team: "Cadillac", points: 8, time: "25:00.000" }],
    },
  ],
};

test("stamp and four boards render", () => {
  const html = renderSeason(season, { today: "2026-03-10" });
  assert.match(html, /Updated 10 Oct 2026, 6:00 pm UTC/);
  assert.equal(formatUpdated("2026-10-10T00:05:00Z"), "10 Oct 2026, 12:05 am UTC");
  assert.match(html, /Drivers/);
  assert.match(html, /Teams/);
  assert.match(html, /Schedule/);
  assert.match(html, /Results/);
  assert.match(html, /Kimi Antonelli/);
  assert.match(html, /data-sprint="true"/);
  assert.equal((html.match(/data-sprint="true"/g) || []).length >= 1, true);
  assert.match(html, /Sprint/);
});

test("non-sprint round has no sprint sticker", () => {
  const html = renderSeason(season, { today: "2026-03-10" });
  const chinese = html.split("Chinese Grand Prix")[1].split("</li>")[0];
  assert.equal(chinese.includes('data-sprint="true"'), false);
});

test("next round is pinned and earlier rounds are marked ran", () => {
  const html = renderSeason(season, { today: "2026-03-10" });
  assert.match(html, /Next round/);
  assert.match(html, /data-next-round="2"/);
  assert.match(html, /Shanghai · 15 Mar 2026/);
  const australian = html.split("Australian Grand Prix")[1].split("</li>")[0];
  assert.match(australian, /Ran/);
  const chinese = html.split('<section class="note"><h2>Schedule<\/h2>')[1].split("Chinese Grand Prix")[1].split("</li>")[0];
  assert.equal(chinese.includes("Ran"), false);
});

test("a finished calendar pins the last round", () => {
  const html = renderSeason(season, { today: "2026-12-01" });
  assert.match(html, /Last round/);
  assert.match(html, /data-next-round="2"/);
});

test("results lead with the latest round", () => {
  const withTwo = {
    ...season,
    results: [
      season.results[0],
      {
        round: 2,
        name: "Chinese Grand Prix",
        hasSprint: false,
        grandPrix: [{ position: 1, driver: "George Russell", team: "Mercedes", time: "1:31:00.000" }],
        sprint: [],
      },
    ],
  };
  const html = renderSeason(withTwo, { today: "2026-03-20" });
  const results = html.split("<h2>Results</h2>")[1];
  assert.ok(results.indexOf("R2 Chinese Grand Prix") < results.indexOf("R1 Australian Grand Prix"));
});

test("latest race is open and older races stay closed", () => {
  const html = renderSeason(season, { today: "2026-03-20" });
  const results = html.split("<h2>Results</h2>")[1];
  assert.match(results, /<details class="round" open><summary>R1 Australian Grand Prix/);
  assert.match(results, /Grand Prix/);
  assert.match(results, /<div class="slip"><h3><i class="sticker" data-sprint="true">Sprint<\/i><\/h3>/);
  const closed = renderSeason(
    {
      ...season,
      results: [
        season.results[0],
        {
          round: 2,
          name: "Chinese Grand Prix",
          hasSprint: false,
          grandPrix: [{ position: 1, driver: "George Russell", team: "Mercedes", points: 25, time: "1:31:00.000" }],
          sprint: [],
        },
      ],
    },
    { today: "2026-03-20" },
  ).split("<h2>Results</h2>")[1];
  assert.match(closed, /<details class="round" open><summary>R2 Chinese Grand Prix/);
  assert.match(closed, /<details class="round"><summary>R1 Australian Grand Prix/);
});

test("driver rows show points from the latest round", () => {
  const html = renderSeason(season, { today: "2026-03-20" });
  assert.match(html, /R1 points/);
  assert.match(html, /Kimi Antonelli[\s\S]*<span class="gain">\+25<\/span>/);
  assert.match(html, /Lewis Hamilton[\s\S]*<span class="gain">\+0<\/span>/);
  assert.match(html, />1<span class="unit"> win<\/span></);
  assert.match(html, />6<span class="unit"> wins<\/span></);
});

test("error copy is fixed", () => {
  const html = renderError();
  assert.match(html, /Could not load the garage board\./);
  assert.match(html, /class="retry"/);
});

test("unknown team gets the gray chip", () => {
  assert.equal(teamColor("No Such Team"), "#8A8A8A");
  assert.equal(teamColor("Mercedes"), "#27F4D2");
});

const waiting = {
  ...season,
  schedule: [
    ...season.schedule,
    { round: 3, name: "Japanese Grand Prix", circuit: "Suzuka", date: "2026-03-29", time: "05:00:00Z", hasSprint: false },
    { round: 4, name: "Bahrain Grand Prix", circuit: "Sakhir", date: "2026-04-12", hasSprint: false },
  ],
};

test("a race with no stored result yet says so instead of looking finished", () => {
  const html = renderSeason(waiting, { today: "2026-03-30" });
  const schedule = html.split('<section class="note"><h2>Schedule</h2>')[1];
  const japanese = schedule.split("Japanese Grand Prix")[1].split("</li>")[0];
  assert.match(japanese, /Results pending/);
  assert.equal(japanese.includes(">Ran<"), false);
  assert.match(html, /data-results-pending="3"/);
  assert.match(html, /R3 Japanese Grand Prix[^<]*ran 29 Mar 2026/);
  assert.match(html, /data-next-round="4"/);
});

test("ran means the results are on the board", () => {
  const html = renderSeason(waiting, { today: "2026-03-30" });
  const schedule = html.split('<section class="note"><h2>Schedule</h2>')[1];
  assert.match(schedule.split("Australian Grand Prix")[1].split("</li>")[0], />Ran</);
  assert.doesNotMatch(renderSeason(waiting, { today: "2026-03-10" }), /data-results-pending/);
});

test("race day and the countdown read plainly", () => {
  assert.equal(daysUntil("2026-03-29", "2026-03-29"), "today");
  assert.equal(daysUntil("2026-03-29", "2026-03-28"), "tomorrow");
  assert.equal(daysUntil("2026-03-29", "2026-03-26"), "in 3 days");
  assert.equal(daysUntil("2026-03-29", "2026-03-30"), "");
  const raceDay = renderSeason(waiting, { today: "2026-03-29" });
  assert.match(raceDay, /Race day/);
  assert.match(renderSeason(waiting, { today: "2026-03-26" }), /Suzuka · 29 Mar 2026[^<]*<time[^>]*>05:00 UTC<\/time>[^<]*<span class="countdown" data-start="2026-03-29T05:00:00Z">in 3 days<\/span>/);
});

test("a start time is machine readable so the browser can localise it", () => {
  const html = renderSeason(waiting, { today: "2026-03-26" });
  assert.match(html, /<time class="start" datetime="2026-03-29T05:00:00Z">05:00 UTC<\/time>/);
  assert.doesNotMatch(html, /datetime="2026-04-12T/);
  assert.equal(localTimeLabel("2026-03-29T05:00:00Z", "en-GB", "Asia/Tokyo"), "Sun 14:00 GMT+9");
  assert.equal(localTimeLabel("nonsense", "en-GB", "UTC"), "");
});

const climbing = {
  ...season,
  drivers: [
    { position: 1, name: "Kimi Antonelli", team: "Mercedes", points: 320, wins: 6 },
    { position: 2, name: "Lewis Hamilton", team: "Ferrari", points: 214, wins: 1 },
    { position: 3, name: "Alex Dunne", team: "Cadillac", points: 210, wins: 0 },
  ],
  results: [
    {
      round: 1,
      name: "Australian Grand Prix",
      hasSprint: false,
      grandPrix: [
        { position: 1, driver: "Alex Dunne", team: "Cadillac", points: 25, time: "1:30:00.000" },
        { position: 2, driver: "Kimi Antonelli", team: "Mercedes", points: 18, time: "+1.0" },
      ],
      sprint: [],
    },
  ],
};

test("position changes compare the board with the board before the latest round", () => {
  // before round 1: Kimi 302, Lewis 214, Alex 185, so nobody changed place
  const flat = positionChanges(climbing.drivers, { round: 1, byDriver: { "Alex Dunne": 25, "Kimi Antonelli": 18 } });
  assert.deepEqual(flat, { "Kimi Antonelli": 0, "Lewis Hamilton": 0, "Alex Dunne": 0 });
  // B scored 25 and now leads on 60; before the round A led on 58 to B's 35
  const overtake = positionChanges(
    [
      { position: 1, name: "B", points: 60 },
      { position: 2, name: "A", points: 58 },
    ],
    { round: 2, byDriver: { B: 25 } },
  );
  assert.deepEqual(overtake, { B: 1, A: -1 });
  // no latest round, no arrows
  assert.deepEqual(positionChanges(climbing.drivers, { byDriver: {} }), {});
});

test("standings show arrows only when a place changed", () => {
  const html = renderSeason(
    {
      ...climbing,
      drivers: [
        { position: 1, name: "B", team: "Mercedes", points: 60, wins: 1 },
        { position: 2, name: "A", team: "Ferrari", points: 58, wins: 0 },
      ],
      results: [
        { round: 2, name: "Chinese Grand Prix", hasSprint: false, grandPrix: [{ position: 1, driver: "B", team: "Mercedes", points: 25 }], sprint: [] },
      ],
    },
    { today: "2026-03-20" },
  );
  assert.match(html, /<span class="move up" aria-label="up 1 place">▲1<\/span>/);
  assert.match(html, /<span class="move down" aria-label="down 1 place">▼1<\/span>/);
  assert.doesNotMatch(renderSeason(season, { today: "2026-03-20" }), /class="move (up|down)"/);
});

test("standings read as columns with a header row", () => {
  const html = renderSeason(season, { today: "2026-03-20" });
  const drivers = html.split("<h2>Drivers</h2>")[1].split("</section>")[0];
  assert.match(drivers, /<li class="head" aria-hidden="true">/);
  assert.match(drivers, /<span class="pts">320<\/span>/);
  assert.match(drivers, /<span class="who">Kimi Antonelli/);
  const teams = html.split("<h2>Teams</h2>")[1].split("</section>")[0];
  assert.match(teams, /<span class="pts">500<\/span>/);
});

test("the page shows a loading line before the board arrives", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  const page = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
  assert.match(page, /<div id="board"[^>]*>\s*<p class="stamp">Loading the garage board/);
});


test("the newest round's recap shows with an AI label and is escaped", () => {
  const latest = Math.max(...season.results.map((row) => row.round));
  const html = renderSeason({ ...season, recaps: [{ round: latest, name: "Test Grand Prix", text: "Antonelli won <b>big</b>." }] }, { today: "2026-03-10" });
  assert.match(html, new RegExp(`data-recap="${latest}"`));
  assert.match(html, /AI-written from the results/);
  assert.match(html, /Antonelli won &lt;b&gt;big&lt;\/b&gt;\./);
  assert.doesNotMatch(html, /<b>big/);
});

test("an older recap or none at all leaves the board unchanged", () => {
  const latest = Math.max(...season.results.map((row) => row.round));
  const old = renderSeason({ ...season, recaps: [{ round: latest - 1, name: "Old", text: "Old news." }] }, { today: "2026-03-10" });
  assert.doesNotMatch(old, /data-recap/);
  assert.doesNotMatch(renderSeason(season, { today: "2026-03-10" }), /data-recap/);
  assert.doesNotMatch(renderSeason({ ...season, recaps: [] }, { today: "2026-03-10" }), /data-recap/);
});


test("a qualifying recap shows for the next round until its race has results", () => {
  const latest = Math.max(...season.results.map((row) => row.round));
  const recaps = [
    { round: latest, kind: "race", name: "Done Grand Prix", text: "Race recap text." },
    { round: latest + 1, kind: "qualifying", name: "Next Grand Prix", text: "Pole went to Verstappen." },
  ];
  const html = renderSeason({ ...season, recaps }, { today: "2026-03-10" });
  assert.match(html, /data-recap-kind="qualifying"/);
  assert.match(html, /qualifying recap/);
  assert.match(html, /Pole went to Verstappen\./);
  assert.ok(html.indexOf('data-recap-kind="qualifying"') < html.indexOf('data-recap-kind="race"'));
  const afterRace = renderSeason({ ...season, recaps: [{ round: latest, kind: "qualifying", name: "Done Grand Prix", text: "Stale quali." }] }, { today: "2026-03-10" });
  assert.doesNotMatch(afterRace, /data-recap/);
});

test("standings show the gap to the leader", () => {
  const html = renderSeason(season, { today: "2026-03-10" });
  const drivers = html.split("<h2>Drivers</h2>")[1].split("</section>")[0];
  assert.match(drivers, /<span class="gap">Gap<\/span>/);
  assert.match(drivers, /Kimi Antonelli.*?<span class="gap">—<\/span>/);
  assert.match(drivers, /Lewis Hamilton.*?<span class="gap">−106<\/span>/);
  const teams = html.split("<h2>Teams</h2>")[1];
  assert.match(teams, /<span class="gap">—<\/span>/);
});

test("a driver level on points with the leader reads level", () => {
  const tied = { ...season, drivers: [season.drivers[0], { ...season.drivers[1], points: 320 }] };
  assert.match(renderSeason(tied, { today: "2026-03-10" }), /<span class="gap">level<\/span>/);
});

test("countdown counts hours and minutes inside two days", () => {
  const start = "2026-10-11T12:00:00Z";
  const at = (h, m) => Date.UTC(2026, 9, 11, h, m);
  assert.equal(countdownLabel(start, at(7, 48)), "in 4h 12m");
  assert.equal(countdownLabel(start, at(11, 18)), "in 42m");
  assert.equal(countdownLabel(start, at(11, 59) + 30000), "in 1m");
  assert.equal(countdownLabel(start, at(13, 0)), "racing now");
  assert.equal(countdownLabel(start, at(16, 0)), "finished");
  assert.equal(countdownLabel(start, Date.UTC(2026, 9, 8, 12, 0)), null);
  assert.equal(countdownLabel("not a date", at(0, 0)), null);
});

const weekend = {
  ...season,
  schedule: [
    { round: 1, name: "Australian Grand Prix", circuit: "Albert Park", date: "2026-03-08", hasSprint: true },
    {
      round: 2,
      name: "Chinese Grand Prix",
      circuit: "Shanghai",
      date: "2026-03-15",
      time: "07:00:00Z",
      hasSprint: true,
      sessions: [
        { name: "Sprint qualifying", date: "2026-03-13", time: "07:30:00Z" },
        { name: "Sprint", date: "2026-03-14", time: "03:00:00Z" },
        { name: "Qualifying", date: "2026-03-14", time: "07:00:00Z" },
      ],
    },
  ],
};

test("next round lists its sessions, a countdown and a calendar button", () => {
  const pin = renderSeason(weekend, { today: "2026-03-10" }).split('data-next-round="2"')[1].split("</section>")[0];
  assert.match(pin, /<ul class="sessions">/);
  assert.match(pin, /Sprint qualifying<\/span> <time class="start" datetime="2026-03-13T07:30:00Z">07:30 UTC<\/time>/);
  assert.match(pin, /Race<\/span> <time class="start" datetime="2026-03-15T07:00:00Z">/);
  assert.match(pin, /<span class="countdown" data-start="2026-03-15T07:00:00Z">in 5 days<\/span>/);
  assert.match(pin, /<button type="button" class="add-calendar" data-round="2">Add to calendar<\/button>/);
});

test("older rows without times get no sessions, countdown or calendar button", () => {
  const html = renderSeason(season, { today: "2026-03-10" });
  assert.match(html, /in 5 days/);
  assert.doesNotMatch(html, /class="sessions"|class="countdown"|add-calendar/);
});

test("the last round of a finished season offers no calendar button", () => {
  const done = { ...weekend, results: [...weekend.results, { round: 2, name: "Chinese Grand Prix", hasSprint: false, grandPrix: [], sprint: [] }] };
  assert.doesNotMatch(renderSeason(done, { today: "2026-03-20" }), /add-calendar|class="sessions"/);
});

test("calendar file has one event per session in UTC", () => {
  const text = calendarFile(weekend.schedule[1], Date.UTC(2026, 2, 10, 9, 0, 0));
  const lines = text.split("\r\n");
  assert.equal(lines[0], "BEGIN:VCALENDAR");
  assert.equal(lines.filter((line) => line === "BEGIN:VEVENT").length, 4);
  assert.ok(lines.includes("UID:pitwall-2026-r2-sprint-qualifying@pitwall"));
  assert.ok(lines.includes("DTSTART:20260315T070000Z"));
  assert.ok(lines.includes("DTEND:20260315T090000Z"));
  assert.ok(lines.includes("DTEND:20260314T080000Z"));
  assert.ok(lines.includes("SUMMARY:Chinese Grand Prix: Sprint"));
  assert.ok(lines.includes("DTSTAMP:20260310T090000Z"));
  assert.ok(text.endsWith("END:VCALENDAR\r\n"));
});

test("a refresh says which results are new, or that none arrived", () => {
  const before = { updatedAt: "2026-10-10T22:00:00Z", results: [{ round: 16, name: "Bahrain Grand Prix in Malaysia" }] };
  const same = { ...before, updatedAt: "2026-10-11T15:04:00Z" };
  assert.equal(refreshOutcome(before, same), "Refreshed 11 Oct 2026, 3:04 pm UTC. No new results from Jolpica yet.");
  const newer = { ...same, results: [...before.results, { round: 17, name: "Singapore Grand Prix" }] };
  assert.equal(refreshOutcome(before, newer), "Refreshed 11 Oct 2026, 3:04 pm UTC. New results: R17 Singapore Grand Prix.");
});
