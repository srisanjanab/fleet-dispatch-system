import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import OrderTable from "./OrderTable.jsx";

const START = { pickup_lat: "12.9716", pickup_lng: "77.5946", dropoff_lat: "12.9352", dropoff_lng: "77.6245" };

export default function Customer() {
  const [form, setForm] = useState(START);
  const [orders, setOrders] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setOrders(await api("/orders"));
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const body = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, parseFloat(v)]));
      await api("/orders", { method: "POST", body });
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="panel" onSubmit={submit}>
        <h1>Place an order</h1>
        <p className="muted">Coordinates are prefilled with two Bangalore points. A map picker comes in Week 3.</p>
        <div className="grid4">
          <label>Pickup latitude<input value={form.pickup_lat} onChange={set("pickup_lat")} required /></label>
          <label>Pickup longitude<input value={form.pickup_lng} onChange={set("pickup_lng")} required /></label>
          <label>Drop-off latitude<input value={form.dropoff_lat} onChange={set("dropoff_lat")} required /></label>
          <label>Drop-off longitude<input value={form.dropoff_lng} onChange={set("dropoff_lng")} required /></label>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "Placing…" : "Place order"}</button>
      </form>

      <section className="panel">
        <h2>Your orders</h2>
        <OrderTable orders={orders} empty="No orders yet. Place your first one above." />
      </section>
    </>
  );
}
