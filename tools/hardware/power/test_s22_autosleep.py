"""Host tests for s22-autosleep (no phone needed): python3 -m pytest -q test_s22_autosleep.py"""
import importlib.util, os, subprocess, time
from pathlib import Path

spec = importlib.util.spec_from_file_location("autosleep", Path(__file__).with_name("s22-autosleep.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)

DMESG = """[1.0] noise
[2.0] s22-autosleep-1-2
[2.1] EXYNOS-PM:: MIF down. cur_count: 5, acc_count: 5
[2.2] PM: Resume caused by IRQ 515, wlan_pci_wake_irq
[2.3] Resume caused by IRQ 401, cp_mbox_irq_0
[2.4] Resume caused by IRQ 515, wlan_pci_wake_irq
"""


def test_wake_reason_after_mark(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: type("R", (), {"stdout": DMESG})())
    assert A.wake_reason("s22-autosleep-1-2") == ["IRQ 515, wlan_pci_wake_irq", "IRQ 401, cp_mbox_irq_0"]


def test_wake_reason_abort(monkeypatch):
    out = "[1] m\n[2] cnss_pci 0000:01:00.0: PM: failed to suspend: error -11\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: type("R", (), {"stdout": out})())
    assert A.wake_reason("m")[0].startswith("ABORT")


def test_inhibit_files_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "INHIBIT_DIR", str(tmp_path))
    (tmp_path / "forever").write_text("")
    old = tmp_path / "expired"
    old.write_text("5")
    os.utime(old, (time.time() - 60, time.time() - 60))
    (tmp_path / "fresh").write_text("600")
    assert sorted(A.inhibit_files()) == ["forever", "fresh"]
    assert not old.exists()


def test_inhibitors_usb_and_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "FLAG", str(tmp_path / "disabled"))
    monkeypatch.setattr(A, "INHIBIT_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(A, "ssh_sessions", lambda: 0)
    monkeypatch.setattr(A, "pcm_running", lambda: None)
    monkeypatch.setattr(A, "active_wakeup_sources", lambda: ["dwc3-otg", "usb_notify"])
    monkeypatch.setattr(A, "calls_active", lambda: False)
    monkeypatch.setattr(A, "usb_host", lambda: True)
    cfg = dict(A.CONFIG)
    assert A.inhibitors(cfg, 1.0) == ["usb-host"]
    cfg["suspend_on_usb"] = True
    assert A.inhibitors(cfg, 1.0) == []            # USB wakeup sources are ignored
    assert A.inhibitors(cfg, 90.0) == ["busy:90%"]
    (tmp_path / "disabled").write_text("x")
    assert A.inhibitors(cfg, 1.0)[0] == "disabled"


def test_handshake_skipped_while_usb_source_active(monkeypatch):
    monkeypatch.setattr(A, "active_wakeup_sources", lambda: ["dwc3-otg"])
    assert A.wakeup_count_handshake(dict(A.CONFIG)) is None
