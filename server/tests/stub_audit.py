#!/usr/bin/env python3
import json
import os
import sys
import time

out = None
home = None
args = sys.argv[1:]
for i, a in enumerate(args):
    if a == '--output':
        out = args[i + 1]
    if a == '--history-home':
        home = args[i + 1]
if os.environ.get('STUB_FAIL'):
    print('boom', file=sys.stderr)
    sys.exit(3)
found = []
for root, _, names in os.walk(home or '.'):
    found += [n for n in names if n.endswith('.jsonl')]
for i in range(1, 4):
    print('\r逐段审查 %d/3' % i, end='', file=sys.stderr, flush=True)
    time.sleep(0.2)
time.sleep(float(os.environ.get('STUB_SLEEP', '0')))
os.makedirs(out)
with open(os.path.join(out, 'report.html'), 'w', encoding='utf-8') as handle:
    handle.write('<html><body>stub report: %s files, HOME=%s ARGS=%s TOKEN=%s</body></html>' % (
        len(found), os.environ.get('HOME'), json.dumps(args), os.environ.get('ANTHROPIC_AUTH_TOKEN', '')))
with open(os.path.join(out, 'report.json'), 'w') as handle:
    handle.write('{}')
