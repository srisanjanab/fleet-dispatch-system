import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import LiveMap, { COLORS } from "../LiveMap.jsx";
import { useSocket } from "../useSocket.js";
import OrderTable from "./OrderTable.jsx";

export default function Admin() {
  const [agents, setAgents] = useState([]);
  const [orders, setOrders] = useState([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const [a, o] = await Promise.all([api("/agents"), api("/orders")]);
      setAgents(a);
      setOrders(o);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000); // slower safety refresh; live updates arrive over the WebSocket
    return () => clearInterval(t);
  }, [load]);

  // Live updates: patch just the agent that changed
  const live = useSocket("/ws/live", (event) => {
    setAgents((prev) =>
      prev.map((a) => {
        if (a.id !== event.agent_id) return a;
        if (event.type === "location") return { ...a, lat: event.lat, lng: event.lng };
        if (event.type === "status") return { ...a, status: event.status };
        return a;
      })
    );
    if (event.type === "status") load(); // statuses change when orders move, so refresh orders too
  });

  async function assign(id) {
    setError("");
    setNotice("");
    try {
      const r = await api(`/orders/${id}/assign`, { method: "POST" });
      setNotice(`Order #${id} assigned to ${r.agent_name}.`);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  const markers = useMemo(
    () => [
      ...agents
        .filter((a) => a.lat != null)
        .map((a) => ({
          id: `a${a.id}`, lat: a.lat, lng: a.lng,
          color: COLORS[a.status] ?? COLORS.offline,
          label: `${a.name} (${a.status})`,
        })),
      ...orders
        .filter((o) => o.status === "created")
        .map((o) => ({
          id: `o${o.id}`, lat: o.pickup_lat, lng: o.pickup_lng,
          color: COLORS.pickup, label: `Order #${o.id} waiting for pickup`, radius: 7,
        })),
    ],
    [agents, orders]
  );

  const available = agents.filter((a) => a.status === "available").length;
  const liveText = { live: "Live", connecting: "Connecting…", reconnecting: "Reconnecting…", closed: "Disconnected" }[live];

  return (
    <>
      <section className="panel">
        <div className="row spread">
          <h1>Fleet</h1>
          <span className={`badge ${live}`}>{liveText}</span>
        </div>
        <p className="muted">{available} of {agents.length} agents available</p>
        <LiveMap markers={markers} height={460} />
        <p className="legend">
          <span><i className="dot" style={{ background: COLORS.available }} />Available</span>
          <span><i className="dot" style={{ background: COLORS.busy }} />Busy</span>
          <span><i className="dot" style={{ background: COLORS.offline }} />Offline</span>
          <span><i className="dot" style={{ background: COLORS.pickup }} />Order waiting</span>
        </p>

        {agents.length === 0 ? (
          <p className="muted">No agents yet. Register an account with the agent role, or run the simulator.</p>
        ) : (
          <div className="scroll">
            <table>
              <thead><tr><th>Agent</th><th>Status</th><th>Last location</th></tr></thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.id}>
                    <td>{a.name}</td>
                    <td><span className={`pill ${a.status}`}>{a.status}</span></td>
                    <td>{a.lat != null ? `${a.lat.toFixed(4)}, ${a.lng.toFixed(4)}` : "Not shared yet"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Orders</h2>
        {notice && <p className="ok" role="status">{notice}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <OrderTable
          orders={orders}
          empty="No orders yet."
          renderAction={(o) =>
            o.status === "created" && <button className="primary" onClick={() => assign(o.id)}>Assign nearest agent</button>
          }
        />
      </section>
    </>
  );
}
