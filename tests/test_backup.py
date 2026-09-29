from netbackup.backup import normalize_config, run_backups
from netbackup.inventory import Device

RAW = """Building configuration...

Current configuration : 1234 bytes
! Last configuration change at 10:00:00 UTC Mon Jan 1 2026
!
hostname R1
ntp clock-period 17179860
interface Gi0/0
 ip address 10.0.0.1 255.255.255.0
end
"""


def make_device(name="R1"):
    return Device(name=name, host="10.0.0.1", device_type="cisco_ios", username="u", password="p")


def test_normalize_strips_volatile_lines():
    out = normalize_config(RAW)
    assert "Building configuration" not in out
    assert "Current configuration" not in out
    assert "Last configuration change" not in out
    assert "ntp clock-period" not in out
    assert out.startswith("!\nhostname R1")


def test_volatile_only_change_is_unchanged(tmp_path):
    dev = make_device()
    run_backups([dev], tmp_path, fetcher=lambda d: RAW)
    changed_ts = RAW.replace("10:00:00", "11:30:00").replace("1234", "1299")
    results = run_backups([dev], tmp_path, fetcher=lambda d: changed_ts)
    assert results[0].status == "unchanged"


def test_new_then_changed_with_diff(tmp_path):
    dev = make_device()
    first = run_backups([dev], tmp_path, fetcher=lambda d: RAW)
    assert first[0].status == "new"

    modified = RAW.replace("10.0.0.1 255.255.255.0", "10.0.0.2 255.255.255.0")
    second = run_backups([dev], tmp_path, fetcher=lambda d: modified)
    assert second[0].status == "changed"
    assert "- ip address 10.0.0.1" in second[0].diff
    assert "+ ip address 10.0.0.2" in second[0].diff
    assert (tmp_path / "R1" / "latest.cfg").read_text().count("10.0.0.2") == 1


def test_one_failure_does_not_stop_others(tmp_path):
    def fetcher(d):
        if d.name == "BAD":
            raise TimeoutError("SSH timed out")
        return RAW

    results = run_backups([make_device("BAD"), make_device("R1")], tmp_path, fetcher=fetcher)
    by_name = {r.device: r for r in results}
    assert by_name["BAD"].status == "failed"
    assert "timed out" in by_name["BAD"].error
    assert by_name["R1"].status == "new"
