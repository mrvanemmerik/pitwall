const COLORS = [
  ["mercedes", "#27F4D2"],
  ["ferrari", "#E8002D"],
  ["red bull", "#3671C6"],
  ["mclaren", "#FF8000"],
  ["aston martin", "#229971"],
  ["williams", "#64C4FF"],
  ["alpine", "#FF87BC"],
  ["haas", "#B6BABD"],
  ["racing bulls", "#6692FF"],
  ["rb", "#6692FF"],
  ["audi", "#BB0A30"],
  ["cadillac", "#C5A572"],
  ["sauber", "#52E252"],
];

function teamColor(name) {
  const key = String(name || "").toLowerCase();
  for (const [needle, color] of COLORS) {
    if (key.includes(needle)) return color;
  }
  return "#8A8A8A";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function chip(team) {
  const color = teamColor(team);
  return `<span class="chip" style="background:${color}"></span> ${escapeHtml(team)}`;
}

function countLabel(count, singular, plural) {
  return `${count} ${Number(count) === 1 ? singular : plural}`;
}

function formatGain(points) {
  const number = Number(points || 0);
  const text = Number.isInteger(number) ? String(number) : String(number);
  return `+${text}`;
}

function roundGain(results) {
  if (!results || !results.length) return { byDriver: {} };
  const latest = results.slice().sort((a, b) => b.round - a.round)[0];
  const byDriver = {};
  for (const row of [...(latest.grandPrix || []), ...(latest.sprint || [])]) {
    byDriver[row.driver] = (byDriver[row.driver] || 0) + Number(row.points || 0);
  }
  return { round: latest.round, name: latest.name, byDriver };
}

function positionChanges(rows, gain) {
  if (!gain || !gain.round) return {};
  const before = rows
    .map((row) => ({ name: row.name, position: row.position, points: Number(row.points) - Number(gain.byDriver[row.name] || 0) }))
    .sort((a, b) => b.points - a.points || a.position - b.position);
  const changes = {};
  before.forEach((row, index) => {
    changes[row.name] = index + 1 - row.position;
  });
  return changes;
}

function moveMark(delta) {
  if (!delta) return "";
  const places = Math.abs(delta);
  const word = `${delta > 0 ? "up" : "down"} ${places} ${places === 1 ? "place" : "places"}`;
  return `<span class="move ${delta > 0 ? "up" : "down"}" aria-label="${word}">${delta > 0 ? "▲" : "▼"}${places}</span>`;
}

function driversBoard(rows, gain) {
  const note = gain && gain.round ? `<p class="kicker">R${gain.round} points</p>` : "";
  const changes = positionChanges(rows, gain);
  const head = `<li class="head" aria-hidden="true"><span class="pos"></span><span class="who">Driver</span><span class="pts">Pts</span><span class="gain">${gain && gain.round ? "R" + gain.round : ""}</span><span class="wins">Wins</span></li>`;
  const lines = rows
    .map((row) => {
      const gained = gain && gain.byDriver ? formatGain(gain.byDriver[row.name]) : "+0";
      const move = moveMark(changes[row.name]);
      return `<li><b class="pos">${row.position}</b><span class="who">${escapeHtml(row.name)}${move} <small>${chip(row.team)}</small></span><span class="pts">${row.points}</span><span class="gain">${gained}</span><span class="wins">${countLabel(row.wins, "win", "wins")}</span></li>`;
    })
    .join("");
  return `<section class="note standings"><h2>Drivers</h2>${note}<ol>${head}${lines}</ol></section>`;
}

function teamsBoard(rows) {
  const head = `<li class="head" aria-hidden="true"><span class="pos"></span><span class="who">Team</span><span class="pts">Pts</span><span class="wins">Wins</span></li>`;
  const lines = rows
    .map(
      (row) =>
        `<li><b class="pos">${row.position}</b><span class="who">${chip(row.name)}</span><span class="pts">${row.points}</span><span class="wins">${countLabel(row.wins, "win", "wins")}</span></li>`,
    )
    .join("");
  return `<section class="note standings teams"><h2>Teams</h2><ol>${head}${lines}</ol></section>`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function formatDay(isoDate) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate || "");
  if (!match) return isoDate;
  return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}`;
}

function formatUpdated(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || "");
  if (!match) return iso;
  let hour = Number(match[4]);
  const suffix = hour >= 12 ? "pm" : "am";
  hour %= 12;
  if (hour === 0) hour = 12;
  return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}, ${hour}:${match[5]} ${suffix} UTC`;
}

function utcToday(now) {
  return (now || new Date()).toISOString().slice(0, 10);
}

function dayNumber(isoDate) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate || "");
  return match ? Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) / 86400000 : NaN;
}

function daysUntil(date, today) {
  const days = dayNumber(date) - dayNumber(today);
  if (!Number.isFinite(days) || days < 0) return "";
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}

function startTime(row) {
  const match = /^(\d{2}):(\d{2})/.exec(row.time || "");
  if (!match || !/^\d{4}-\d{2}-\d{2}$/.test(row.date || "")) return "";
  return `<time class="start" datetime="${escapeHtml(row.date)}T${escapeHtml(row.time)}">${match[1]}:${match[2]} UTC</time>`;
}

function localTimeLabel(iso, locale, timeZone) {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "";
  const parts = new Intl.DateTimeFormat(locale, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
    timeZoneName: "short",
  }).formatToParts(when);
  const pick = (type) => (parts.find((part) => part.type === type) || {}).value || "";
  return `${pick("weekday")} ${pick("hour")}:${pick("minute")} ${pick("timeZoneName")}`;
}

