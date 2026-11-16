const LABELS = {
  created: "Finding an agent",
  offered: "Waiting for agent",
  assigned: "Assigned",
  picked_up: "Picked up",
  delivered: "Delivered",
  cancelled: "Cancelled",
};

function agentText(o) {
  if (o.agent_id) return `Agent ${o.agent_id}`;
  if (o.status === "offered") return "Offer sent";
  if (o.status === "created") return "In queue";
  return "Not assigned";
}

export default function OrderTable({ orders, empty, renderAction }) {
  if (orders.length === 0) return <p className="muted">{empty}</p>;
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Order</th>
            <th>Customer</th>
            <th>Status</th>
            <th>Agent</th>
            <th>Pickup</th>
            {renderAction && <th></th>}
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id}>
              <td>#{o.id}</td>
              <td>{o.customer_name}</td>
              <td><span className={`pill ${o.status}`}>{LABELS[o.status] ?? o.status}</span></td>
              <td>{agentText(o)}</td>
              <td>{o.pickup_lat.toFixed(4)}, {o.pickup_lng.toFixed(4)}</td>
              {renderAction && <td>{renderAction(o)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
