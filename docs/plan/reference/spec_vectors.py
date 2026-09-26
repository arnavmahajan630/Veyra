"""VEYRA reference implementation + test vectors (frozen after S0).

Implements exactly what 02_CONTRACTS.md specifies for:
  IF-TEMPLATE-SIG  (token masking + signature)
  IF-CHAIN         (per-partition hash chain)
  IF-MERKLE        (RFC 6962 tree hashing)
Run:  python3 reference/spec_vectors.py   -> prints the vectors quoted in 02_CONTRACTS.md.
Every implementation (veyra_common.hashing, veyra_evidence.chain/merkle, export verify.py)
must reproduce these outputs in its unit tests.
"""
import hashlib, re, uuid, json

def sha(b): return hashlib.sha256(b).hexdigest()

RE_UUID=re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
RE_IPV4=re.compile(r'^(\d{1,3}\.){3}\d{1,3}(:\d{1,5})?$')
RE_IPV6=re.compile(r'^[0-9a-fA-F:]*:[0-9a-fA-F:]*:[0-9a-fA-F:]*$')
RE_EMAIL=re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')
RE_KV=re.compile(r'^([A-Za-z_][\w.\-]*)=(.*)$')
RE_CKV=re.compile(r'^([A-Za-z_][\w.\-]*):(.+)$')
RE_TS=re.compile(r'^\d{1,4}[-/:T]\d{1,2}([-/:T.]\d{1,4})*Z?$')
RE_HEX=re.compile(r'^[0-9a-fA-F]{8,}$')
RE_DIG=re.compile(r'\d')

def mask(t):
    if RE_UUID.match(t): return '<UUID>'
    if RE_IPV4.match(t): return '<IP>'
    if RE_TS.match(t): return '<TS>'
    if RE_IPV6.match(t) and ('::' in t or t.count(':')>=3): return '<IP>'
    if RE_EMAIL.match(t): return '<EMAIL>'
    m=RE_KV.match(t)
    if m: return m.group(1)+'=<V>'
    m=RE_CKV.match(t)
    if m: return m.group(1)+':<V>'
    if RE_HEX.match(t): return '<HEX>'
    if RE_DIG.search(t): return '<NUM>'
    return t

def template_sig(scope, text):
    masked=' '.join(mask(t) for t in text.split())
    return masked, 't_'+sha((scope+'\x1f'+masked).encode())[:12]

samples=[
 ("authsrv","user=neel.k FAILED login from 45.12.3.9 via 10.2.3.4 attempts:3"),
 ("authsrv","user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1"),
 ("unregistered","Sep 26 14:05:11 conn 88213 closed by 10.0.0.5"),
]
out={"template_sig":[]}
for s,t in samples:
    m,sig=template_sig(s,t); out["template_sig"].append({"scope":s,"text":t,"masked":m,"sig":sig})

raws=[
 b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=neel.k FAILED login from 45.12.3.9 via 10.2.3.4 attempts:3"} | trace=\n  at com.x.Auth.login(Auth.java:88)',
 b'<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password for invalid user admin from 45.12.3.9 port 52144 ssh2',
 b'CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4 dpt=22 act=deny',
]
out["raw_sha256"]=[{"raw_utf8":r.decode(),"sha256":sha(r)} for r in raws]

uids=[uuid.UUID("0192a4f0-0000-7000-8000-000000000001"),uuid.UUID("0192a4f0-0000-7000-8000-000000000002"),uuid.UUID("0192a4f0-0000-7000-8000-000000000003")]
topic,part="raw.acme",0
h=hashlib.sha256(b"VEYRA-GENESIS\x1f"+topic.encode()+b"\x1f"+str(part).encode()).digest()
chain=[{"h0":h.hex()}]
for i,(r,u) in enumerate(zip(raws,uids)):
    off=100+i
    h=hashlib.sha256(h+bytes.fromhex(sha(r))+u.bytes+off.to_bytes(8,'big')).digest()
    chain.append({"event_uid":str(u),"offset":off,"raw_sha256":sha(r),"h":h.hex()})
out["chain"]={"topic":topic,"partition":part,"steps":chain}

def leaf(d): return hashlib.sha256(b"\x00"+d).digest()
def node(l,r): return hashlib.sha256(b"\x01"+l+r).digest()
def mth(ds):
    n=len(ds)
    if n==1: return leaf(ds[0])
    k=1
    while k*2<n: k*=2
    return node(mth(ds[:k]),mth(ds[k:]))
leaves=[hashlib.sha256(f"segment-{i}".encode()).digest() for i in range(3)]
out["merkle"]={"leaf_data_hex":[l.hex() for l in leaves],"root":mth(leaves).hex()}
print(json.dumps(out,indent=1))
