"""Check every VEYRA rule against the real Wazuh rule engine (A6).

    uv run python tools/wazuh_logtest.py            # all cases
    uv run python tools/wazuh_logtest.py --case tier3_unknown_template

Each case is a file in `wazuh/tests/` plus the rule id it must produce. The lines go through
`wazuh-logtest` inside the running manager, so this tests the rules Wazuh actually loaded — not our
reading of them. That distinction earned its keep twice while A6 was written:

* `<field name="veyra.revision">^([2-9]|\\d{2,})$</field>` does not load at all. Wazuh's default
  field matcher is OS_Regex, which has no alternation; the rule needs `type="pcre2"`.
* A parent's children are tried in **document order** and the first match wins. Rule 100130 has to
  be last, or a corrected event alerts as an ordinary auth failure and the correction is invisible.

Neither would have been caught by reading the XML, and neither shows up as an error at delivery
time: the first stops analysisd from loading the file, the second quietly produces the wrong alert.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "wazuh" / "tests"
MANAGER = "veyra-wazuh-manager"
LOGTEST = "/var/ossec/bin/wazuh-logtest"

# sample file -> a rule id the sample must produce **somewhere** in its chain. "Somewhere" rather
# than "on the last line" because a frequency rule fires once, at the moment the threshold is
# crossed: the burst reports 100110, 100110, 100110, 100110, **100111**, 100110. Asserting on the
# last line would have quietly demanded the wrong thing.
CASES: dict[str, str] = {
    "tier1_auth_failure.json": "100110",
    "tier3_unknown_template.json": "100120",
    "tier4_unparseable.json": "100121",
    "revision2_corrected.json": "100130",
    "brute_force_burst.json": "100111",
}

_ID = re.compile(r"^\s+id: '(\d+)'", re.MULTILINE)


def run_case(path: Path) -> list[str]:
    """The rule ids `wazuh-logtest` reported, in order."""
    with path.open("rb") as handle:
        result = subprocess.run(
            ["docker", "exec", "-i", MANAGER, LOGTEST],
            stdin=handle,
            # wazuh-logtest writes its phase output to **stderr**, not stdout — reading only stdout
            # gets an empty string and looks exactly like "no rule fired".
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )
    if "error when connecting with wazuh-analysisd" in result.stdout:
        raise SystemExit(
            "wazuh-analysisd is not running — the rules probably failed to load.\n"
            f"  docker exec {MANAGER} tail -5 /var/ossec/logs/ossec.log"
        )
    return _ID.findall(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="run one sample by name (with or without .json)")
    args = parser.parse_args()

    cases = dict(CASES)
    if args.case:
        name = args.case if args.case.endswith(".json") else f"{args.case}.json"
        if name not in cases:
            raise SystemExit(f"no such case: {name}; known: {', '.join(sorted(cases))}")
        cases = {name: cases[name]}

    failures: list[str] = []
    for name, expected in sorted(cases.items()):
        path = SAMPLES / name
        if not path.exists():
            failures.append(f"{name}: missing sample file")
            continue
        fired = run_case(path)
        ok = expected in fired
        chain = ", ".join(fired) if fired else "none"
        print(f"{'ok  ' if ok else 'FAIL'} {name:<32} expected {expected}, fired [{chain}]")
        if not ok:
            failures.append(f"{name}: expected {expected}, fired [{chain}]")

    if failures:
        print("\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print(f"\n{len(cases)} rule case(s) verified against Wazuh's own engine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
