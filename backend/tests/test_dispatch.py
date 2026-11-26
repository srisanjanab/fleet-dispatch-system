from concurrent.futures import ThreadPoolExecutor

from app.main import dispatch_waiting, expire_offers

PICKUP = (12.9716, 77.5946)
NEAR = (12.9726, 77.5956)  # about 150 m from the pickup
FAR = (13.0500, 77.7000)   # about 14 km from the pickup


# ---------- choosing an agent ----------
def test_nearest_available_agent_gets_the_offer(api):
    far = api.agent(*FAR)
    near = api.agent(*NEAR)
    order = api.order(*PICKUP)

    r = api.assign(order)

    assert r.status_code == 200
    assert r.json()["agent_id"] == near
    assert api.order_status(order) == "offered"
    assert api.agent_status(near) == "busy"       # reserved while the offer is open
    assert api.agent_status(far) == "available"


def test_agent_without_a_location_is_never_offered_an_order(api):
    api.agent(None, None)
    order = api.order(*PICKUP)
    assert api.assign(order).status_code == 409
    assert api.order_status(order) == "created"


def test_offline_agent_is_never_offered_an_order(api):
    api.agent(*NEAR, status="offline")
    order = api.order(*PICKUP)
    assert api.assign(order).status_code == 409


# ---------- accepting, rejecting, expiring ----------
def test_accepting_assigns_the_order_and_a_second_accept_fails(api):
    api.agent(*NEAR)
    order = api.order(*PICKUP)
    offer = api.assign(order).json()["offer_id"]

    assert api.accept(offer).status_code == 200
    assert api.order_status(order) == "assigned"
    assert api.accept(offer).status_code == 409


def test_rejected_offer_goes_to_next_nearest_agent_and_never_back(api):
    near = api.agent(*NEAR)
    far = api.agent(*FAR)
    order = api.order(*PICKUP)

    first = api.assign(order).json()
    assert first["agent_id"] == near

    assert api.reject(first["offer_id"]).status_code == 200
    offers = api.pending()
    assert len(offers) == 1 and offers[0]["agent_id"] == far   # moved on automatically

    assert api.reject(offers[0]["id"]).status_code == 200
    assert api.pending() == []                                  # both tried: nobody left
    assert api.order_status(order) == "created"                 # waiting in the queue
    assert api.assign(order).status_code == 409                 # near and far are free again but already tried


def test_unanswered_offer_expires_and_returns_to_the_queue(api):
    agent = api.agent(*NEAR)
    order = api.order(*PICKUP)
    offer = api.assign(order).json()["offer_id"]

    api.sql("UPDATE offers SET expires_at = now() - interval '5 seconds' WHERE id=$1", offer)
    api.run(expire_offers)

    assert api.order_status(order) == "created"
    assert api.agent_status(agent) == "available"
    assert api.accept(offer).status_code == 409


def test_accept_after_the_deadline_loses_even_before_the_expiry_job_runs(api):
    api.agent(*NEAR)
    order = api.order(*PICKUP)
    offer = api.assign(order).json()["offer_id"]

    api.sql("UPDATE offers SET expires_at = now() - interval '1 second' WHERE id=$1", offer)
    r = api.accept(offer)  # offer still says 'pending', but the database clock says it is too late

    assert r.status_code == 409
    assert api.order_status(order) == "offered"  # unchanged: the expiry job has not run yet


def test_an_agent_cannot_accept_someone_elses_offer(api):
    owner_headers, owner = api.register("agent")
    other_headers, _ = api.register("agent")
    api.c.put(f"/agents/{owner['agent_id']}/location", json={"lat": NEAR[0], "lng": NEAR[1]}, headers=owner_headers)
    api.c.patch(f"/agents/{owner['agent_id']}/status", json={"status": "available"}, headers=owner_headers)
    offer = api.assign(api.order(*PICKUP)).json()["offer_id"]

    assert api.accept(offer, headers=other_headers).status_code == 403
    assert api.accept(offer, headers=owner_headers).status_code == 200


# ---------- the waiting queue ----------
def test_waiting_orders_are_offered_oldest_first_when_an_agent_appears(api):
    first = api.order(*PICKUP)
    api.order(*PICKUP)
    api.run(dispatch_waiting)
    assert api.pending() == []  # no agents yet: both orders just wait

    api.agent(*NEAR)
    api.run(dispatch_waiting)

    offers = api.pending()
    assert len(offers) == 1
    assert offers[0]["order_id"] == first


# ---------- the full journey ----------
def test_full_lifecycle_frees_the_agent_at_the_end(api):
    agent = api.agent(*NEAR)
    order = api.order(*PICKUP)
    offer = api.assign(order).json()["offer_id"]

    assert api.accept(offer).status_code == 200
    assert api.post(f"/orders/{order}/pickup").status_code == 200
    assert api.order_status(order) == "picked_up"
    assert api.post(f"/orders/{order}/complete").status_code == 200
    assert api.order_status(order) == "delivered"
    assert api.agent_status(agent) == "available"


# ---------- cancelling ----------
def test_cancelling_an_offered_order_frees_the_agent_and_voids_the_offer(api):
    agent = api.agent(*NEAR)
    order = api.order(*PICKUP)
    offer = api.assign(order).json()["offer_id"]

    assert api.post(f"/orders/{order}/cancel").status_code == 200
    assert api.order_status(order) == "cancelled"
    assert api.agent_status(agent) == "available"
    assert api.accept(offer).status_code == 409


def test_cannot_cancel_after_pickup(api):
    api.agent(*NEAR)
    order = api.order(*PICKUP)
    api.accept(api.assign(order).json()["offer_id"])
    api.post(f"/orders/{order}/pickup")
    assert api.post(f"/orders/{order}/cancel").status_code == 409


def test_customer_can_cancel_only_their_own_order(api):
    owner, _ = api.register("customer")
    other, _ = api.register("customer")
    order = api.order(headers=owner)

    assert api.post(f"/orders/{order}/cancel", headers=other).status_code == 403
    assert api.post(f"/orders/{order}/cancel", headers=owner).status_code == 200
    assert api.post(f"/orders/{order}/cancel", headers=owner).status_code == 409  # already cancelled


# ---------- the guarantee the whole project is built around ----------
def test_concurrent_dispatch_never_double_books_an_agent(api):
    agents = [api.agent(12.9716 + i * 0.001, 77.5946) for i in range(15)]
    orders = [api.order(*PICKUP) for _ in range(50)]

    with ThreadPoolExecutor(max_workers=25) as pool:
        results = list(pool.map(api.assign, orders))  # 50 simultaneous requests for 15 agents

    ok = [r.json() for r in results if r.status_code == 200]
    assert all(r.status_code in (200, 409) for r in results)
    assert len(ok) == len(agents)                              # every agent used exactly once
    assert len({o["agent_id"] for o in ok}) == len(agents)     # ...and never twice
    doubled = api.sql_val(
        "SELECT count(*) FROM (SELECT agent_id FROM offers WHERE status='pending' "
        "GROUP BY agent_id HAVING count(*) > 1) t"
    )
    assert doubled == 0
