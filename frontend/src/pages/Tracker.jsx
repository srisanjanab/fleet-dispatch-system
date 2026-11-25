import { useMemo, useState } from "react";
import LiveMap, { COLORS } from "../LiveMap.jsx";
import { useSocket } from "../useSocket.js";
import { distanceKm, etaMinutes } from "../geo.js";

export default function Tracker({ order }) {
  const [pos, setPos] = useState(null);

  const state = useSocket(`/ws/orders/${order.id}/track`, (event) => {
    if (event.type === "location") setPos({ lat: event.lat, lng: event.lng });
  });

  const pickup = { lat: order.pickup_lat, lng: order.pickup_lng };
  const dropoff = { lat: order.dropoff_lat, lng: order.dropoff_lng };

  const markers = useMemo(
    () => [
      { id: "pickup", ...pickup, color: COLORS.pickup, label: "Pickup", radius: 8 },
      { id: "dropoff", ...dropoff, color: COLORS.dropoff, label: "Drop-off", radius: 8 },
      ...(pos ? [{ id: "agent", ...pos, color: COLORS.busy, label: "Your agent", radius: 11 }] : []),
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pos, order.id]
  );

  // Before pickup the agent is heading to the pickup point; after pickup, to the drop-off
  const pickedUp = order.status === "picked_up";
  const target = pickedUp ? dropoff : pickup;
  const targetName = pickedUp ? "drop-off point" : "pickup point";
  const km = pos ? distanceKm(pos, target) : null;

  return (
    <section className="panel">
      <h2>Tracking order #{order.id}</h2>
      <p className="muted">
        {state === "reconnecting" && "Connection lost. Reconnecting…"}
        {state === "closed" && "Live tracking isn't available for this order."}
        {(state === "live" || state === "connecting") &&
          (pos
            ? `${pickedUp ? "Your parcel is on its way." : "Your agent is heading to the pickup."} About ${km.toFixed(1)} km from the ${targetName}, roughly ${etaMinutes(km)} min away.`
            : "Waiting for your agent to share a location…")}
      </p>
      <LiveMap markers={markers} />
      <p className="muted small">Estimate based on straight-line distance, adjusted for roads, at an average city speed.</p>
      <p className="legend">
        <span><i className="dot" style={{ background: COLORS.busy }} />Your agent</span>
        <span><i className="dot" style={{ background: COLORS.pickup }} />Pickup</span>
        <span><i className="dot" style={{ background: COLORS.dropoff }} />Drop-off</span>
      </p>
    </section>
  );
}
