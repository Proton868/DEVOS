import asyncio
from pathlib import Path


def test_chaos_drills_all_pass():
    from chaos.drills import run_all_drills
    report = asyncio.run(run_all_drills())
    assert report.failed == 0, [r.name for r in report.results if not r.passed]
    assert report.passed >= 10


def test_report_written(tmp_path):
    from chaos.drills import run_all_drills
    report = asyncio.run(run_all_drills())
    path = tmp_path / "r.json"
    report.write(path)
    assert path.exists()
    assert '"gate": "PASS"' in path.read_text()
