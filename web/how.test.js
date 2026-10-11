const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

test("how it works tab explains the weekly copy", () => {
  assert.match(html, /data-tab="how"/);
  assert.match(html, /id="how"/);
  assert.match(html, /Jolpica/);
  assert.match(html, /Sat, Sun and Mon/);
  assert.doesNotMatch(html, /refreshed every Monday/);
  assert.doesNotMatch(html.split('class="brand-mark"')[1].split("</svg>")[0], /<line/);
  assert.match(html, /A failed fetch writes nothing/);
  assert.match(html, /GET \/season/);
  assert.match(html, /Sprint slip/);
  assert.match(html, /The board is public/);
  assert.match(html, /The AWS resources/);
  assert.match(html, /Practice is not covered/);
  assert.match(html, /emails the site owner/);
  assert.match(html, /AgentCore Memory/);
  assert.match(html, /remembers what you tell it/);
});

test("admin lives in its own tab, hidden until the server says you are an admin", () => {
  assert.match(html, /<button type="button" role="tab" id="tab-admin" data-tab="admin"[^>]* hidden>Admin<\/button>/);
  assert.match(html, /<section id="admin" class="note" role="tabpanel" aria-labelledby="tab-admin" hidden>/);
  const ask = html.split('<section id="ask"')[1].split("</section>")[0];
  assert.doesNotMatch(ask, /admin/);
  const admin = html.split('<section id="admin"')[1].split("</section>")[0];
  assert.match(admin, /<button type="button" id="admin-refresh">Refresh now<\/button>/);
  assert.match(admin, /id="refresh-note" class="fine" role="status"/);
});
