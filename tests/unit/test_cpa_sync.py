from autoteam import cpa_sync


def test_sync_to_cpa_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(cpa_sync, "CPA_SYNC_ENABLED", False)
    monkeypatch.setattr(cpa_sync, "list_cpa_files", lambda: (_ for _ in ()).throw(AssertionError("should not list CPA files")))

    result = cpa_sync.sync_to_cpa()

    assert result == {"skipped": True, "reason": "disabled"}
