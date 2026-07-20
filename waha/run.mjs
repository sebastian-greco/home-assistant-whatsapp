import { spawn } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";

import { registerDiscoveryWithRetry } from "./discovery.mjs";

const OPTIONS_PATH = process.env.OPTIONS_PATH || "/data/options.json";

function fail(message) {
  console.error(`[WAHA app] ${message}`);
  process.exit(1);
}

function readOptions() {
  try {
    return JSON.parse(readFileSync(OPTIONS_PATH, "utf8"));
  } catch (error) {
    fail(`Unable to read ${OPTIONS_PATH}: ${error.message}`);
  }
}

const options = readOptions();
const apiKey = String(options.api_key || "");
const sessionName = String(options.session_name || "default");
const deviceName = String(options.device_name ?? "Home Assistant");
const logLevel = String(options.log_level || "info");

if (apiKey.length < 16) {
  fail("api_key must be at least 16 characters long");
}
if (!/^[A-Za-z0-9_-]{1,64}$/.test(sessionName)) {
  fail("session_name may contain only letters, numbers, underscores, and hyphens");
}
if (deviceName.length < 1 || deviceName.length > 64) {
  fail("device_name must contain between 1 and 64 characters");
}
if (!["error", "warn", "info", "debug"].includes(logLevel)) {
  fail("log_level must be error, warn, info, or debug");
}

mkdirSync("/data/sessions", { recursive: true });
mkdirSync("/data/media", { recursive: true });

Object.assign(process.env, {
  WAHA_API_KEY: apiKey,
  WAHA_DASHBOARD_ENABLED: "true",
  WAHA_DASHBOARD_USERNAME: "admin",
  WAHA_DASHBOARD_PASSWORD: apiKey,
  WHATSAPP_SWAGGER_ENABLED: "false",
  // WAHA's entrypoint generates and prints missing Swagger credentials even
  // when Swagger is disabled. Supplying them prevents the API key from being
  // echoed into the app logs by that fallback path.
  WHATSAPP_SWAGGER_USERNAME: "admin",
  WHATSAPP_SWAGGER_PASSWORD: apiKey,
  WHATSAPP_DEFAULT_ENGINE: "GOWS",
  WHATSAPP_API_HOSTNAME: "0.0.0.0",
  WHATSAPP_API_PORT: "3000",
  WAHA_LOCAL_STORE_BASE_DIR: "/data/sessions",
  WAHA_NAMESPACE: "all",
  WHATSAPP_FILES_FOLDER: "/data/media",
  WHATSAPP_DOWNLOAD_MEDIA: options.download_media ? "true" : "false",
  WHATSAPP_FILES_LIFETIME: "86400",
  WHATSAPP_RESTART_ALL_SESSIONS: "true",
  WAHA_PRINT_QR: "false",
  WAHA_CLIENT_DEVICE_NAME: deviceName,
  WAHA_CLIENT_BROWSER_NAME: "Desktop",
  WAHA_LOG_FORMAT: "PRETTY",
  WAHA_LOG_LEVEL: logLevel,
  // Keep request logs below the normal INFO threshold. They become visible
  // automatically when the user temporarily enables DEBUG logging.
  WAHA_HTTP_LOG_LEVEL: "debug",
  HA_WAHA_SESSION_NAME: sessionName,
  HA_WAHA_AUTO_CREATE: options.auto_create_session ? "true" : "false",
  HA_WAHA_API_URL: "http://127.0.0.1:3000",
  HA_WAHA_CONTROL_PORT: "8099",
  HA_WAHA_CONTROL_HTML: "/ha/control.html",
});

console.log(`[WAHA app] Starting WAHA (GOWS) with session '${sessionName}'`);

const children = new Set();
let shuttingDown = false;

function start(command, args, label) {
  const child = spawn(command, args, {
    env: process.env,
    stdio: "inherit",
  });
  children.add(child);
  child.once("exit", (code, signal) => {
    children.delete(child);
    if (!shuttingDown) {
      console.error(
        `[WAHA app] ${label} exited unexpectedly (${signal || code || 0})`,
      );
      shutdown(code || 1);
    }
  });
  return child;
}

function shutdown(exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) child.kill("SIGTERM");
  const timer = setTimeout(() => {
    for (const child of children) child.kill("SIGKILL");
    process.exit(exitCode);
  }, 10_000);
  timer.unref();
  Promise.all(
    [...children].map(
      (child) => new Promise((resolve) => child.once("exit", resolve)),
    ),
  ).then(() => process.exit(exitCode));
}

process.on("SIGTERM", () => shutdown(0));
process.on("SIGINT", () => shutdown(0));

start("/entrypoint.sh", [], "WAHA");
start("node", ["/ha/control.mjs"], "control panel");

registerDiscoveryWithRetry({
  apiKey,
  sessionName,
})
  .then((registered) => {
    if (registered) {
      console.log("[WAHA app] Published Home Assistant integration discovery");
    }
  })
  .catch((error) => {
    console.warn(`[WAHA app] Integration discovery failed: ${error.message}`);
  });
