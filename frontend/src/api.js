// In development the Vite proxy serves the API under /api. When deployed, set VITE_API_URL to the API's address.
const BASE = (import.meta.env.VITE_API_URL || "/api").replace(/\/$/, "");

// Builds the WebSocket address for a path such as "/ws/live", for both setups.
export function wsUrl(path, token) {
  const base = BASE.startsWith("http")
    ? BASE.replace(/^http/, "ws")
    : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${BASE}`;
  return `${base}${path}?token=${token}`;
}

export function getAuth() {
  try {
    return JSON.parse(localStorage.getItem("auth"));
  } catch {
    return null;
  }
}

export function setAuth(auth) {
  if (auth) localStorage.setItem("auth", JSON.stringify(auth));
  else localStorage.removeItem("auth");
}

export async function api(path, { method = "GET", body } = {}) {
  const auth = getAuth();
  const res = await fetch(BASE + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(auth ? { Authorization: `Bearer ${auth.access_token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }

  if (!res.ok) {
    if (res.status === 401 && auth) {
      setAuth(null);
      window.location.href = "/login";
    }
    const detail = data?.detail;
    throw new Error(
      typeof detail === "string" ? detail : "Check the values you entered and try again."
    );
  }
  return data;
}
