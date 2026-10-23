import { useCallback, useEffect, useState } from "react";
import { api, getAuth } from "../api.js";
import OrderTable from "./OrderTable.jsx";

export default function Agent() {
  const [me, setMe] = useState(null);
  const [orders, setOrders] = useState([]);
  const [pos, setPos] = useState({ lat: "12.9716", lng: "77.5946" });
  const [sharing, setSharing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const [agent, list] = await Promise.all([api("/agents/me"), api("/orders")]);
      setMe(agent);
      setOrders(list);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  // Live sharing: stream this device's real GPS position over a WebSocket while the toggle is on
  const agentId = me?.id;
  useEffect(() => {
    if (!sharing || !agentId) return undefined;
    if (!navigator.geolocation) {
      setError("This browser can't share your location.");
      setSharing(false);
      return undefined;
    }

    const auth = getAuth();
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/api/ws/agents/${agentId}?token=${auth.access_token}`);
    let watchId;

    ws.onopen = () => {
      watchId = navigator.geolocation.watchPosition(
        (p) => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ lat: p.coords.latitude, lng: p.coords.longitude }));
          }
        },
        () => {
          setError("Location permission was denied. Allow it in your browser, or use the manual update below.");
          setSharing(false);
        },
        { enableHighAccuracy: true, maximumAge: 2000 }
      );
    };
    ws.onclose = (e) => {
      if (e.code >= 4400) {
        setError("Live sharing was rejected. Log out and log in again.");
        setSharing(false);
      }
    };

    return () => {
      if (watchId !== undefined) navigator.geolocation.clearWatch(watchId);
      ws.close();
    };
  }, [sharing, agentId]);

  async function run(action, message) {
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(message);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  const setStatus = (status) =>
    run(() => api(`/agents/${me.id}/status`, { method: "PATCH", body: { status } }), `You are now ${status}.`);

  const sendLocation = () =>
    run(
      () => api(`/agents/${me.id}/location`, {
        method: "PUT",
        body: { lat: parseFloat(pos.lat), lng: parseFloat(pos.lng) },
      }),
      "Location updated."
    );

  const complete = (id) =>
    run(() => api(`/orders/${id}/complete`, { method: "POST" }), `Order #${id} marked delivered.`);

  if (!me) return <p className="muted">{error || "Loading…"}</p>;

  return (
    <>
      <section className="panel">
        <h1>Your shift</h1>
        <p>Status: <span className={`pill ${me.status}`}>{me.status}</span></p>
        <div className="row">
          <button onClick={() => setStatus("available")} disabled={me.status === "busy"}>Go available</button>
          <button onClick={() => setStatus("offline")} disabled={me.status === "busy"}>Go offline</button>
        </div>
        {me.status === "busy" && <p className="muted">You have an active order. Mark it delivered to become available again.</p>}

        <h2>Share your location</h2>
        <p className="muted">
          Turn this on and your position streams live to dispatch and to customers waiting on your orders.
        </p>
        <button className={sharing ? "" : "primary"} onClick={() => setSharing(!sharing)}>
          {sharing ? "Stop sharing" : "Start sharing live location"}
        </button>
        {sharing && <p className="ok" role="status">Sharing your live location.</p>}

        <h2>Set location manually</h2>
        <div className="row">
          <label>Latitude<input value={pos.lat} onChange={(e) => setPos({ ...pos, lat: e.target.value })} /></label>
          <label>Longitude<input value={pos.lng} onChange={(e) => setPos({ ...pos, lng: e.target.value })} /></label>
          <button onClick={sendLocation}>Update location</button>
        </div>
        {notice && <p className="ok" role="status">{notice}</p>}
        {error && <p className="error" role="alert">{error}</p>}
      </section>

      <section className="panel">
        <h2>Your orders</h2>
        <OrderTable
          orders={orders}
          empty="No orders assigned to you yet."
          renderAction={(o) =>
            o.status === "assigned" && <button onClick={() => complete(o.id)}>Mark delivered</button>
          }
        />
      </section>
    </>
  );
}
