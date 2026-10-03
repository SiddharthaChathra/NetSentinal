"""Starting the agent automatically at sign-in.

"Set it to start automatically" on the Devices page used to do nothing: the
link set a flag only the empty-account panel read, and the instructions it
would have shown were for the source checkout - the downloaded binary had no
way to start itself at all. The agent now installs this itself
(--install-autostart, or a prompt on first run).

These are the unit tests. The real thing was exercised end to end with
PyInstaller builds: on Windows (Run value, no window at sign-in, survives the
download being deleted, uninstall stops it) and on Linux under systemd
(service enabled and active, restarted after being killed).
"""
import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "agent"))
import agent  # noqa: E402
import autostart  # noqa: E402


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "data_dir", lambda: tmp_path)
    return tmp_path


class TestLaunchCommand:
    def test_binary_runs_its_stable_copy_in_the_background(self, data, monkeypatch, tmp_path):
        downloaded = tmp_path / "Downloads" / "netsentinel-agent-windows.exe"
        downloaded.parent.mkdir()
        downloaded.write_bytes(b"binary v1")
        monkeypatch.setattr(autostart, "is_frozen", lambda: True)
        monkeypatch.setattr(sys, "executable", str(downloaded))
        cmd = autostart.launch_command()
        assert cmd[1:] == ["--start", "--background"]
        assert pathlib.Path(cmd[0]) == data / "bin" / downloaded.name
        assert pathlib.Path(cmd[0]).read_bytes() == b"binary v1"

    def test_a_new_download_replaces_the_stable_copy(self, data, monkeypatch, tmp_path):
        downloaded = tmp_path / "netsentinel-agent-linux"
        downloaded.write_bytes(b"v2")
        (data / "bin").mkdir()
        (data / "bin" / downloaded.name).write_bytes(b"v1")
        monkeypatch.setattr(autostart, "is_frozen", lambda: True)
        monkeypatch.setattr(sys, "executable", str(downloaded))
        assert pathlib.Path(autostart.stable_executable()).read_bytes() == b"v2"

    def test_a_running_stable_copy_is_kept_rather_than_failing(self, data, monkeypatch, tmp_path):
        """Windows will not overwrite a running .exe - the one started at sign-in."""
        downloaded = tmp_path / "netsentinel-agent-windows.exe"
        downloaded.write_bytes(b"v2")
        (data / "bin").mkdir()
        (data / "bin" / downloaded.name).write_bytes(b"v1")
        monkeypatch.setattr(autostart, "is_frozen", lambda: True)
        monkeypatch.setattr(sys, "executable", str(downloaded))
        with patch.object(autostart.shutil, "copy2", side_effect=PermissionError("in use")):
            assert autostart.stable_executable() == data / "bin" / downloaded.name

    def test_source_checkout_runs_agent_py(self, monkeypatch):
        monkeypatch.setattr(autostart, "is_frozen", lambda: False)
        cmd = autostart.launch_command()
        assert cmd[1].endswith("agent.py") and cmd[2:] == ["--start", "--background"]


class TestWindows:
    def test_binary_is_launched_through_a_headless_console(self, monkeypatch):
        monkeypatch.setattr(autostart, "launch_command",
                            lambda: [r"C:\Users\A B\AppData\Local\NetSentinel\bin\netsentinel-agent-windows.exe",
                                     "--start", "--background"])
        value = autostart._windows_value()
        assert "conhost.exe --headless" in value
        # A path with spaces must be quoted, or Windows runs "C:\Users\A".
        assert r'"C:\Users\A B\AppData\Local\NetSentinel\bin\netsentinel-agent-windows.exe"' in value
        assert value.endswith("--start --background")

    def test_pythonw_needs_no_console_at_all(self, monkeypatch):
        monkeypatch.setattr(autostart, "launch_command",
                            lambda: [r"C:\venv\Scripts\pythonw.exe", r"C:\repo\agent\agent.py", "--start", "--background"])
        assert "conhost" not in autostart._windows_value()

    @pytest.mark.skipif(sys.platform != "win32", reason="registry")
    def test_install_and_uninstall_write_only_our_value(self, monkeypatch):
        """Against a throwaway key, so a developer's real Run key is untouched."""
        import winreg
        test_key = r"Software\NetSentinelTest\Run"
        monkeypatch.setattr(autostart, "RUN_KEY", test_key)
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, test_key)
        monkeypatch.setattr(autostart, "_windows_value", lambda: "agent.exe --start --background")
        try:
            assert autostart._win_install()[0]
            assert autostart._win_registered_command() == "agent.exe --start --background"
            assert autostart._win_uninstall()[0]
            assert autostart._win_registered_command() == ""
            assert autostart._win_uninstall() == (True, "Automatic start was not set up.")
        finally:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, test_key)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\NetSentinelTest")


