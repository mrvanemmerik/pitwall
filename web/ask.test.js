const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ask = require("./ask.js");

test("authorize url is the google code flow", async () => {
  const verifier = ask.randomUrlToken(32);
  const challenge = await ask.codeChallenge(verifier);
  const url = new URL(ask.authorizeUrl({
    domain: "https://f1-garage.auth.us-east-2.amazoncognito.com",
    clientId: "client",
    redirectUri: ask.redirectUri("https://pitwall.example.com"),
    state: "state-1",
    nonce: "nonce-1",
    challenge,
  }));
  assert.equal(url.pathname, "/oauth2/authorize");
  assert.equal(url.searchParams.get("identity_provider"), "Google");
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.equal(url.searchParams.get("redirect_uri"), "https://pitwall.example.com/");
  assert.equal(url.searchParams.get("scope"), "openid email profile");
  assert.notEqual(challenge, verifier);
});

test("token body keeps the verifier off the authorize url", () => {
  const body = new URLSearchParams(ask.tokenRequestBody({
    clientId: "client",
    code: "code-1",
    redirectUri: "https://pitwall.example.com/",
    verifier: "verifier-1",
  }));
  assert.equal(body.get("grant_type"), "authorization_code");
  assert.equal(body.get("code_verifier"), "verifier-1");
  assert.equal(body.get("client_secret"), null);
});

test("chat session id stays long enough for the harness", () => {
  const storage = new Map();
  storage.getItem = (key) => storage.get(key) || null;
  storage.setItem = (key, value) => storage.set(key, value);
  const legacy = new Map();
  legacy.getItem = (key) => legacy.get(key) || null;
  legacy.setItem = (key, value) => legacy.set(key, value);
  legacy.set("f1-chat-session", "chat" + "a".repeat(32));
  const first = ask.chatSessionId(storage, legacy);
  const second = ask.chatSessionId(storage, legacy);
  assert.equal(first, "chat" + "a".repeat(32));
  assert.equal(first, second);
  assert.equal(storage.get("f1-chat-session"), first);
});

test("answers drop markdown markers", () => {
  assert.equal(ask.plainAnswer("The next race is the **Singapore Grand Prix**."), "The next race is the Singapore Grand Prix.");
  assert.equal(ask.plainAnswer("See [the note](https://example.com/a)."), "See the note.");
});

test("the signed-in name prefers the token name", () => {
  const payload = Buffer.from(JSON.stringify({ name: "Pat Tester", email: "person@example.com" })).toString("base64url");
  assert.equal(ask.displayName("h." + payload + ".s"), "Pat Tester");
  const emailOnly = Buffer.from(JSON.stringify({ email: "person@example.com" })).toString("base64url");
  assert.equal(ask.displayName("h." + emailOnly + ".s"), "person@example.com");
});

test("the board slip is the google gate", () => {
  const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
  assert.match(html, /id="ask"/);
  assert.match(html, /Sign in with Google/);
  assert.match(html, /id="who"/);
  assert.doesNotMatch(html, /id="ask"[\s\S]*id="board"/);
});

function fakeToken(claims) {
  return "h." + Buffer.from(JSON.stringify(claims)).toString("base64url") + ".s";
}

test("an expired or unreadable token is not signed in", () => {
  assert.equal(ask.tokenExpired(fakeToken({ exp: 1000 }), 2000), true);
  assert.equal(ask.tokenExpired(fakeToken({ exp: 5000 }), 2000), false);
  assert.equal(ask.tokenExpired(fakeToken({ exp: 2030 }), 2000), true);
  assert.equal(ask.tokenExpired("garbage", 2000), true);
  assert.equal(ask.tokenExpired(fakeToken({}), 2000), true);
});

function memoryStorage() {
  const map = new Map();
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
  };
}

test("the thread survives a reload and stays short", () => {
  const storage = memoryStorage();
  assert.deepEqual(ask.loadThread(storage), []);
  const lines = [];
  for (let i = 0; i < 60; i += 1) lines.push({ kind: "user", text: "q" + i });
  ask.saveThread(storage, lines);
  const back = ask.loadThread(storage);
  assert.equal(back.length, 40);
  assert.equal(back[39].text, "q59");
  storage.setItem("f1-thread", "{not json");
  assert.deepEqual(ask.loadThread(storage), []);
  ask.clearThread(storage);
  assert.deepEqual(ask.loadThread(storage), []);
});

