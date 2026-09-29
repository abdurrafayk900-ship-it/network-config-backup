import pytest

from netbackup.inventory import InventoryError, load_inventory


def write(tmp_path, text):
    p = tmp_path / "inv.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults_and_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("NET_USER", "admin")
    monkeypatch.setenv("NET_PASS", "s3cret")
    inv = write(
        tmp_path,
        """
defaults:
  username: ${NET_USER}
  password: ${NET_PASS}
  device_type: cisco_ios
devices:
  - name: R1
    host: 10.0.0.1
    groups: [core]
  - name: SW1
    host: 10.0.0.11
    groups: [access]
""",
    )
    devices = load_inventory(inv)
    assert [d.name for d in devices] == ["R1", "SW1"]
    assert devices[0].username == "admin"
    assert devices[0].password == "s3cret"
    assert devices[0].backup_command == "show running-config"
    assert "s3cret" not in repr(devices[0])


def test_group_filter(tmp_path):
    inv = write(
        tmp_path,
        """
defaults: {username: u, password: p, device_type: cisco_ios}
devices:
  - {name: R1, host: 1.1.1.1, groups: [core]}
  - {name: SW1, host: 1.1.1.2, groups: [access]}
""",
    )
    assert [d.name for d in load_inventory(inv, group="core")] == ["R1"]


def test_missing_env_var_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    inv = write(
        tmp_path,
        """
devices:
  - {name: R1, host: 1.1.1.1, device_type: cisco_ios, username: u, password: "${DOES_NOT_EXIST}"}
""",
    )
    with pytest.raises(InventoryError):
        load_inventory(inv)


def test_unsupported_platform(tmp_path):
    inv = write(
        tmp_path,
        """
devices:
  - {name: X, host: 1.1.1.1, device_type: toaster_os, username: u, password: p}
""",
    )
    with pytest.raises(InventoryError, match="unsupported"):
        load_inventory(inv)


def test_duplicate_names(tmp_path):
    inv = write(
        tmp_path,
        """
defaults: {username: u, password: p, device_type: cisco_ios}
devices:
  - {name: R1, host: 1.1.1.1}
  - {name: R1, host: 1.1.1.2}
""",
    )
    with pytest.raises(InventoryError, match="Duplicate"):
        load_inventory(inv)
