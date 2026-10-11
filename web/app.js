function localizeTimes(root) {
  root.querySelectorAll("time.start").forEach((node) => {
    const label = window.localTimeLabel(node.getAttribute("datetime"));
    if (label) node.textContent = label;
  });
}

let season = null;

function tickCountdowns(root) {
  root.querySelectorAll(".countdown[data-start]").forEach((node) => {
    const label = window.countdownLabel(node.dataset.start, Date.now());
    if (label) node.textContent = label;
  });
}

function saveCalendar(roundNumber) {
  const round = ((season && season.schedule) || []).find((row) => Number(row.round) === Number(roundNumber));
  if (!round) return;
  const blob = new Blob([window.calendarFile(round, Date.now())], { type: "text/calendar" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `pit-wall-r${round.round}.ics`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function drawBoard(root, data) {
  season = data;
  root.innerHTML = window.renderSeason(data);
  localizeTimes(root);
  tickCountdowns(root);
  const updated = document.querySelector("#admin-updated");
  if (updated) updated.textContent = window.formatUpdated(data.updatedAt);
}

const REFRESH_POLL_MS = 10000;
const REFRESH_POLL_TRIES = 18;

async function waitForRefresh(before) {
  for (let tries = 0; tries < REFRESH_POLL_TRIES; tries += 1) {
    await new Promise((resolve) => setTimeout(resolve, REFRESH_POLL_MS));
    try {
      const response = await window.fetch(window.SEASON_API, { cache: "reload" });
      if (!response.ok) continue;
      const data = await response.json();
      if (data.updatedAt !== before.updatedAt) return data;
    } catch (error) {
      /* a dropped poll just waits for the next one */
    }
  }
  return null;
}

async function refreshNow() {
  const button = document.querySelector("#admin-refresh");
  const note = document.querySelector("#refresh-note");
  const token = sessionStorage.getItem("f1-id-token");
  if (!token || !season) return;
  const before = season;
  button.disabled = true;
  note.textContent = "Starting the refresh\u2026";
  try {
    const result = await callApi("/admin/refresh", token);
    if (result.status === 429) {
      const minutes = Math.max(1, Math.ceil(Number(result.data.retryAfter || 60) / 60));
      note.textContent = `A refresh ran a moment ago. Try again in ${minutes} ${minutes === 1 ? "minute" : "minutes"}.`;
      return;
    }
    if (!result.ok) {
      note.textContent = result.status === 401 ? "Your sign-in expired. Sign in again to refresh." : "Could not start the refresh. Try again in a minute.";
      return;
    }
    note.textContent = "Refresh started. The board updates in about a minute.";
    const after = await waitForRefresh(before);
    if (!after) {
      note.textContent = "The refresh is taking longer than usual. Reload the page in a few minutes.";
      return;
    }
    drawBoard(document.querySelector("#board"), after);
    note.textContent = window.refreshOutcome(before, after);
  } catch (error) {
    note.textContent = "Could not start the refresh. Try again in a minute.";
  } finally {
    button.disabled = false;
  }
}

async function loadBoard(root, url, fetchImpl) {
  try {
    const response = await fetchImpl(url);
    if (!response.ok) throw new Error("bad status");
    drawBoard(root, await response.json());
  } catch (error) {
    root.innerHTML = window.renderError();
    const retry = root.querySelector(".retry");
    if (retry) retry.addEventListener("click", () => loadBoard(root, url, fetchImpl));
  }
}

function showTab(name) {
  const board = document.querySelector("#board");
  const ask = document.querySelector("#ask");
  const how = document.querySelector("#how");
  const onBoard = name === "board";
  board.hidden = !onBoard;
  if (ask) ask.hidden = !onBoard;
  how.hidden = name !== "how";
  document.querySelector("#admin").hidden = name !== "admin";
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    const on = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.tabIndex = on ? 0 : -1;
  });
}

function setAdmin(on) {
  const tab = document.querySelector("#tab-admin");
  tab.hidden = !on;
  if (!on && tab.getAttribute("aria-selected") === "true") showTab("board");
}

function adminTabLabel(rows) {
  const waiting = rows.filter((row) => row.status === "pending").length;
  document.querySelector("#tab-admin").textContent = waiting ? `Admin (${waiting})` : "Admin";
}

let thread = [];

function apiUrl(path) {
  return String(window.ASK_API).replace(/\/$/, "") + path;
}

async function callApi(path, token, body) {
  const response = await window.fetch(apiUrl(path), {
    method: "POST",
    headers: { "content-type": "application/json", authorization: "Bearer " + token },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try {
    data = await response.json();
  } catch (error) {
    /* an empty or non-JSON body leaves data empty */
  }
  return { status: response.status, ok: response.ok, data };
}

function applyAccess(status, admin) {
  const message = window.garageAsk.accessMessage(status);
  const approved = status === "approved";
  document.querySelector("#ask-form").hidden = !approved;
  document.querySelector("#access").hidden = approved;
  document.querySelector("#access-text").textContent = message.text;
  document.querySelector("#access-request").hidden = !message.canRequest;
  setAdmin(admin);
}

async function loadAccess(token) {
  applyAccess("checking", false);
  try {
    const result = await callApi("/access/status", token);
    if (result.status === 401) {
      sessionStorage.removeItem("f1-id-token");
      showSignedIn("");
      askNote("Your sign-in expired. Sign in again to keep asking.");
      return;
    }
    if (!result.ok) throw new Error("access status");
    applyAccess(result.data.status, Boolean(result.data.admin));
    if (result.data.admin) loadRequests(token);
  } catch (error) {
    applyAccess("approved", false);
  }
}

async function requestAccess() {
  const button = document.querySelector("#access-request");
  const token = sessionStorage.getItem("f1-id-token");
  if (!token) return;
  button.disabled = true;
  try {
    const result = await callApi("/access/request", token);
    if (result.ok) applyAccess(result.data.status, false);
    else document.querySelector("#access-text").textContent = result.data.error || "Could not send the request. Try again in a minute.";
  } catch (error) {
    document.querySelector("#access-text").textContent = "Could not send the request. Try again in a minute.";
  } finally {
    button.disabled = false;
  }
}

function renderRequests(rows) {
  const list = document.querySelector("#admin-list");
  list.replaceChildren();
  adminTabLabel(rows);
  if (!rows.length) {
    const empty = document.createElement("li");
    empty.className = "admin-empty fine";
    empty.textContent = "No requests yet.";
    list.append(empty);
    return;
  }
  rows.forEach((row) => {
    const item = document.createElement("li");
    item.className = "admin-row";
    const who = document.createElement("span");
    who.className = "admin-who";
    who.textContent = row.name ? row.name + " \u00b7 " + row.email : row.email;
    const tag = document.createElement("i");
    tag.className = "admin-status " + row.status;
    tag.textContent = row.status;
    item.append(who, tag);
    [["approve", "Approve", row.status !== "approved"], ["deny", "Deny", row.status !== "denied"]].forEach(([decision, label, show]) => {
      if (!show) return;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", () => decideAccess(row.email, decision));
      item.append(button);
    });
    list.append(item);
  });
}

async function loadRequests(token) {
  try {
    const result = await callApi("/admin/requests", token);
    if (result.ok) renderRequests(result.data.requests || []);
  } catch (error) {
    document.querySelector("#admin-note").textContent = "Could not load the requests.";
  }
}

async function decideAccess(email, decision) {
  const token = sessionStorage.getItem("f1-id-token");
  const note = document.querySelector("#admin-note");
  note.textContent = "";
  try {
    const result = await callApi("/admin/decide", token, { email, decision });
    if (result.ok) renderRequests(result.data.requests || []);
    else note.textContent = result.data.error || "Could not save that.";
  } catch (error) {
    note.textContent = "Could not save that.";
  }
}

function showSignedIn(token) {
  document.querySelector("#ask-out").hidden = Boolean(token);
  document.querySelector("#ask-form").hidden = true;
  document.querySelector("#access").hidden = true;
  setAdmin(false);
  const who = document.querySelector("#who");
  if (who) who.textContent = token ? window.garageAsk.displayName(token) : "";
  if (token) loadAccess(token);
}

function askNote(text) {
  const note = document.querySelector("#ask-note");
  if (note) note.textContent = text || "";
}

function showQuota(remaining) {
  const quota = document.querySelector("#quota");
  if (quota) quota.textContent = window.garageAsk.quotaNote(remaining);
}

function syncNewChat() {
  const button = document.querySelector("#new-chat");
  if (button) button.hidden = thread.length === 0;
}

function newChat() {
  const send = document.querySelector("#ask-send");
  if (send && send.disabled) return;
  window.garageAsk.resetChat(localStorage, sessionStorage);
  thread = [];
  document.querySelector("#thread").replaceChildren();
  const input = document.querySelector("#question");
  input.value = "";
  input.focus();
  askNote("");
  syncNewChat();
}

function line(className, text, keep) {
  const row = document.createElement("div");
  row.className = "ask-line " + className;
  if (className === "garage") row.innerHTML = window.garageAsk.renderAnswer(text);
  else row.textContent = text;
  const box = document.querySelector("#thread");
  box.append(row);
  box.scrollTop = className === "garage" ? Math.max(row.offsetTop - box.offsetTop - 8, 0) : box.scrollHeight;
  if (keep) {
    thread.push({ kind: className, text });
    window.garageAsk.saveThread(localStorage, thread);
    syncNewChat();
  }
  return row;
}

function keepDraft(question) {
  try {
    sessionStorage.setItem("f1-draft", question);
  } catch (error) {
    /* the question stays in the box if sign-in is not needed */
  }
}

function restoreDraft() {
  try {
    const draft = sessionStorage.getItem("f1-draft");
    if (draft) document.querySelector("#question").value = draft;
    sessionStorage.removeItem("f1-draft");
  } catch (error) {
    /* no draft to restore */
  }
}

function restoreThread() {
  thread = window.garageAsk.loadThread(localStorage);
  const saved = thread;
  thread = [];
  saved.forEach((entry) => line(entry.kind, entry.text, true));
}

async function signIn() {
  const ask = window.garageAsk;
  const verifier = ask.randomUrlToken(32);
  const state = ask.randomUrlToken(16);
  const nonce = ask.randomUrlToken(16);
  sessionStorage.setItem("f1-pkce", verifier);
  sessionStorage.setItem("f1-oauth-state", state);
  sessionStorage.setItem("f1-nonce", nonce);
  location.assign(ask.authorizeUrl({
    domain: window.COGNITO_DOMAIN,
    clientId: window.COGNITO_CLIENT_ID,
    redirectUri: ask.redirectUri(location.origin),
    state,
    nonce,
    challenge: await ask.codeChallenge(verifier),
  }));
}

async function finishSignIn(fetchImpl) {
  const ask = window.garageAsk;
  const params = new URLSearchParams(location.search);
  const code = params.get("code");
  if (!code) {
    const stored = sessionStorage.getItem("f1-id-token");
    if (stored && ask.tokenExpired(stored)) {
      sessionStorage.removeItem("f1-id-token");
      askNote("Your sign-in expired. Sign in again to keep asking.");
      return "";
    }
    return stored;
  }
  const state = params.get("state");
  const expected = sessionStorage.getItem("f1-oauth-state");
  const verifier = sessionStorage.getItem("f1-pkce");
  const nonce = sessionStorage.getItem("f1-nonce");
  if (!expected || state !== expected || !verifier) throw new Error("sign in again");
  const response = await fetchImpl(String(window.COGNITO_DOMAIN).replace(/\/$/, "") + "/oauth2/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: ask.tokenRequestBody({
      clientId: window.COGNITO_CLIENT_ID,
      code,
      redirectUri: ask.redirectUri(location.origin),
      verifier,
    }),
  });
  if (!response.ok) throw new Error("sign in again");
  const payload = await response.json();
  if (ask.tokenNonce(payload.id_token) !== nonce) throw new Error("sign in again");
  sessionStorage.setItem("f1-id-token", payload.id_token);
  sessionStorage.removeItem("f1-pkce");
  sessionStorage.removeItem("f1-oauth-state");
  sessionStorage.removeItem("f1-nonce");
  history.replaceState({}, "", ask.redirectUri(location.origin));
  return payload.id_token;
}

