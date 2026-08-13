from fleasion.utils.microprofiler import start_microprofiler


def test_microprofiler_disabled_is_inert(monkeypatch):
    monkeypatch.setattr('fleasion.utils.microprofiler.sys.platform', 'win32')

    assert start_microprofiler(enabled=False) is None


def test_microprofiler_is_inert_outside_windows(monkeypatch):
    monkeypatch.setattr('fleasion.utils.microprofiler.sys.platform', 'linux')

    assert start_microprofiler(enabled=True) is None
