import { useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { getAuth, setAuth } from "./api.js";
import Login from "./pages/Login.jsx";
import Register from "./pages/Register.jsx";
import Customer from "./pages/Customer.jsx";
import Agent from "./pages/Agent.jsx";
import Admin from "./pages/Admin.jsx";

const HOME = { customer: "/customer", agent: "/agent", admin: "/admin" };

export default function App() {
  const [auth, setAuthState] = useState(getAuth());
  const navigate = useNavigate();

  function onLogin(data) {
    setAuth(data);
    setAuthState(data);
    navigate(HOME[data.role]);
  }

  function logout() {
    setAuth(null);
    setAuthState(null);
    navigate("/login");
  }

  function guard(role, element) {
    if (!auth) return <Navigate to="/login" replace />;
    if (auth.role !== role) return <Navigate to={HOME[auth.role]} replace />;
    return element;
  }

  return (
    <>
      <header className="topbar">
        <span className="brand">Fleet Dispatch</span>
        {auth && (
          <span className="who">
            {auth.name} <span className="role">{auth.role}</span>
            <button className="link" onClick={logout}>Log out</button>
          </span>
        )}
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to={auth ? HOME[auth.role] : "/login"} replace />} />
          <Route path="/login" element={<Login onLogin={onLogin} />} />
          <Route path="/register" element={<Register />} />
          <Route path="/customer" element={guard("customer", <Customer />)} />
          <Route path="/agent" element={guard("agent", <Agent />)} />
          <Route path="/admin" element={guard("admin", <Admin />)} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
