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


def test_windows_hosts_entries_include_ipv4_and_ipv6_loopback(tmp_path, monkeypatch):
    hosts_file = tmp_path / "hosts"
    hosts_file.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    logs = []

    monkeypatch.setattr(proxy_master, "HOSTS_FILE", hosts_file)
    monkeypatch.setattr(proxy_master, "IS_WINDOWS", True)
    monkeypatch.setattr(proxy_master, "IS_MACOS", False)
    monkeypatch.setattr(proxy_master, "_HOSTS_ACTIVE_LOOPBACK_IPS", None)
    monkeypatch.setattr(proxy_master.log_buffer, "log", lambda category, message: logs.append((category, message)))

    assert proxy_master._add_hosts_entries({"assetdelivery.roblox.com"})
    content = hosts_file.read_text(encoding="utf-8")

    assert "127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry" in content
    assert "::1 assetdelivery.roblox.com # Fleasion proxy entry" in content
    assert proxy_master._verify_hosts_entries({"assetdelivery.roblox.com"})


def test_hosts_cleanup_removes_ipv4_and_ipv6_loopback_entries(tmp_path, monkeypatch):
    hosts_file = tmp_path / "hosts"
    hosts_file.write_text(
        "127.0.0.1 localhost\n"
        "127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry\n"
        "::1 assetdelivery.roblox.com # Fleasion proxy entry\n"
        "::1 gamejoin.roblox.com\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(proxy_master, "HOSTS_FILE", hosts_file)
    monkeypatch.setattr(proxy_master, "IS_MACOS", False)

    assert proxy_master._remove_hosts_entries({"assetdelivery.roblox.com", "gamejoin.roblox.com"})
    content = hosts_file.read_text(encoding="utf-8")

    assert "assetdelivery.roblox.com" not in content
    assert "gamejoin.roblox.com" not in content
    assert "127.0.0.1 localhost" in content
