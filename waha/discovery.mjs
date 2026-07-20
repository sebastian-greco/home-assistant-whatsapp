const DEFAULT_SUPERVISOR_URL = "http://supervisor";

async function responseMessage(response) {
  const text = await response.text();
  if (!text) return `Supervisor returned HTTP ${response.status}`;
  try {
    const data = JSON.parse(text);
    return data?.message || data?.error || text;
  } catch {
    return text;
  }
}

export async function registerDiscovery({
  token = process.env.SUPERVISOR_TOKEN || "",
  host = process.env.HOSTNAME || "",
  apiKey = process.env.WAHA_API_KEY || "",
  sessionName = process.env.HA_WAHA_SESSION_NAME || "default",
  port = 3000,
  supervisorUrl = DEFAULT_SUPERVISOR_URL,
  fetchImpl = globalThis.fetch,
} = {}) {
  if (!token || !host || !apiKey) return false;
  const discoveryHost = host.replaceAll("_", "-");

  const response = await fetchImpl(new URL("/discovery", supervisorUrl), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({
      service: "waha_whatsapp",
      config: {
        host: discoveryHost,
        port,
        api_key: apiKey,
        session: sessionName,
      },
    }),
  });

  if (!response.ok) {
    throw new Error(await responseMessage(response));
  }
  return true;
}

export async function registerDiscoveryWithRetry(
  options = {},
  { attempts = 5, delayMs = 2000 } = {},
) {
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await registerDiscovery(options);
    } catch (error) {
      if (attempt === attempts) throw error;
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  return false;
}
