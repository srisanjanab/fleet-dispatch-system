def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_register_then_login_returns_role_and_token(api):
    _, login = api.register("customer")
    assert login["role"] == "customer"
    assert login["access_token"]


def test_duplicate_email_is_rejected(api):
    body = {"email": "dup@test.com", "name": "A", "password": "secret123", "role": "customer"}
    assert api.c.post("/auth/register", json=body).status_code == 201
    assert api.c.post("/auth/register", json=body).status_code == 409


def test_cannot_self_register_as_admin(api):
    body = {"email": "sneaky@test.com", "name": "A", "password": "secret123", "role": "admin"}
    assert api.c.post("/auth/register", json=body).status_code == 422


def test_wrong_password_and_unknown_email_give_the_same_error(api):
    body = {"email": "known@test.com", "name": "A", "password": "secret123", "role": "customer"}
    api.c.post("/auth/register", json=body)
    wrong_password = api.c.post("/auth/login", json={"email": "known@test.com", "password": "nope-nope"})
    unknown_email = api.c.post("/auth/login", json={"email": "ghost@test.com", "password": "secret123"})
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()  # attackers can't learn which emails exist


def test_endpoints_require_a_token(client):
    assert client.get("/orders").status_code == 401


def test_customer_cannot_list_the_fleet(api):
    headers, _ = api.register("customer")
    assert api.c.get("/agents", headers=headers).status_code == 403


def test_customer_cannot_read_another_customers_order(api):
    owner, _ = api.register("customer")
    other, _ = api.register("customer")
    order = api.order(headers=owner)
    assert api.c.get(f"/orders/{order}", headers=owner).status_code == 200
    assert api.c.get(f"/orders/{order}", headers=other).status_code == 403


def test_agent_cannot_change_another_agents_status(api):
    _, first = api.register("agent")
    second_headers, _ = api.register("agent")
    r = api.c.patch(f"/agents/{first['agent_id']}/status", json={"status": "offline"}, headers=second_headers)
    assert r.status_code == 403
