from bifrost.commands.solution import summarize_bundle


def test_summary_counts_and_warns():
    summary = summarize_bundle(
        python_files={f"m/{i}.py": "x" for i in range(613)},
        apps=[],
        vendored_count=613,
    )
    assert summary.file_count == 613
    assert summary.warn is True
    assert "vendored" in summary.message.lower()


def test_summary_no_warn_counts_apps():
    summary = summarize_bundle(
        python_files={"a.py": "xx", "b.py": "yyy"},
        apps=[{"src_files": {"index.tsx": "code"}, "bin_files": {"logo.png": "AAAA"}}],
        vendored_count=0,
    )
    # 2 python + 1 src + 1 bin = 4 files
    assert summary.file_count == 4
    assert summary.warn is False
    assert "Bundle:" in summary.message
