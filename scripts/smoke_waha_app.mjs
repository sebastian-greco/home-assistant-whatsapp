// Real-image smoke test with an isolated, unlinked session. Never use HA data.
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const image = process.argv[2];
const expectedVersion = process.argv[3] || "2026.9.1";
if (!image) {
  console.error("Usage: node scripts/smoke_waha_app.mjs IMAGE [WAHA_VERSION]");
  process.exit(1);
}

const id = randomBytes(8).toString("hex");
const container = `ha-waha-smoke-${id}`;
const volume = `${container}-data`;
const apiKey = randomBytes(24).toString("hex");
const fixtureDir = mkdtempSync(join(tmpdir(), "ha-waha-smoke-"));
const optionsPath = join(fixtureDir, "options.json");
let createdVolume = false;
let createdContainer = false;

function docker(...args) {
  return execFileSync("docker", args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 60_000,
  }).trim();
}

function redact(value) {
  return String(value).replaceAll(apiKey, "[redacted]");
}

function containerLogs() {
  const result = spawnSync("docker", ["logs", "--tail", "200", container], {
    encoding: "utf8",
    timeout: 20_000,
  });
  if (result.error || result.status !== 0) throw new Error("Unable to read test-container logs");
  return `${result.stdout}\n${result.stderr}`;
}

function addresses() {
  const ports = JSON.parse(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", container));
  return {
    api: `http://127.0.0.1:${ports["3000/tcp"][0].HostPort}`,
    control: `http://127.0.0.1:${ports["8099/tcp"][0].HostPort}`,
  };
}

async function request(base, path, { authenticated = true, ...init } = {}) {
  return fetch(`${base}${path}`, {
    ...init,
    headers: {
      ...(authenticated ? { "X-Api-Key": apiKey } : {}),
      ...init.headers,
    },
    signal: AbortSignal.timeout(5000),
  });
}

async function waitFor(check, description) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    try {
      const value = await check();
      if (value) return value;
    } catch {
      // Startup and restart can briefly refuse connections.
    }
    await delay(1000);
  }
  throw new Error(`Timed out waiting for ${description}`);
}

try {
  writeFileSync(optionsPath, JSON.stringify({
    api_key: apiKey,
    session_name: "default",
    device_name: "Isolated app smoke test",
    auto_create_session: true,
    log_level: "info",
    download_media: false,
  }), { mode: 0o600 });
  docker("volume", "create", volume);
  createdVolume = true;
  docker(
    "run", "--detach", "--platform", "linux/amd64", "--name", container,
    "--mount", `type=volume,source=${volume},target=/data`,
    "--mount", `type=bind,source=${optionsPath},target=/data/options.json,readonly`,
    "--publish", "127.0.0.1::3000", "--publish", "127.0.0.1::8099", image,
  );
  createdContainer = true;
  let { api, control } = addresses();
  const ingressHeaders = { "X-Ingress-Path": "/api/hassio_ingress/smoke" };

  await waitFor(async () => (await request(control, "/health")).ok, "app health");
  const version = await (await request(api, "/api/version")).json();
  assert.equal(version.version, expectedVersion);
  assert.equal(version.engine, "GOWS");
  assert.equal(version.tier, "CORE");
  assert.equal((await request(api, "/api/version", { authenticated: false })).status, 401);
  assert.equal((await request(control, "/", { authenticated: false })).status, 403);
  assert.equal((await request(control, "//", { headers: ingressHeaders })).status, 200);
  const overview = await request(control, "/control/overview", { headers: ingressHeaders });
  assert.equal(overview.status, 200);
  const overviewBody = await overview.text();
  assert.ok(!overviewBody.includes(apiKey), "Sidebar must not expose the API key");
  assert.equal(JSON.parse(overviewBody).version.version, expectedVersion);
  const dashboard = await request(api, "/dashboard/", {
    headers: { Authorization: `Basic ${Buffer.from(`admin:${apiKey}`).toString("base64")}` },
  });
  assert.equal(dashboard.status, 200);

  await waitFor(async () => {
    const sessions = await (await request(api, "/api/sessions?all=true")).json();
    return sessions.find((session) => session.name === "default");
  }, "automatic unlinked session creation");
  const stop = await request(api, "/api/sessions/default/stop", { method: "POST" });
  assert.ok(stop.ok, "Test session must stop before saving its configuration");
  const update = await request(api, "/api/sessions/default", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config: { metadata: { appSmoke: id } } }),
  });
  assert.ok(update.ok, "Test session configuration must persist");

  docker("restart", "--timeout", "20", container);
  // Docker may reassign ephemeral host ports after a container restart.
  ({ api, control } = addresses());
  await waitFor(async () => (await request(control, "/health")).ok, "health after restart");
  const restored = await (await request(api, "/api/sessions?all=true")).json();
  const matching = restored.filter((session) => session.name === "default");
  assert.equal(matching.length, 1, "Restart must not duplicate the configured session");
  assert.equal(matching[0].config?.metadata?.appSmoke, id);
  const logs = containerLogs();
  assert.ok(!logs.includes(apiKey), "Startup must not log the configured API key");
  console.log(`PASS: WAHA ${expectedVersion} GOWS/CORE; authenticated API, ingress, dashboard, unlinked session creation and configuration persistence across restart.`);
  console.log("Not tested: paired-account migration, WhatsApp delivery/votes, HAOS backup restore, or group operations on WhatsApp.");
} catch (error) {
  console.error(redact(error.message));
  if (createdContainer) {
    try {
      console.error(redact(containerLogs()));
    } catch {
      // Keep the original failure if the container is already unavailable.
    }
  }
  process.exitCode = 1;
} finally {
  if (createdContainer) {
    try { docker("rm", "--force", container); } catch { process.exitCode = 1; }
  }
  if (createdVolume) {
    try { docker("volume", "rm", volume); } catch { process.exitCode = 1; }
  }
  rmSync(fixtureDir, { recursive: true, force: true });
}
