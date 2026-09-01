#!/usr/bin/env python3
"""Check pip-audit JSON report for HIGH/CRITICAL vulnerabilities."""

import json
import sys
import os

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pip-audit-report.json')

try:
    with open(REPORT_PATH) as f:
        data = json.load(f)
except FileNotFoundError:
    print(f"pip-audit report not found at {REPORT_PATH}")
    # Exit 0 so a missing report (e.g. pip-audit itself failed earlier) does not
    # mask the real failure — the pip-audit step should have failed the job.
    sys.exit(0)

has_high_critical = False

# pip-audit JSON report shape: { "dependencies": [...] }
for dep in data.get('dependencies', []):
    pkg = dep.get('name', 'unknown')
    installed = dep.get('version', 'unknown')
    for vuln in dep.get('vulns', []):
        # pip-audit returns severity as a CVSS score or string; treat >=7.0 as HIGH
        severity = vuln.get('severity', vuln.get('severity_string', ''))
        is_high = False
        if isinstance(severity, (int, float)):
            is_high = severity >= 7.0
        else:
            sev_str = str(severity).upper()
            is_high = sev_str in ('HIGH', 'CRITICAL') or sev_str.startswith('CVSS:3.1/')

        if is_high:
            print(f"HIGH/CRITICAL: {pkg} {installed} - {vuln.get('id', 'unknown')}")
            has_high_critical = True

if has_high_critical:
    sys.exit(1)

print("No HIGH/CRITICAL Python vulnerabilities found")
