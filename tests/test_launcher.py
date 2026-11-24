"""cwdc stop: kills the `cwdc serve` processes and reports what happened (subprocess mocked)."""
from cwdc import launcher


def _fake(monkeypatch, pids, up_after_kill):
    killed = []
    state = {"up": bool(pids)}
    monkeypatch.setattr(launcher, "server_pids", lambda: list(pids))
    monkeypatch.setattr(launcher, "server_up", lambda timeout=1.0: state["up"])
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)

    def run(cmd, **kw):
        killed.append(cmd)
        state["up"] = up_after_kill
    monkeypatch.setattr(launcher.subprocess, "run", run)
    return killed


def test_stop_kills_server(monkeypatch):
    killed = _fake(monkeypatch, [4242], up_after_kill=False)
    assert launcher.stop() == ["web UI stopped"]
    assert killed == [["taskkill", "/PID", "4242", "/F"]]


def test_stop_when_not_running(monkeypatch):
    killed = _fake(monkeypatch, [], up_after_kill=False)
    assert launcher.stop() == ["web UI was not running"]
    assert killed == []


def test_stop_reports_survivor(monkeypatch):
    _fake(monkeypatch, [7], up_after_kill=True)
    assert "still answering" in launcher.stop()[0]


def test_stop_shortcut_listed():
    names = {name: args for name, args, *_ in launcher.SHORTCUTS}
    assert names["Stop ColdWarDynamic"] == "-m cwdc stop --popup"


def test_cli_has_stop():
    from cwdc import cli
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["stop", "--help"])
