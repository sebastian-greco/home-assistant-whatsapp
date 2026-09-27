import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, test } from "node:test";
import { createControlServer } from "../control.mjs";

const servers = [];
const directories = [];

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) => new Promise((resolve) => server.close(resolve)),
    ),
  );
  for (const dir of directories.splice(0)) rmSync(dir, { recursive: true, force: true });
});

async function fixture(fetchImpl, allowDirect = true) {
  const dir = mkdtempSync(join(tmpdir(), "waha-control-"));
  directories.push(dir);
  const htmlPath = join(dir, "index.html");
  writeFileSync(htmlPath, "<h1>WAHA</h1>");
  const { server } = createControlServer({
    apiUrl: "http://waha.test:3000",
    apiKey: "test-secret-key-123",
    sessionName: "default",
    htmlPath,
    allowDirect,
    fetchImpl,
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  servers.push(server);
  const address = server.address();
  return `http://127.0.0.1:${address.port}`;
}

test("blocks control access outside Home Assistant ingress", async () => {
  const base = await fixture(async () => new Response("{}"), false);
  const response = await fetch(`${base}/control/overview`);
  assert.equal(response.status, 403);
});

test("serves the ingress root when Supervisor sends a double slash", async () => {
  const base = await fixture(async () => new Response("{}"));
  const response = await fetch(`${base}//`);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "<h1>WAHA</h1>");
});

test("overview calls WAHA with the configured API key", async () => {
  const requests = [];
  const base = await fixture(async (url, init) => {
    requests.push({ url: String(url), key: init.headers.get("X-Api-Key") });
    if (String(url).includes("/api/sessions")) return Response.json([]);
    if (String(url).includes("/api/version")) return Response.json({ version: "2026.9.1", engine: "GOWS" });
    return Response.json({ uptime: 10 });
  });
  const response = await fetch(`${base}/control/overview`);
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.version.version, "2026.9.1");
  assert.equal(body.version.engine, "GOWS");
  assert.equal(body.configuredSession, "default");
  assert.ok(!JSON.stringify(body).includes("test-secret-key-123"));
  assert.equal(requests.length, 3);
  assert.ok(requests.every((request) => request.key === "test-secret-key-123"));
});

test("session creation and QR requests retain the configured session and authenticated API paths", async () => {
  const requests = [];
  const base = await fixture(async (url, init) => {
    requests.push({ url: String(url), init });
    return Response.json({ status: "ok" });
  });
  const created = await fetch(`${base}/control/session`, { method: "POST" });
  assert.equal(created.status, 201);
  assert.equal(requests[0].url, "http://waha.test:3000/api/sessions");
  assert.equal(requests[0].init.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].init.body), { name: "default", start: true });
  assert.equal(requests[0].init.headers.get("Content-Type"), "application/json");
  const qr = await fetch(`${base}/control/session/default/qr`);
  assert.equal(qr.status, 200);
  assert.equal(requests[1].url, "http://waha.test:3000/api/default/auth/qr");
  assert.ok(requests.every(({ init }) => init.headers.get("X-Api-Key") === "test-secret-key-123"));
  const invalid = await fetch(`${base}/control/session/bad%20name/qr`);
  assert.equal(invalid.status, 400);
  assert.equal(requests.length, 2);
});

test("health remains accessible outside ingress without exposing version or credentials", async () => {
  const base = await fixture(async (url, init) => {
    assert.equal(String(url), "http://waha.test:3000/api/version");
    assert.equal(init.headers.get("X-Api-Key"), "test-secret-key-123");
    return Response.json({ version: "2026.9.1", engine: "GOWS" });
  }, false);
  const response = await fetch(`${base}/health`);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { status: "ok" });
});

test("ingress headers authorize control requests but are not copied to the upstream API", async () => {
  const base = await fixture(async (_url, init) => {
    assert.equal(init.headers.get("X-Remote-User-Id"), null);
    assert.equal(init.headers.get("X-Ingress-Path"), null);
    return Response.json([]);
  }, false);
  const response = await fetch(`${base}/control/overview`, { headers: { "X-Ingress-Path": "/api/hassio_ingress/test" } });
  assert.equal(response.status, 200);
  assert.ok(!(await response.text()).includes("test-secret-key-123"));
});

test("session actions are restricted and forwarded", async () => {
  let calledUrl;
  const base = await fixture(async (url) => {
    calledUrl = String(url);
    return Response.json({ status: "ok" });
  });
  const response = await fetch(`${base}/control/session/default/restart`, { method: "POST" });
  assert.equal(response.status, 200);
  assert.equal(calledUrl, "http://waha.test:3000/api/sessions/default/restart");
  const invalid = await fetch(`${base}/control/session/default/logout`, { method: "POST" });
  assert.equal(invalid.status, 404);
});

test("known API credentials are redacted from upstream failures on every control path", async () => {
  const base = await fixture(async () => Response.json(
    { message: "Upstream rejected test-secret-key-123" },
    { status: 401 },
  ));
  for (const [path, method, status] of [
    ["/health", "GET", 503],
    ["/control/overview", "GET", 502],
    ["/control/session", "POST", 502],
    ["/control/session/default/restart", "POST", 502],
    ["/control/session/default/qr", "GET", 502],
  ]) {
    const response = await fetch(`${base}${path}`, { method });
    assert.equal(response.status, status);
    const text = await response.text();
    assert.ok(!text.includes("test-secret-key-123"), path);
    assert.ok(text.includes("[redacted]"), path);
  }
});
