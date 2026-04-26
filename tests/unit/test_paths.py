import importlib


def test_paths_respects_autoteam_data_dir_env(monkeypatch, tmp_path):
    import autoteam.paths as paths

    original_dir = paths.DATA_DIR

    monkeypatch.setenv("AUTOTEAM_DATA_DIR", str(tmp_path))
    reloaded = importlib.reload(paths)

    assert reloaded.DATA_DIR == tmp_path
    assert reloaded.data_path("state.json") == tmp_path / "state.json"

    monkeypatch.delenv("AUTOTEAM_DATA_DIR", raising=False)
    restored = importlib.reload(paths)
    assert restored.DATA_DIR == original_dir

