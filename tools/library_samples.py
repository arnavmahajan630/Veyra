# ruff: noqa: E501  (sample log lines are as long as real vendors make them)
"""Write the library packs' golden samples into a contract registry checkout.

Run from Veyra/:  uv run --python 3.12 python tools/library_samples.py ../contracts-repo
sshd and Acme samples are demo corpus lines; the generic packs and nginx get
vendor-realistic lines of their own (none of them appear in the demo).
"""

import sys
from pathlib import Path

registry = Path(sys.argv[1])
corpus = Path("demo/corpus")

GENERIC_CEF = [
    "CEF:0|Fortinet|FortiGate|7.2|0000000013|traffic forward|3|src=10.1.1.5 dst=8.8.8.8 spt=51234 dpt=53 proto=udp act=accept",
    "CEF:0|Fortinet|FortiGate|7.2|0000000013|traffic forward|3|src=10.1.1.6 dst=1.1.1.1 spt=51240 dpt=443 proto=tcp act=accept",
    "CEF:0|Palo Alto Networks|PAN-OS|11.0|TRAFFIC|end|3|src=10.2.0.14 dst=52.94.236.248 spt=60412 dpt=443 proto=tcp act=allow",
    "CEF:0|Palo Alto Networks|PAN-OS|11.0|TRAFFIC|deny|5|src=45.12.3.9 dst=10.2.0.1 spt=40022 dpt=3389 proto=tcp act=deny",
    "CEF:0|Check Point|VPN-1 & FireWall-1|R81|Log|Drop|6|src=103.21.4.77 dst=10.2.3.4 dpt=22 proto=tcp act=drop",
    "CEF:0|Check Point|VPN-1 & FireWall-1|R81|Log|Accept|2|src=10.4.1.20 dst=10.2.3.9 dpt=443 proto=tcp act=accept",
    "CEF:0|Cisco|ASA|9.18|106023|Deny tcp|5|src=192.168.9.14 dst=10.2.0.5 spt=33440 dpt=445 proto=tcp act=deny",
    "CEF:0|Cisco|ASA|9.18|302013|Built outbound TCP connection|3|src=10.2.0.7 dst=142.250.67.46 spt=55012 dpt=443 proto=tcp",
    "CEF:0|Sophos|XG Firewall|19.5|010101600001|Allowed|1|src=10.5.1.2 dst=10.5.9.9 spt=49152 dpt=8080 proto=tcp act=allow",
    "CEF:0|Juniper|SRX|21.4|RT_FLOW_SESSION_DENY|session denied|6|src=203.0.113.50 dst=10.2.0.2 spt=1024 dpt=23 proto=tcp act=deny",
]

TAB = "\t"
GENERIC_LEEF = [
    f"LEEF:1.0|IBM|QRadar|7.5|Login|src=10.0.0.5{TAB}dst=10.0.0.9{TAB}usrName=alice",
    f"LEEF:1.0|IBM|QRadar|7.5|Logout|src=10.0.0.5{TAB}dst=10.0.0.9{TAB}usrName=alice",
    f"LEEF:2.0|Lancope|StealthWatch|7.3|flow|src=10.3.1.4{TAB}dst=10.3.9.1{TAB}srcPort=51000{TAB}dstPort=443",
    f"LEEF:2.0|Lancope|StealthWatch|7.3|flow|src=10.3.1.5{TAB}dst=10.3.9.1{TAB}srcPort=51004{TAB}dstPort=443",
    f"LEEF:1.0|Imperva|SecureSphere|14.7|SQLInjection|src=45.12.3.9{TAB}dst=10.2.0.80{TAB}severity=8",
    f"LEEF:1.0|Imperva|SecureSphere|14.7|XSS|src=103.21.4.77{TAB}dst=10.2.0.80{TAB}severity=6",
    f"LEEF:1.0|Trend Micro|Deep Security|20.0|4000001|src=10.4.2.9{TAB}dst=10.4.0.1{TAB}act=blocked",
    f"LEEF:1.0|Trend Micro|Deep Security|20.0|4000002|src=10.4.2.10{TAB}dst=10.4.0.1{TAB}act=allowed",
    f"LEEF:2.0|Symantec|Endpoint Protection|14.3|Virus|src=10.6.1.12{TAB}dst=10.6.0.2{TAB}virus=EICAR",
    f"LEEF:1.0|IBM|QRadar|7.5|PortScan|src=198.51.100.7{TAB}dst=10.2.0.1{TAB}dstPort=22",
]

UA = '"Mozilla/5.0 (X11; Linux x86_64) Firefox/131.0"'
NGINX = [
    f'10.4.1.20 - - [26/Sep/2026:14:05:01 +0530] "GET /index.html HTTP/1.1" 200 5120 "-" {UA}',
    f'10.4.1.21 - r.patil [26/Sep/2026:14:05:02 +0530] "GET /api/v1/status HTTP/1.1" 200 312 "-" {UA}',
    '45.12.3.9 - - [26/Sep/2026:14:05:03 +0530] "GET /wp-login.php HTTP/1.1" 404 153 "-" "curl/8.5.0"',
    f'45.12.3.9 - - [26/Sep/2026:14:05:04 +0530] "POST /login HTTP/1.1" 401 88 "https://portal.maha.example/" {UA}',
    f'10.4.2.9 - v.rao [26/Sep/2026:14:05:05 +0530] "POST /api/v1/orders HTTP/1.1" 201 64 "-" {UA}',
    f'10.4.2.9 - v.rao [26/Sep/2026:14:05:06 +0530] "PUT /api/v1/orders/77 HTTP/1.1" 204 0 "-" {UA}',
    '10.4.2.10 - - [26/Sep/2026:14:05:07 +0530] "DELETE /api/v1/cache HTTP/1.1" 403 17 "-" "python-requests/2.32"',
    '103.21.4.77 - - [26/Sep/2026:14:05:08 +0530] "GET /../../etc/passwd HTTP/1.1" 400 150 "-" "Nikto/2.5"',
    f'10.4.1.44 - neel.k [26/Sep/2026:14:05:09 +0530] "GET /reports/q3.pdf HTTP/2.0" 200 884211 "https://portal.maha.example/reports" {UA}',
    '10.4.1.31 - - [26/Sep/2026:14:05:10 +0530] "HEAD /healthz HTTP/1.1" 200 0 "-" "kube-probe/1.30"',
]


def write(pack: str, name: str, line: str | bytes) -> None:
    path = registry / "samples" / "library" / pack / f"{name}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = line if isinstance(line, bytes) else line.encode("utf-8")
    path.write_bytes(data.rstrip(b"\r\n") + b"\n")


sshd = (corpus / "linux_sshd.log").read_bytes().split(b"\n")
for n in (1, 7, 8, 9, 10, 11, 12, 13, 15, 16, 18, 19, 20, 22, 23, 24):
    write("linux_sshd", f"{n:02d}", sshd[n - 1])
acme = [line for line in (corpus / "acme_ngfw_cef.log").read_bytes().split(b"\n") if line.strip()]
for n, line in enumerate(acme, start=1):
    write("acme_ngfw_cef", f"{n:02d}", line)
for pack, lines in (
    ("generic_cef", GENERIC_CEF),
    ("generic_leef", GENERIC_LEEF),
    ("nginx_access", NGINX),
):
    for n, text in enumerate(lines, start=1):
        write(pack, f"{n:02d}", text)
print("samples written under", registry / "samples" / "library")