async function sendQuestion(event, fetchImpl) {
  event.preventDefault();
  const input = document.querySelector("#question");
  const question = input.value.trim();
  if (!question) return;
  const token = sessionStorage.getItem("f1-id-token");
  if (!token || window.garageAsk.tokenExpired(token)) {
    sessionStorage.removeItem("f1-id-token");
    keepDraft(question);
    input.value = "";
    askNote("Your sign-in expired. Sign in again; your question is saved.");
    showSignedIn("");
    return;
  }
  input.value = "";
  line("user", question, true);
  const waiting = line("waiting", "Looking at the board…");
  const send = document.querySelector("#ask-send");
  send.disabled = true;
  const fresh = document.querySelector("#new-chat");
  if (fresh) fresh.disabled = true;
  const giveBack = () => {
    if (!input.value) input.value = question;
    const last = thread[thread.length - 1];
    if (last && last.kind === "user" && last.text === question) {
      thread.pop();
      window.garageAsk.saveThread(localStorage, thread);
      syncNewChat();
    }
  };
  try {
    const response = await fetchImpl(window.ASK_API, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: "Bearer " + token },
      body: JSON.stringify({ question, sessionId: window.garageAsk.chatSessionId(localStorage, sessionStorage) }),
    });
    const body = await response.json();
    waiting.remove();
    showQuota(body.remaining);
    if (response.status === 401) {
      sessionStorage.removeItem("f1-id-token");
      giveBack();
      keepDraft(question);
      input.value = "";
      askNote("Your sign-in expired. Sign in again; your question is saved.");
      showSignedIn("");
      return;
    }
    if (response.status === 403 && body.access) {
      giveBack();
      applyAccess(body.access, false);
      return;
    }
    if (!response.ok) {
      giveBack();
      line("error", body.error || "could not ask the garage");
      return;
    }
    line("garage", body.answer, true);
  } catch (error) {
    waiting.remove();
    giveBack();
    line("error", "could not ask the garage");
  } finally {
    send.disabled = false;
    if (fresh) fresh.disabled = false;
  }
}

