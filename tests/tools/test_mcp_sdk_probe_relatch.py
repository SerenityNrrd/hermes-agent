"""A boot-race-lost MCP SDK probe must not latch its verdict.

``_ensure_mcp_sdk`` freezes availability flags after the first probe. On a multiplex
gateway boot (many profiles importing concurrently), the probe can transiently see an
import that is not ready; freezing that parked every HTTP MCP server for the process
lifetime behind a misleading "upgrade the mcp package" error. The contract pinned here:
a failed probe leaves the attempt unlatched so the next call re-probes, and a fully
successful probe still latches (the no-op-once-attempted behavior tests rely on).
"""

import pytest

import tools.mcp_tool as mt


@pytest.fixture
def probe_state():
    """Isolated flag state with a recording, scripted importer.

    The importer answers from a script of per-module results and mirrors the real
    binding behaviour for the core names so the guard under test is exercised."""
    state = {"calls": 0}
    real_import = mt._import_sdk_names
    mt._MCP_SDK_IMPORT_ATTEMPTED = False
    mt._MCP_AVAILABLE = True
    mt._MCP_HTTP_AVAILABLE = mt._MCP_NEW_HTTP = mt._MCP_LEGACY_HTTP = False
    saved_session = mt.ClientSession
    mt.ClientSession = None
    yield state
    mt._import_sdk_names = real_import
    mt.ClientSession = saved_session
    mt._MCP_SDK_IMPORT_ATTEMPTED = False
    mt._MCP_HTTP_AVAILABLE = mt._MCP_NEW_HTTP = mt._MCP_LEGACY_HTTP = False


def _importer(script):
    def fake(module, names, missing_msg=None):
        result = script.get(module, True)
        if result:
            for name in names:
                setattr(mt, name if name != "ClientSession" else "ClientSession",
                        object() if name == "ClientSession" else object())
        return result
    return fake


def test_http_probe_failure_does_not_latch(monkeypatch, probe_state):
    """First probe loses the boot race (HTTP names absent), second sees them."""
    script = {"mcp.client.streamable_http": False}
    monkeypatch.setattr(mt, "_import_sdk_names", _importer(script))
    assert mt._ensure_mcp_sdk() is True
    assert mt._MCP_AVAILABLE and not mt._MCP_HTTP_AVAILABLE
    assert mt._MCP_SDK_IMPORT_ATTEMPTED is False, "failed HTTP probe must not latch"

    script["mcp.client.streamable_http"] = True
    assert mt._ensure_mcp_sdk() is True
    assert mt._MCP_HTTP_AVAILABLE, "later probe must be honored"
    assert mt._MCP_SDK_IMPORT_ATTEMPTED is True


def test_core_probe_failure_does_not_latch(monkeypatch, probe_state):
    """A transient core-import failure must not freeze the SDK as unavailable."""
    script = {"mcp": False}
    monkeypatch.setattr(mt, "_import_sdk_names", _importer(script))
    assert mt._ensure_mcp_sdk() is True  # _MCP_AVAILABLE stays True (SDK installed)
    assert mt.ClientSession is None
    assert mt._MCP_SDK_IMPORT_ATTEMPTED is False, "failed core probe must not latch"

    script["mcp"] = True
    script["mcp.client.streamable_http"] = True
    mt._ensure_mcp_sdk()
    assert mt.ClientSession is not None
    assert mt._MCP_HTTP_AVAILABLE and mt._MCP_SDK_IMPORT_ATTEMPTED


def test_frozen_unavailable_flag_rechecks(monkeypatch, probe_state):
    """A module-level find_spec that lost the boot race must not veto forever."""
    import importlib.util

    mt._MCP_AVAILABLE = False  # frozen False from import time
    real_find_spec = importlib.util.find_spec
    calls = {"n": 0}

    def flaky_find_spec(name, *a, **kw):
        if name != "mcp":
            return real_find_spec(name, *a, **kw)
        calls["n"] += 1
        return None if calls["n"] == 1 else True

    monkeypatch.setattr(importlib.util, "find_spec", flaky_find_spec)
    monkeypatch.setattr(mt, "_import_sdk_names", _importer({}))
    assert mt._ensure_mcp_sdk() is False  # first call: still marked unavailable
    assert mt._MCP_AVAILABLE is False
    assert mt._ensure_mcp_sdk() is True  # second call re-checked and recovered
    assert mt._MCP_AVAILABLE and mt.ClientSession is not None


def test_successful_probe_latches_and_no_ops(monkeypatch, probe_state):
    """A fully successful probe keeps the no-op-once-attempted contract."""
    monkeypatch.setattr(mt, "_import_sdk_names", _importer({}))
    assert mt._ensure_mcp_sdk() is True
    assert mt._MCP_SDK_IMPORT_ATTEMPTED and mt._MCP_HTTP_AVAILABLE
    bound = mt.ClientSession
    probe_state["calls"] += 1
    assert mt._ensure_mcp_sdk() is True
    assert mt.ClientSession is bound  # second call did not re-run the probe body
