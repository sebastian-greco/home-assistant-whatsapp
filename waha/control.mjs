import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { pathToFileURL } from "node:url";

const ALLOWED_ACTIONS = new Set(["start", "stop", "restart"]);
const SESSION_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

function json(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(payload),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
  });
  res.end(payload);
}

function ingressRequest(req, allowDirect) {
  return (
    allowDirect ||
    Boolean(req.headers["x-ingress-path"] || req.headers["x-remote-user-id"])
  );
}

function requestUrl(requestTarget) {
  const target = requestTarget || "/";
  // Supervisor can request an ingress root ending in "//". WHATWG URL treats
  // that as a protocol-relative URL, so collapse only repeated leading slashes.
  return new URL(target.replace(/^\/{2,}/, "/"), "http://control.local");
}

async function parseResponse(response) {
  const text = await response.text();
  let body = text;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      // WAHA occasionally returns plain text for action endpoints.
    }
  }
  if (!response.ok) {
    const message =
      typeof body === "object" && body?.message ? body.message : text;
    throw new Error(message || `WAHA returned HTTP ${response.status}`);
  }
  return body;
}

export function createControlServer({
  apiUrl = process.env.HA_WAHA_API_URL || "http://127.0.0.1:3000",
  apiKey = process.env.WAHA_API_KEY || "",
  sessionName = process.env.HA_WAHA_SESSION_NAME || "default",
  htmlPath = process.env.HA_WAHA_CONTROL_HTML || "/ha/control.html",
  allowDirect = process.env.HA_WAHA_ALLOW_DIRECT === "true",
  fetchImpl = globalThis.fetch,
} = {}) {
  const html = readFileSync(htmlPath);

  function safeError(error) {
    const message = String(error?.message || error);
    return apiKey ? message.replaceAll(apiKey, "[redacted]") : message;
  }

  async function waha(path, init = {}) {
    const headers = new Headers(init.headers || {});
    headers.set("X-Api-Key", apiKey);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    return parseResponse(
      await fetchImpl(new URL(path, apiUrl), { ...init, headers }),
    );
  }

  const server = createServer(async (req, res) => {
    const url = requestUrl(req.url);

    if (url.pathname === "/health") {
      try {
        await waha("/api/version");
        return json(res, 200, { status: "ok" });
      } catch (error) {
        return json(res, 503, { status: "unavailable", error: safeError(error) });
      }
    }

    if (!ingressRequest(req, allowDirect)) {
      return json(res, 403, { error: "Home Assistant ingress is required" });
    }

    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      res.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "Content-Security-Policy":
          "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
      });
      return res.end(html);
    }

    if (req.method === "GET" && url.pathname === "/control/overview") {
      try {
        const [version, serverStatus, sessions] = await Promise.all([
          waha("/api/version"),
          waha("/api/server/status"),
          waha("/api/sessions?all=true"),
        ]);
        return json(res, 200, {
          version,
          serverStatus,
          sessions,
          configuredSession: sessionName,
        });
      } catch (error) {
        return json(res, 502, { error: safeError(error) });
      }
    }

    if (req.method === "POST" && url.pathname === "/control/session") {
      try {
        const session = await waha("/api/sessions", {
          method: "POST",
          body: JSON.stringify({ name: sessionName, start: true }),
        });
        return json(res, 201, session);
      } catch (error) {
        return json(res, 502, { error: safeError(error) });
      }
    }

    const actionMatch = url.pathname.match(
      /^\/control\/session\/([^/]+)\/(start|stop|restart)$/,
    );
    if (req.method === "POST" && actionMatch) {
      const name = decodeURIComponent(actionMatch[1]);
      const action = actionMatch[2];
      if (!SESSION_PATTERN.test(name) || !ALLOWED_ACTIONS.has(action)) {
        return json(res, 400, { error: "Invalid session or action" });
      }
      try {
        const result = await waha(
          `/api/sessions/${encodeURIComponent(name)}/${action}`,
          { method: "POST" },
        );
        return json(res, 200, result || { status: "ok" });
      } catch (error) {
        return json(res, 502, { error: safeError(error) });
      }
    }

    const qrMatch = url.pathname.match(/^\/control\/session\/([^/]+)\/qr$/);
    if (req.method === "GET" && qrMatch) {
      const name = decodeURIComponent(qrMatch[1]);
      if (!SESSION_PATTERN.test(name)) {
        return json(res, 400, { error: "Invalid session" });
      }
      try {
        const qr = await waha(`/api/${encodeURIComponent(name)}/auth/qr`);
        return json(res, 200, qr);
      } catch (error) {
        return json(res, 502, { error: safeError(error) });
      }
    }

    return json(res, 404, { error: "Not found" });
  });

  return { server, waha };
}

export async function ensureSession(waha, sessionName) {
  for (let attempt = 0; attempt < 45; attempt += 1) {
    try {
      const sessions = await waha("/api/sessions?all=true");
      if (!sessions.some((session) => session.name === sessionName)) {
        await waha("/api/sessions", {
          method: "POST",
          body: JSON.stringify({ name: sessionName, start: true }),
        });
        console.log(`[WAHA app] Created session '${sessionName}'`);
      }
      return;
    } catch (error) {
      if (attempt === 44) {
        console.error(`[WAHA app] Could not initialize session: ${error.message}`);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 2_000));
    }
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.HA_WAHA_CONTROL_PORT || 8099);
  const sessionName = process.env.HA_WAHA_SESSION_NAME || "default";
  const { server, waha } = createControlServer({ sessionName });
  server.listen(port, "0.0.0.0", () => {
    console.log(`[WAHA app] Sidebar control panel listening on port ${port}`);
  });
  if (process.env.HA_WAHA_AUTO_CREATE === "true") {
    ensureSession(waha, sessionName);
  }
}