function signOut() {
  const back = window.garageAsk.redirectUri(location.origin);
  sessionStorage.removeItem("f1-id-token");
  window.garageAsk.resetChat(localStorage, sessionStorage);
  const url = new URL("/logout", String(window.COGNITO_DOMAIN).replace(/\/$/, "") + "/");
  url.searchParams.set("client_id", window.COGNITO_CLIENT_ID);
  url.searchParams.set("logout_uri", back);
  location.assign(url.toString());
}

window.addEventListener("DOMContentLoaded", () => {
  const board = document.querySelector("#board");
  loadBoard(board, window.SEASON_API, window.fetch.bind(window));
  setInterval(() => tickCountdowns(board), 60000);
  board.addEventListener("click", (event) => {
    const button = event.target.closest(".add-calendar");
    if (button) saveCalendar(button.dataset.round);
  });
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    tab.addEventListener("click", () => showTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      event.preventDefault();
      const tabs = [...document.querySelectorAll("[data-tab]")].filter((each) => !each.hidden);
      const index = tabs.indexOf(tab);
      const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : (index - 1 + tabs.length) % tabs.length;
      tabs[next].focus();
      showTab(tabs[next].dataset.tab);
    });
  });
  restoreThread();
  restoreDraft();
  document.querySelector("#sign-in").addEventListener("click", () => {
    askNote("");
    signIn().catch(() => {
      askNote("Sign-in is not available right now. Try again in a minute.");
      showSignedIn("");
    });
  });
  document.querySelector("#ask-form").addEventListener("submit", (event) => sendQuestion(event, window.fetch.bind(window)));
  document.querySelector("#sign-out").addEventListener("click", signOut);
  document.querySelector("#access-signout").addEventListener("click", signOut);
  document.querySelector("#access-request").addEventListener("click", requestAccess);
  document.querySelector("#admin-add").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = document.querySelector("#admin-email");
    decideAccess(input.value, "approve").then(() => {
      input.value = "";
    });
  });
  document.querySelector("#new-chat").addEventListener("click", newChat);
  document.querySelector("#admin-refresh").addEventListener("click", refreshNow);
  syncNewChat();
  finishSignIn(window.fetch.bind(window)).then(showSignedIn).catch(() => {
    line("error", "sign in again");
    showSignedIn("");
  });
});