class TestLinux:
    def test_unit_restarts_on_failure_but_not_on_a_clean_exit(self, monkeypatch):
        """A second copy exits cleanly when one is already running; that must
        not turn into a restart loop."""
        monkeypatch.setattr(autostart, "launch_command",
                            lambda: ["/home/u/.local/share/netsentinel/bin/netsentinel-agent-linux", "--start", "--background"])
        unit = autostart._unit_text()
        assert "ExecStart=/home/u/.local/share/netsentinel/bin/netsentinel-agent-linux --start --background" in unit
        assert "Restart=on-failure" in unit and "WantedBy=default.target" in unit

    def test_no_systemd_says_what_to_do_instead(self, monkeypatch):
        monkeypatch.setattr(autostart.shutil, "which", lambda name: None)
        monkeypatch.setattr(autostart, "launch_command", lambda: ["/x/agent", "--start", "--background"])
        ok, message = autostart._linux_install()
        assert not ok and "@reboot /x/agent --start --background" in message

    def test_install_enables_and_starts_the_service(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr(autostart.shutil, "which", lambda name: "/usr/bin/systemctl")
        monkeypatch.setattr(autostart, "launch_command", lambda: ["/x/agent", "--start", "--background"])
        calls = []
        monkeypatch.setattr(autostart, "_systemctl",
                            lambda *a: calls.append(a) or MagicMock(returncode=0, stdout="", stderr=""))
        ok, _ = autostart._linux_install()
        assert ok and ("enable", "--now", "netsentinel-agent") in calls
        assert (tmp_path / "systemd" / "user" / "netsentinel-agent.service").exists()


class TestSingleInstance:
    @pytest.fixture
    def lock(self, tmp_path, monkeypatch):
        f = tmp_path / "agent.pid"
        monkeypatch.setattr(agent, "LOCK_FILE", f)
        return f

    def _fake_process(self, cmdline):
        proc = MagicMock()
        proc.cmdline.return_value = cmdline
        return proc

    def test_no_lock_means_not_running(self, lock):
        assert agent._running_instance() is None

    def test_our_own_pid_is_not_another_instance(self, lock):
        lock.write_text(str(os.getpid()))
        assert agent._running_instance() is None

    def test_a_live_agent_is_detected(self, lock):
        lock.write_text("4242")
        with patch("psutil.Process", return_value=self._fake_process(["netsentinel-agent-windows.exe", "--start"])):
            assert agent._running_instance() == 4242

    def test_a_recycled_pid_is_not_mistaken_for_the_agent(self, lock):
        lock.write_text("4242")
        with patch("psutil.Process", return_value=self._fake_process(["chrome.exe"])):
            assert agent._running_instance() is None

    def test_a_dead_pid_is_not_running(self, lock):
        lock.write_text("4242")
        with patch("psutil.Process", side_effect=Exception("no such process")):
            assert agent._running_instance() is None

    def test_start_refuses_when_another_copy_runs(self, lock, monkeypatch):
        monkeypatch.setattr(agent, "_running_instance", lambda: 4242)
        monkeypatch.setattr(agent, "run_once", lambda: pytest.fail("must not report"))
        assert agent.start_agent() is False


class TestTheLoopSurvivesErrors:
    def test_a_failed_cycle_does_not_end_the_agent(self, tmp_path, monkeypatch):
        """An agent that quietly dies on one bad cycle is exactly what auto-start
        exists to prevent."""
        monkeypatch.setattr(agent, "LOCK_FILE", tmp_path / "agent.pid")
        cycles = {"n": 0}

        def flaky():
            cycles["n"] += 1
            if cycles["n"] == 1:
                raise RuntimeError("network blip")
            if cycles["n"] == 3:
                raise KeyboardInterrupt  # end the test
        monkeypatch.setattr(agent, "run_once", flaky)
        monkeypatch.setattr(agent.time, "sleep", lambda s: None)
        with pytest.raises(KeyboardInterrupt):
            agent.start_agent()
        assert cycles["n"] == 3


class TestFirstRunPrompt:
    def _tty(self, monkeypatch, answer):
        stdin = MagicMock()
        stdin.isatty.return_value = True
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr("builtins.input", lambda prompt="": answer)

    def test_yes_installs_and_hands_over_to_the_background(self, monkeypatch):
        self._tty(monkeypatch, "")  # Enter = yes
        monkeypatch.setattr(autostart, "is_installed", lambda: False)
        monkeypatch.setattr(autostart, "install", lambda: (True, "installed"))
        started = []
        monkeypatch.setattr(autostart, "start_in_background", lambda: started.append(1))
        assert agent._offer_autostart() is True
        assert started == ([1] if sys.platform == "win32" else [])

    def test_no_keeps_running_in_this_window(self, monkeypatch):
        self._tty(monkeypatch, "n")
        monkeypatch.setattr(autostart, "is_installed", lambda: False)
        monkeypatch.setattr(autostart, "install", lambda: pytest.fail("must not install"))
        assert agent._offer_autostart() is False

    def test_not_asked_without_a_terminal_or_when_already_set_up(self, monkeypatch):
        monkeypatch.setattr(autostart, "is_installed", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("must not ask"))
        assert agent._offer_autostart() is False
