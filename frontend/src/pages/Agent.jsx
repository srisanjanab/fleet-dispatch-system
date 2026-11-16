import { useCallback, useEffect, useState } from "react";
import { api, getAuth } from "../api.js";
import { distanceKm } from "../geo.js";
import { useSocket } from "../useSocket.js";
import OrderTable from "./OrderTable.jsx";

export default function Agent() {
  const [me, setMe] = useState(null);
  const [orders, setOrders] = useState([]);
  const [offers, setOffers] = useState([]);
  const [fetchedAt, setFetchedAt] = useState(Date.now());
  const [, setTick] = useState(0);
  const [pos, setPos] = useState({ lat: "12.9716", lng: "77.5946" });
  const [sharing, setSharing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const [agent, list, offerList] = await Promise.all([
        api("/agents/me"),
        api("/orders"),
        api("/offers/mine"),
      ]);
      setMe(agent);
      setOrders(list);
      setOffers(offerList);
      setFetchedAt(Date.now());
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  // Tick once a second so the countdown moves
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  // New offers are pushed to us instantly over a private WebSocket
  useSocket("/ws/me", () => load());

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
    } catch (err) {
      setError(err.message);
    }
    await load();
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

  const accept = (id) => run(() => api(`/offers/${id}/accept`, { method: "POST" }), "Order accepted. Head to the pickup point.");
  const reject = (id) => run(() => api(`/offers/${id}/reject`, { method: "POST" }), "Offer declined. It goes to the next agent.");
  const complete = (id) =>
    run(() => api(`/orders/${id}/complete`, { method: "POST" }), `Order #${id} marked delivered.`);

  const secondsLeft = (o) => Math.max(0, Math.ceil(o.seconds_left - (Date.now() - fetchedAt) / 1000));

  if (!me) return <p className="muted">{error || "Loading…"}</p>;

  const hasOffer = offers.length > 0;

  return (
    <>
      {offers.map((o) => {
        const left = secondsLeft(o);
        const away = me.lat != null ? distanceKm(me, { lat: o.pickup_lat, lng: o.pickup_lng }) : null;
        return (
          <section key={o.id} className="panel offer" aria-live="polite">
            <h2>New delivery offer: order #{o.order_id}</h2>
            <p>
              Customer {o.customer_name}.
              {away != null && ` Pickup is ${away.toFixed(1)} km from you.`}
            </p>
            <p className={left <= 10 ? "error" : "muted"}>
              {left > 0 ? `Respond within ${left} seconds, or it goes to another agent.` : "This offer has expired."}
            </p>
            <div className="row">
              <button className="primary" onClick={() => accept(o.id)} disabled={left === 0}>Accept</button>
              <button onClick={() => reject(o.id)} disabled={left === 0}>Reject</button>
            </div>
          </section>
        );
      })}

      <section className="panel">
        <h1>Your shift</h1>
        <p>Status: <span className={`pill ${me.status}`}>{me.status}</span></p>
        <div className="row">
          <button onClick={() => setStatus("available")} disabled={me.status === "busy"}>Go available</button>
          <button onClick={() => setStatus("offline")} disabled={me.status === "busy"}>Go offline</button>
        </div>
        {me.status === "busy" && (
          <p className="muted">
            {hasOffer ? "You have a pending offer." : "You have an active order. Mark it delivered to become available again."}
          </p>
        )}

        <h2>Share your location</h2>
        <p className="muted">
          Turn this on and your position streams live to dispatch and to customers waiting on your orders.
          You need a location and the available status to receive offers.
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
