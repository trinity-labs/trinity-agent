"""Reproducible offline retrieval benchmark; no credentials or model requests."""
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trinity_agent.cache import CACHE
from trinity_agent.request import analyze_request, retrieve_context


def main():
    measurements = []
    with tempfile.TemporaryDirectory(prefix='trinity-retrieval-benchmark-') as directory:
        allowed = Path(directory)
        project = allowed / 'machine' / 'project'
        project.mkdir(parents=True)
        (project / 'package.json').write_text('{}')
        for index in range(240):
            (project / f'unit_{index:03}.py').write_text(
                'def route_recovery():\n    return "nebula_recovery handling"\n'
                if index == 220 else 'def unrelated():\n    return "ordinary data"\n')
        (project / 'long.py').write_text(
            '# ordinary filler\n' * 6000
            + 'def nebula_recovery():\n    return "late_evidence"\n')
        analysis = analyze_request('Corrige notre nebula_recovery')
        for profile, files, characters, mode in [('small', 120, 65536, 'auto'), ('local', 480, 524288, 'auto'), ('automatic', 'auto', 'auto', 'auto'), ('maximum', 'auto', 'auto', 'max')]:
            with patch.dict(os.environ, {'TRINITY_AGENT_PREFLIGHT_MAX_FILES': str(files),
                                         'TRINITY_AGENT_PREFLIGHT_FILE_CHARS': str(characters),
                                         'TRINITY_AGENT_PERFORMANCE_MODE': mode}):
                CACHE.clear()
                CACHE.refresh_limits()
                timings = []
                for run in range(6):
                    hits = CACHE.hits
                    started = time.perf_counter()
                    result = retrieve_context(analysis, [], project, allowed)
                    elapsed = (time.perf_counter() - started) * 1000
                    timings.append(elapsed)
                    measurements.append({
                        'profile': profile, 'run': 'cold' if run == 0 else 'warm',
                        'ms': round(elapsed, 2), 'files_scanned': result['files_scanned'],
                        'recall': sum(marker in result['context'] for marker in ('handling', 'late_evidence')),
                        'expected': 2, 'context_chars': len(result['context']),
                        'partial': result['partial'], 'cache_hits': CACHE.hits - hits,
                        'cache_estimated_bytes': CACHE.bytes, **result['retrieval'],
                    })
                measurements.append({'profile': profile, 'warm_median_ms': round(statistics.median(timings[1:]), 2)})
    print(json.dumps(measurements, ensure_ascii=False, indent=2))
    # Resource-limited hosts may intentionally scan less. Check full recall only
    # when the effective profile actually covers this corpus and both locations.
    for row in measurements:
        if 'run' not in row:
            continue
        assert row['context_chars'] <= 24000
        if (not row['partial'] and row['scanned_files'] == row['candidate_files']
                and row['resources']['file_chars'] >= 108100):
            assert row['recall'] == 2


if __name__ == '__main__':
    main()
