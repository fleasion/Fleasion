from Fleasion.proxy import master as proxy_master


def test_windows_hosts_writer_creates_missing_immediate_parent(tmp_path, monkeypatch):
    hosts_root = tmp_path / "SystemRoot"
    drivers_dir = hosts_root / "System32" / "drivers"
    drivers_dir.mkdir(parents=True)
    hosts_file = drivers_dir / "etc" / "hosts"

    monkeypatch.setattr(proxy_master, "HOSTS_FILE", hosts_file)

    proxy_master._write_hosts_file("127.0.0.1 assetdelivery.roblox.com\n")

    assert hosts_file.parent.is_dir()
    assert hosts_file.read_text(encoding="utf-8") == "127.0.0.1 assetdelivery.roblox.com\n"
