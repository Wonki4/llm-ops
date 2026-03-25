"""E2E tests for budget search API against live Docker environment.

Requires:
  - docker compose up (all services running)
  - Keycloak admin user with super_user role/group

Run:
  cd backend && python -m pytest tests/e2e/test_budget_search.py -v
"""

import httpx
import pytest


class TestBudgetList:
    """GET /api/budgets - basic listing."""

    def test_list_budgets_returns_paginated(self, admin_session: httpx.Client):
        resp = admin_session.get("/api/budgets")
        assert resp.status_code == 200
        data = resp.json()
        assert "budgets" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert data["page"] == 1
        assert isinstance(data["budgets"], list)

    def test_list_budgets_pagination(self, admin_session: httpx.Client):
        # First page
        resp1 = admin_session.get("/api/budgets", params={"page": 1, "page_size": 5})
        assert resp1.status_code == 200
        data1 = resp1.json()

        if data1["total"] > 5:
            # Second page
            resp2 = admin_session.get("/api/budgets", params={"page": 2, "page_size": 5})
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2["page"] == 2

            # Different budgets on different pages
            ids1 = {b["budget_id"] for b in data1["budgets"]}
            ids2 = {b["budget_id"] for b in data2["budgets"]}
            assert ids1.isdisjoint(ids2), "Pages should not overlap"

    def test_budget_has_linked_counts(self, admin_session: httpx.Client):
        resp = admin_session.get("/api/budgets", params={"page_size": 1})
        assert resp.status_code == 200
        budgets = resp.json()["budgets"]
        if budgets:
            b = budgets[0]
            assert "team_membership_count" in b
            assert "key_count" in b
            assert "org_count" in b
            assert isinstance(b["team_membership_count"], int)
            assert isinstance(b["key_count"], int)
            assert isinstance(b["org_count"], int)


class TestBudgetSearchById:
    """GET /api/budgets?search_id=... - ID search."""

    def test_search_by_full_id(self, admin_session: httpx.Client):
        # Get a real budget_id first
        resp = admin_session.get("/api/budgets", params={"page_size": 1})
        budgets = resp.json()["budgets"]
        if not budgets:
            pytest.skip("No budgets in DB")

        budget_id = budgets[0]["budget_id"]

        # Search by full ID
        resp2 = admin_session.get("/api/budgets", params={"search_id": budget_id})
        assert resp2.status_code == 200
        results = resp2.json()["budgets"]
        assert len(results) >= 1
        assert any(b["budget_id"] == budget_id for b in results)

    def test_search_by_partial_id(self, admin_session: httpx.Client):
        resp = admin_session.get("/api/budgets", params={"page_size": 1})
        budgets = resp.json()["budgets"]
        if not budgets:
            pytest.skip("No budgets in DB")

        budget_id = budgets[0]["budget_id"]
        partial = budget_id[:8]  # First 8 chars of UUID

        resp2 = admin_session.get("/api/budgets", params={"search_id": partial})
        assert resp2.status_code == 200
        results = resp2.json()["budgets"]
        assert len(results) >= 1
        assert all(partial.lower() in b["budget_id"].lower() for b in results)

    def test_search_by_nonexistent_id(self, admin_session: httpx.Client):
        resp = admin_session.get("/api/budgets", params={"search_id": "nonexistent-id-xyz"})
        assert resp.status_code == 200
        assert resp.json()["budgets"] == []
        assert resp.json()["total"] == 0


class TestBudgetSearchByAmount:
    """GET /api/budgets?search_amount=... - amount search."""

    def test_search_by_exact_amount(self, admin_session: httpx.Client):
        # Get a real budget with max_budget
        resp = admin_session.get("/api/budgets", params={"page_size": 50})
        budgets = resp.json()["budgets"]
        budget_with_amount = next((b for b in budgets if b["max_budget"] is not None), None)
        if not budget_with_amount:
            pytest.skip("No budgets with max_budget in DB")

        amount = budget_with_amount["max_budget"]

        # Search by exact amount
        resp2 = admin_session.get("/api/budgets", params={"search_amount": amount})
        assert resp2.status_code == 200
        results = resp2.json()["budgets"]
        assert len(results) >= 1
        assert all(b["max_budget"] == amount for b in results)

    def test_search_by_nonexistent_amount(self, admin_session: httpx.Client):
        resp = admin_session.get("/api/budgets", params={"search_amount": 9999999.99})
        assert resp.status_code == 200
        assert resp.json()["budgets"] == []


class TestBudgetSearchCombined:
    """GET /api/budgets?search_id=...&search_amount=... - combined search."""

    def test_combined_search_narrows_results(self, admin_session: httpx.Client):
        # Get a real budget
        resp = admin_session.get("/api/budgets", params={"page_size": 50})
        budgets = resp.json()["budgets"]
        budget_with_amount = next((b for b in budgets if b["max_budget"] is not None), None)
        if not budget_with_amount:
            pytest.skip("No budgets with max_budget in DB")

        budget_id = budget_with_amount["budget_id"]
        amount = budget_with_amount["max_budget"]

        # Combined search should find it
        resp2 = admin_session.get("/api/budgets", params={
            "search_id": budget_id[:8],
            "search_amount": amount,
        })
        assert resp2.status_code == 200
        results = resp2.json()["budgets"]
        assert len(results) >= 1
        assert any(b["budget_id"] == budget_id for b in results)

    def test_combined_search_mismatched_returns_empty(self, admin_session: httpx.Client):
        resp = admin_session.get("/api/budgets", params={"page_size": 1})
        budgets = resp.json()["budgets"]
        if not budgets:
            pytest.skip("No budgets in DB")

        # Real ID + impossible amount → no results
        resp2 = admin_session.get("/api/budgets", params={
            "search_id": budgets[0]["budget_id"][:8],
            "search_amount": 9999999.99,
        })
        assert resp2.status_code == 200
        assert resp2.json()["budgets"] == []


class TestBudgetDetails:
    """GET /api/budgets/{budget_id}/details - linked entities."""

    def test_get_budget_details(self, admin_session: httpx.Client):
        resp = admin_session.get("/api/budgets", params={"page_size": 1})
        budgets = resp.json()["budgets"]
        if not budgets:
            pytest.skip("No budgets in DB")

        budget_id = budgets[0]["budget_id"]

        resp2 = admin_session.get(f"/api/budgets/{budget_id}/details")
        assert resp2.status_code == 200
        data = resp2.json()
        assert "team_memberships" in data
        assert "keys" in data
        assert "organizations" in data
        assert isinstance(data["team_memberships"], list)
        assert isinstance(data["keys"], list)
        assert isinstance(data["organizations"], list)


class TestBudgetAuth:
    """Non-admin users should be rejected."""

    def test_unauthenticated_returns_401(self):
        client = httpx.Client(base_url="http://localhost:8002", timeout=10.0)
        resp = client.get("/api/budgets")
        assert resp.status_code == 401
