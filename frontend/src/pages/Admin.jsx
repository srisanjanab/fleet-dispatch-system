import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
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
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

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

  const available = agents.filter((a) => a.status === "available").length;

  return (
    <>
      <section className="panel">
        <h1>Fleet</h1>
        <p className="muted">{available} of {agents.length} agents available</p>
        {agents.length === 0 ? (
          <p className="muted">No agents yet. Register an account with the agent role to add one.</p>
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
