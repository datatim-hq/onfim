from datetime import datetime, timedelta, timezone

from mem.policy import NO_ACCESS, Facts, Grant, resolve

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
ALL = {"read", "write", "review", "forget", "manage"}


def facts(grants, groups=(), clearance=3, tenant="acme", ns_tenant="acme", exists=True, active=True):
    return Facts(exists, ns_tenant, tenant, "ivanov", clearance, active, frozenset(groups), tuple(grants))


def test_direct_grant_and_clearance_cap():
    """A personal contributor grant gives read and write, and the classification level is cut by the person's clearance."""
    f = facts([Grant("principal", "ivanov", "contributor", 3)], clearance=1)
    assert resolve(f, NOW, ["*"], ALL, "acme.pmo", False) == (frozenset({"read", "write"}), 1)


def test_group_grant_and_best_role():
    """Group and organization grants add up: the best role and the highest classification level win."""
    f = facts([Grant("group", "acme/pmo", "reader", 1), Grant("tenant", "acme", "contributor", 0)], groups={"acme/pmo"})
    rights, mc = resolve(f, NOW, ["*"], ALL, "acme.pmo", False)
    assert rights == {"read", "write"} and mc == 1


def test_ceiling_limits_rights_and_namespaces():
    """The token ceiling cuts rights even for the owner, and a namespace outside the ceiling is closed entirely."""
    f = facts([Grant("principal", "ivanov", "owner", 3)])
    assert resolve(f, NOW, ["acme.pmo"], {"read"}, "acme.pmo", False) == (frozenset({"read"}), 3)
    assert resolve(f, NOW, ["acme.other"], ALL, "acme.pmo", False) == NO_ACCESS


def test_tenant_mismatch_expired_inactive_missing():
    """No access if the namespace belongs to another organization, the grant expired, the person is inactive or the namespace is missing."""
    g = [Grant("principal", "ivanov", "owner", 3)]
    assert resolve(facts(g, ns_tenant="demo"), NOW, ["*"], ALL, "x", False) == NO_ACCESS
    assert resolve(facts([Grant("principal", "ivanov", "owner", 3, NOW - timedelta(seconds=1))]),
                   NOW, ["*"], ALL, "x", False) == NO_ACCESS
    assert resolve(facts(g, active=False), NOW, ["*"], ALL, "x", False) == NO_ACCESS
    assert resolve(facts(g, exists=False), NOW, ["*"], ALL, "x", False) == NO_ACCESS


def test_local_admin_bypass():
    """The local administrator gets the admin right and level 3 without grants and in any organization."""
    rights, mc = resolve(facts([], ns_tenant="other"), NOW, ["*"], {"admin"}, "x", True)
    assert "admin" in rights and mc == 3
