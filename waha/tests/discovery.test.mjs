import assert from "node:assert/strict";
import test from "node:test";

import { registerDiscovery } from "../discovery.mjs";

test("publishes private app connection data through Supervisor discovery", async () => {
  let request;
  const result = await registerDiscovery({
    token: "supervisor-token",
    host: "abc123_waha",
    apiKey: "a-very-long-private-api-key",
    sessionName: "house",
    fetchImpl: async (url, init) => {
      request = { url: String(url), init };
      return new Response('{"result":"ok"}', { status: 200 });
    },
  });

  assert.equal(result, true);
  assert.equal(request.url, "http://supervisor/discovery");
  assert.equal(request.init.headers.Authorization, "Bearer supervisor-token");
  assert.deepEqual(JSON.parse(request.init.body), {
    service: "waha_whatsapp",
    config: {
      host: "abc123-waha",
      port: 3000,
      api_key: "a-very-long-private-api-key",
      session: "house",
    },
  });
});

test("skips discovery outside Home Assistant Supervisor", async () => {
  let called = false;
  const result = await registerDiscovery({
    token: "",
    host: "local-waha",
    apiKey: "a-very-long-private-api-key",
    fetchImpl: async () => {
      called = true;
      return new Response();
    },
  });

  assert.equal(result, false);
  assert.equal(called, false);
});

test("reports Supervisor discovery errors without including credentials", async () => {
  await assert.rejects(
    registerDiscovery({
      token: "supervisor-token",
      host: "abc123-waha",
      apiKey: "secret-api-key-never-log-this",
      fetchImpl: async () =>
        new Response('{"message":"service unavailable"}', { status: 503 }),
    }),
    (error) => {
      assert.match(error.message, /service unavailable/);
      assert.doesNotMatch(error.message, /secret-api-key/);
      return true;
    },
  );
});