function resultRounds(results) {
  return new Set((results || []).map((row) => Number(row.round)));
}

function roundState(row, today, done) {
  if (done.has(Number(row.round))) return "ran";
  if (row.date < today) return "pending";
  if (row.date === today) return "today";
  return "upcoming";
}

function scheduleBoard(rows, today, done) {
  const lines = rows
    .map((row) => {
      const sprint = row.hasSprint ? `<i class="sticker" data-sprint="true">Sprint</i>` : "";
      const state = roundState(row, today, done);
      const mark = { ran: `<i class="ran">Ran</i>`, pending: `<i class="ran">Results pending</i>`, today: `<i class="sticker">Race day</i>` }[state] || "";
      return `<li><b>R${row.round}</b> ${escapeHtml(row.name)} <span>${escapeHtml(row.circuit)}</span> <time>${escapeHtml(formatDay(row.date))}</time> ${mark} ${sprint}</li>`;
    })
    .join("");
  return `<section class="note"><h2>Schedule</h2><ol>${lines}</ol></section>`;
}

function pendingResults(rows, today, done) {
  const waiting = rows.filter((row) => roundState(row, today, done) === "pending");
  if (!waiting.length) return "";
  const latest = waiting[waiting.length - 1];
  return `<p class="pending" data-results-pending="${latest.round}">R${latest.round} ${escapeHtml(latest.name)} ran ${escapeHtml(formatDay(latest.date))}. Its results are not on the board yet; they arrive with the Monday refresh.</p>`;
}

function nextRoundPin(rows, today, done) {
  if (!rows.length) return "";
  const upcoming = rows.find((row) => row.date >= today && !done.has(Number(row.round)));
  const round = upcoming || rows[rows.length - 1];
  const label = upcoming ? (upcoming.date === today ? "Race day" : "Next round") : "Last round";
  const sprint = round.hasSprint ? `<i class="sticker" data-sprint="true">Sprint</i>` : "";
  const time = startTime(round);
  const away = upcoming ? daysUntil(round.date, today) : "";
  const when = [formatDay(round.date), time].filter(Boolean).join(" ");
  return `<section class="pin" data-next-round="${round.round}"><p class="kicker">${label}</p><h2>R${round.round} ${escapeHtml(round.name)}</h2><p>${escapeHtml(round.circuit)} · ${when}${away ? ` · ${away}` : ""} ${sprint}</p>${pendingResults(rows, today, done)}</section>`;
}

function recapSection(recap, round, label) {
  return `<section class="pin recap" data-recap="${round}" data-recap-kind="${label.kind}"><p class="kicker">R${round} ${escapeHtml(recap.name || "")} ${label.text} <span class="ai-tag">AI-written from the results</span></p><p>${escapeHtml(recap.text.trim())}</p></section>`;
}

function recapCards(recaps, results) {
  if (!Array.isArray(recaps) || !recaps.length || !Array.isArray(results) || !results.length) return "";
  const newest = Math.max(...results.map((row) => Number(row.round)));
  const usable = (row) => row && typeof row.text === "string" && row.text.trim();
  const kindOf = (row) => row.kind || "race";
  const race = recaps.find((row) => Number(row.round) === newest && kindOf(row) === "race" && usable(row));
  const upcoming = recaps
    .filter((row) => kindOf(row) === "qualifying" && Number(row.round) > newest && usable(row))
    .sort((x, y) => Number(y.round) - Number(x.round))[0];
  return [
    upcoming ? recapSection(upcoming, Number(upcoming.round), { kind: "qualifying", text: "qualifying recap" }) : "",
    race ? recapSection(race, newest, { kind: "race", text: "recap" }) : "",
  ].join("");
}

function finishLine(row) {
  const time = row.time ? ` <span>${escapeHtml(row.time)}</span>` : "";
  return `<li><b>${row.position}</b> ${escapeHtml(row.driver)} ${chip(row.team)}${time}</li>`;
}

function slip(title, rows) {
  return `<div class="slip"><h3>${title}</h3><ol>${(rows || []).map(finishLine).join("")}</ol></div>`;
}

function resultsBoard(rows) {
  const latestFirst = rows.slice().sort((a, b) => b.round - a.round);
  const blocks = latestFirst
    .map((row, index) => {
      const sprint = row.hasSprint ? slip(`<i class="sticker" data-sprint="true">Sprint</i>`, row.sprint) : "";
      return `<details class="round"${index === 0 ? " open" : ""}><summary>R${row.round} ${escapeHtml(row.name)}</summary>${slip("Grand Prix", row.grandPrix)}${sprint}</details>`;
    })
    .join("");
  return `<section class="note results"><h2>Results</h2>${blocks}</section>`;
}

function renderSeason(data, options) {
  const today = options && options.today ? options.today : utcToday();
  const done = resultRounds(data.results);
  return `<p class="stamp">Updated ${escapeHtml(formatUpdated(data.updatedAt))}</p>
    ${nextRoundPin(data.schedule, today, done)}
    ${recapCards(data.recaps, data.results)}
    ${driversBoard(data.drivers, roundGain(data.results))}
    ${teamsBoard(data.teams)}
    ${scheduleBoard(data.schedule, today, done)}
    ${resultsBoard(data.results)}`;
}

function renderError() {
  return `<p class="stamp">Could not load the garage board.</p><button type="button" class="retry">Retry</button>`;
}

if (typeof window !== "undefined") {
  window.renderSeason = renderSeason;
  window.renderError = renderError;
  window.localTimeLabel = localTimeLabel;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { daysUntil, formatUpdated, localTimeLabel, positionChanges, renderError, renderSeason, teamColor };
}
