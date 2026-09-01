#!/usr/bin/env python3
"""Check npm audit report for HIGH/CRITICAL vulnerabilities.

Supports both the legacy `npm audit --json` format (where counts live under
`metadata.vulnerabilities`) and the newer JSON format produced by recent
npm versions (where findings are under `vulnerabilities` and totals are
computed from `metadata.vulnerabilities` may be absent).
"""

import json
import sys
import os

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend-src', 'npm-audit-report.json')

try:
    with open(REPORT_PATH) as f:
        data = json.load(f)
except FileNotFoundError:
    print(f"npm audit report not found at {REPORT_PATH}")
    sys.exit(0)

metadata = data.get('metadata', {})
legacy = metadata.get('vulnerabilities', {})
high = legacy.get('high', 0)
critical = legacy.get('critical', 0)

# Newer npm versions expose per-advisory severity data under data['vulnerabilities']
if not high and not critical:
    for vuln in data.get('vulnerabilities', {}).values():
        sev = vuln.get('severity', '').upper()
        if sev == 'HIGH':
            high += 1
        elif sev == 'CRITICAL':
            critical += 1
        # Some versions nest severity inside 'via' entries
        for via in vuln.get('via', []):
            if isinstance(via, dict):
                sev_via = via.get('severity', '').upper()
                if sev_via == 'HIGH':
                    high += 1
                elif sev_via == 'CRITICAL':
                    critical += 1

total = high + critical

if total > 0:
    print(f"Found {total} HIGH/CRITICAL vulnerabilities in Node dependencies (high={high}, critical={critical})")
    sys.exit(1)

print("No HIGH/CRITICAL Node vulnerabilities found")
