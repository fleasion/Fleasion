import Fleasion.utils.logging as fleasion_logging


def test_log_file_rotates_at_one_mib_before_append(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    log_file = logs_dir / "fleasion.log"
    logs_dir.mkdir()
    log_file.write_bytes(b"x" * fleasion_logging.MAX_LOG_FILE_BYTES)

    monkeypatch.setattr(fleasion_logging, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(fleasion_logging, "LOG_FILE", log_file)

    buffer = fleasion_logging.LogBuffer()
    buffer.log("Test", "after rotation")

    rotated = logs_dir / "fleasion.log.1"
    assert rotated.exists()
    assert rotated.stat().st_size == fleasion_logging.MAX_LOG_FILE_BYTES
    assert log_file.stat().st_size < fleasion_logging.MAX_LOG_FILE_BYTES
    assert "after rotation" in log_file.read_text(encoding="utf-8")
