const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

test("how it works tab explains the weekly copy", () => {
  assert.match(html, /data-tab="how"/);
  assert.match(html, /id="how"/);
  assert.match(html, /Jolpica/);
  assert.match(html, /Monday morning/);
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
