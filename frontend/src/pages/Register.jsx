import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api.js";

export default function Register() {
  const [form, setForm] = useState({ name: "", email: "", password: "", role: "customer" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api("/auth/register", { method: "POST", body: form });
      navigate("/login");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel narrow" onSubmit={submit}>
      <h1>Create an account</h1>
      <label>Name
        <input value={form.name} onChange={set("name")} required />
      </label>
      <label>Email
        <input type="email" value={form.email} onChange={set("email")} required />
      </label>
      <label>Password (6 characters or more)
        <input type="password" minLength={6} value={form.password} onChange={set("password")} required />
      </label>
      <label>I am a
        <select value={form.role} onChange={set("role")}>
          <option value="customer">Customer placing orders</option>
          <option value="agent">Delivery agent</option>
        </select>
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="primary" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
      <p className="muted">Already registered? <Link to="/login">Log in</Link></p>
    </form>
  );
}
