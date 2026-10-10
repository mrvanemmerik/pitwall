function base64url(bytes) {
  let bin = "";
  for (const value of bytes) bin += String.fromCharCode(value);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomUrlToken(size) {
  const bytes = new Uint8Array(size);
  crypto.getRandomValues(bytes);
  return base64url(bytes);
}

async function codeChallenge(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

function redirectUri(origin) {
  return String(origin).replace(/\/$/, "") + "/";
}

function googleRedirect(domain) {
  return String(domain).replace(/\/$/, "") + "/oauth2/idpresponse";
}

function authorizeUrl(options) {
  const url = new URL("/oauth2/authorize", String(options.domain).replace(/\/$/, "") + "/");
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", options.clientId);
  url.searchParams.set("redirect_uri", options.redirectUri);
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("identity_provider", "Google");
  url.searchParams.set("state", options.state);
  url.searchParams.set("nonce", options.nonce);
  url.searchParams.set("code_challenge", options.challenge);
  url.searchParams.set("code_challenge_method", "S256");
  return url.toString();
}

function tokenRequestBody(options) {
  return new URLSearchParams({
    grant_type: "authorization_code",
    client_id: options.clientId,
    code: options.code,
    redirect_uri: options.redirectUri,
    code_verifier: options.verifier,
  }).toString();
}

function tokenClaims(idToken) {
  const part = String(idToken).split(".")[1] || "";
  const padded = part.replace(/-/g, "+").replace(/_/g, "/");
  const json = atob(padded + "===".slice((padded.length + 3) % 4));
  return JSON.parse(json);
}

function tokenNonce(idToken) {
  return tokenClaims(idToken).nonce;
}

function displayName(idToken) {
  const claims = tokenClaims(idToken);
  return claims.name || claims.email || "Signed in";
}

function tokenExpired(idToken, nowSeconds) {
  try {
    const exp = Number(tokenClaims(idToken).exp);
    const now = nowSeconds === undefined ? Date.now() / 1000 : nowSeconds;
    return !Number.isFinite(exp) || exp - 60 <= now;
  } catch (error) {
    return true;
  }
}

const THREAD_KEY = "f1-thread";
const THREAD_MAX = 40;

function loadThread(storage) {
  try {
    const lines = JSON.parse(storage.getItem(THREAD_KEY) || "[]");
    return Array.isArray(lines) ? lines.slice(-THREAD_MAX) : [];
  } catch (error) {
    return [];
  }
}

function saveThread(storage, lines) {
  try {
    storage.setItem(THREAD_KEY, JSON.stringify(lines.slice(-THREAD_MAX)));
  } catch (error) {
    /* storage full or blocked: the thread just will not survive a reload */
  }
}

function clearThread(storage) {
  try {
    storage.removeItem(THREAD_KEY);
  } catch (error) {
    /* nothing to clear */
  }
}

function resetChat(local, session) {
  clearThread(local);
  for (const storage of [local, session]) {
    try {
      storage.removeItem("f1-chat-session");
    } catch (error) {
      /* nothing to reset */
    }
  }
}

function accessMessage(status) {
  if (status === "checking") return { text: "Checking your access\u2026", canRequest: false };
  if (status === "pending") return { text: "Your request is with the site owner. You can chat once it is approved; sign in again later to check.", canRequest: false };
  if (status === "denied") return { text: "Your request was not approved.", canRequest: false };
  if (status === "approved") return { text: "", canRequest: false };
  return { text: "Chat is invite-only while this is a proof of concept. The board is open to everyone. Request access and the site owner will review it.", canRequest: true };
}

function quotaNote(remaining) {
  if (typeof remaining !== "number") return "";
  if (remaining <= 0) return "No questions left today";
  return `${remaining} ${remaining === 1 ? "question" : "questions"} left today`;
}

function chatSessionId(storage, legacy) {
  const key = "f1-chat-session";
  let id = storage.getItem(key);
  if ((!id || id.length < 33) && legacy) {
    const prior = legacy.getItem(key);
    if (prior && prior.length >= 33) {
      id = prior;
      storage.setItem(key, id);
    }
  }
  if (!id || id.length < 33) {
    id = "chat" + randomUrlToken(32);
    storage.setItem(key, id);
  }
  return id;
}

function plainAnswer(text) {
  return String(text)
    .replace(/\s*[\[(]updatedAt:?\s*[^\])]*[\])]/gi, "")
    .replace(/\[([^\]]+)\]\(https?:\/\/[^\s)]+\)/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1");
}

function escapeAnswer(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function inlineMarkdown(text) {
  return escapeAnswer(text)
    .replace(/\[([^\]]+)\]\((https:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer nofollow">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function tableCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;
const ORDERED = /^\s*\d+[.)]\s+/;
const BULLET = /^\s*[-*\u2022]\s+/;

function renderAnswer(text) {
  const clean = String(text).replace(/\s*[\[(]updatedAt:?\s*[^\])]*[\])]/gi, "").replace(/\r/g, "");
  const lines = clean.split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
    } else if (line.includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
      const head = tableCells(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(tableCells(lines[i]));
        i += 1;
      }
      const numeric = head.map((_, index) => rows.length > 0 && rows.every((row) => /^[+\-\u2212]?\d[\d.,:']*$/.test((row[index] || "").trim())));
      const cls = (index) => (numeric[index] ? ' class="num"' : "");
      const th = head.map((cell, index) => `<th${cls(index)}>${inlineMarkdown(cell)}</th>`).join("");
      const body = rows.map((row) => `<tr>${head.map((_, index) => `<td${cls(index)}>${inlineMarkdown(row[index] || "")}</td>`).join("")}</tr>`).join("");
      out.push(`<div class="md-table"><table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div>`);
    } else if (ORDERED.test(line) || BULLET.test(line)) {
      const ordered = ORDERED.test(line);
      const marker = ordered ? ORDERED : BULLET;
      const items = [];
      while (i < lines.length && marker.test(lines[i])) {
        items.push(`<li>${inlineMarkdown(lines[i].replace(marker, ""))}</li>`);
        i += 1;
      }
      out.push(`<${ordered ? "ol" : "ul"}>${items.join("")}</${ordered ? "ol" : "ul"}>`);
    } else if (/^#{1,4}\s+/.test(line)) {
      out.push(`<p class="md-h"><strong>${inlineMarkdown(line.replace(/^#{1,4}\s+/, ""))}</strong></p>`);
      i += 1;
    } else {
      const parts = [];
      while (i < lines.length && lines[i].trim() && !ORDERED.test(lines[i]) && !BULLET.test(lines[i]) && !/^#{1,4}\s+/.test(lines[i]) &&
             !(lines[i].includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1]))) {
        parts.push(lines[i].trim());
        i += 1;
      }
      out.push(`<p>${inlineMarkdown(parts.join(" "))}</p>`);
    }
  }
  return out.join("");
}

const garageAsk = {
  accessMessage,
  renderAnswer,
  authorizeUrl,
  chatSessionId,
  clearThread,
  loadThread,
  plainAnswer,
  resetChat,
  quotaNote,
  saveThread,
  tokenExpired,
  codeChallenge,
  googleRedirect,
  randomUrlToken,
  redirectUri,
  displayName,
  tokenNonce,
  tokenRequestBody,
};

if (typeof window !== "undefined") window.garageAsk = garageAsk;
if (typeof module !== "undefined" && module.exports) module.exports = garageAsk;
