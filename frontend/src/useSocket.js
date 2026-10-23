import { useEffect, useRef, useState } from "react";
import { getAuth } from "./api.js";

/**
 * Opens a WebSocket at `path` (for example "/ws/live"), calls onEvent(parsedJson) for every message,
 * and reconnects after 2 seconds if the connection drops.
 * Close codes 4400 and above mean the server refused us (bad token, not allowed), so we stop retrying.
 * Returns "connecting" | "live" | "reconnecting" | "closed".
 */
export function useSocket(path, onEvent) {
  const [state, setState] = useState("connecting");
  const handler = useRef(onEvent);
  handler.current = onEvent; // always call the latest callback without reconnecting

  useEffect(() => {
    if (!path) return undefined;
    let ws;
    let timer;
    let stopped = false;

    const connect = () => {
      const auth = getAuth();
      if (!auth) return;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${window.location.host}/api${path}?token=${auth.access_token}`);
      ws.onopen = () => setState("live");
      ws.onmessage = (e) => handler.current(JSON.parse(e.data));
      ws.onclose = (e) => {
        if (stopped) return;
        if (e.code >= 4400) {
          setState("closed");
          return;
        }
        setState("reconnecting");
        timer = setTimeout(connect, 2000);
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      if (ws) ws.close();
    };
  }, [path]);

  return state;
}