test("the quota note reads plainly", () => {
  assert.equal(ask.quotaNote(26), "26 questions left today");
  assert.equal(ask.quotaNote(1), "1 question left today");
  assert.equal(ask.quotaNote(0), "No questions left today");
  assert.equal(ask.quotaNote(undefined), "");
});

test("answers drop a leaked updatedAt note", () => {
  assert.equal(
    ask.plainAnswer("The next race is Singapore. [updatedAt: 2026-10-10T16:48:37Z]"),
    "The next race is Singapore.",
  );
  assert.equal(ask.plainAnswer("Kimi leads (updatedAt 2026-10-10T16:48:37Z)."), "Kimi leads.");
  assert.equal(ask.plainAnswer("Plain answer."), "Plain answer.");
});

test("renderAnswer turns markdown into safe lists, tables and links", () => {
  const html = ask.renderAnswer("Top three:\n\n1. Max Verstappen (Red Bull)\n2. Charles Leclerc\n3. Lewis Hamilton\n\n| Pos | Driver |\n|---|---|\n| 1 | **Max** |\n| 2 | Charles |\n\n- one\n- two\n\nSee [ESPN](https://espn.com/f1).");
  assert.match(html, /<ol><li>Max Verstappen \(Red Bull\)<\/li><li>Charles Leclerc<\/li><li>Lewis Hamilton<\/li><\/ol>/);
  assert.match(html, /<th class="num">Pos<\/th><th>Driver<\/th>/);
  assert.match(html, /<td class="num">1<\/td><td><strong>Max<\/strong><\/td>/);
  assert.match(html, /<ul><li>one<\/li><li>two<\/li><\/ul>/);
  assert.match(html, /<a href="https:\/\/espn.com\/f1" target="_blank" rel="noopener noreferrer nofollow">ESPN<\/a>/);
});

test("renderAnswer escapes html and refuses non-https links", () => {
  const html = ask.renderAnswer('<img src=x onerror=alert(1)> [bad](javascript:alert(1)) "quoted"');
  assert.doesNotMatch(html, /<img/);
  assert.doesNotMatch(html, /<a /);
  assert.match(html, /&lt;img/);
  assert.match(html, /&quot;quoted&quot;/);
});

test("renderAnswer drops a leaked updatedAt and keeps plain text as a paragraph", () => {
  assert.equal(ask.renderAnswer("Kimi leads. [updatedAt: 2026-10-10T16:48:37Z]"), "<p>Kimi leads.</p>");
});


test("resetChat clears the thread and the chat session in both stores", () => {
  const make = (entries) => {
    const map = new Map(Object.entries(entries));
    return { getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, v), removeItem: (k) => map.delete(k), has: (k) => map.has(k) };
  };
  const local = make({ "f1-chat-session": "chat-local-session-0000000000000000000", "f1-thread": "[1]" });
  const session = make({ "f1-chat-session": "chat-session-session-0000000000000000", "f1-id-token": "keep-me" });
  ask.resetChat(local, session);
  assert.equal(local.has("f1-chat-session"), false);
  assert.equal(session.has("f1-chat-session"), false);
  assert.equal(session.has("f1-id-token"), true);
  const next = ask.chatSessionId(local, session);
  assert.ok(next.length >= 33);
  assert.notEqual(next, "chat-local-session-0000000000000000000");
});


test("only all-number columns are right-aligned", () => {
  const html = ask.renderAnswer("| Driver | Team | Pts |\n|---|---|---|\n| Kimi | Mercedes | 320 |\n| George | Mercedes | 236 |");
  assert.match(html, /<th>Driver<\/th><th>Team<\/th><th class="num">Pts<\/th>/);
  assert.match(html, /<td>Mercedes<\/td><td class="num">320<\/td>/);
});


test("accessMessage only offers a request when there is none yet", () => {
  assert.equal(ask.accessMessage("none").canRequest, true);
  assert.equal(ask.accessMessage(undefined).canRequest, true);
  for (const status of ["pending", "denied", "approved", "checking"]) assert.equal(ask.accessMessage(status).canRequest, false);
  assert.match(ask.accessMessage("pending").text, /approved/);
  assert.equal(ask.accessMessage("approved").text, "");
});
