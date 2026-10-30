# -*- coding: utf-8 -*-
"""Regression tests for statusline.py. Standard library only.

    python3 tests/test_statusline.py

Each test builds a throwaway transcript + isolated state directory, runs the
script as a subprocess with `-S -E` (exactly how the status line is invoked),
and asserts on the ANSI-stripped output.
"""
import sys, io, os, json, re, time, shutil, tempfile, subprocess

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'statusline.py')
PY = sys.executable
PASSED = FAILED = 0


def check(ok, name, detail=''):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print('PASS %s' % name)
    else:
        FAILED += 1
        print('FAIL %s%s' % (name, ('  <- ' + detail) if detail else ''))


def strip_ansi(s):
    return re.sub(r'\x1b\[[0-9;]*m', '', s)


def render(workdir, payload, cfg=None, env_extra=None):
    """Run the status line; return the plain-text line."""
    env = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_STATUSLINE_')}
    env['CLAUDE_STATUSLINE_HOME'] = os.path.join(workdir, 'data')
    if cfg is not None:
        path = os.path.join(workdir, 'cfg.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f)
        env['CLAUDE_STATUSLINE_CONFIG'] = path
    else:
        env['CLAUDE_STATUSLINE_CONFIG'] = os.path.join(workdir, 'no-such-config.json')
    env.update(env_extra or {})
    out = subprocess.run([PY, '-S', '-E', SCRIPT], input=json.dumps(payload).encode('utf-8'),
                         capture_output=True, env=env)
    return strip_ansi(out.stdout.decode('utf-8', 'replace')).strip()


def write_transcript(path, records):
    with open(path, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')


def assistant(mid, ts, model, n_in=0, n_out=0, n_hit=0, n_write=0):
    return {'type': 'assistant', 'timestamp': ts,
            'message': {'id': mid, 'model': model,
                        'usage': {'input_tokens': n_in, 'output_tokens': n_out,
                                  'cache_read_input_tokens': n_hit,
                                  'cache_creation_input_tokens': n_write}}}


def money_in(line):
    m = re.findall(r'[￥$](\d+\.\d\d)', line)
    return [float(x) for x in m]


DEEPSEEK = {
    'currency': {'symbol': '￥', 'code': 'CNY'},
    'models': {'deepseek-flash': [0.02, 1.0, 4.0]},
    'balance': {'url': '{base}/user/balance', 'format': 'deepseek', 'ttl': 3600},
}


def with_peak(cfg):
    c = json.loads(json.dumps(cfg))
    c['peak'] = {'multiplier': 2.0, 'weekdays_only': True, 'windows': [[9, 12], [14, 18]]}
    return c


def test_cost_dedup(tmp):
    """Three transcript lines share one message.id (Claude Code writes one line
    per content block). Only one must be billed."""
    tp = os.path.join(tmp, 'dedup.jsonl')
    rec = assistant('m1', '2026-09-30T12:30:00+08:00', 'deepseek-flash', n_in=100000)
    write_transcript(tp, [rec, dict(rec), dict(rec)])
    line = render(tmp, {'transcript_path': tp}, DEEPSEEK)
    got = money_in(line)
    check(got and abs(got[0] - 0.10) < 0.005, 'dedup by message.id -> ￥0.10', str(got))


def test_peak_multiplier(tmp):
    """Same tokens at 10:00 on a weekday cost double."""
    tp = os.path.join(tmp, 'peak.jsonl')
    write_transcript(tp, [assistant('m2', '2026-09-30T10:00:00+08:00',
                                   'deepseek-flash', n_in=100000)])
    line = render(tmp, {'transcript_path': tp}, with_peak(DEEPSEEK))
    got = money_in(line)
    check(got and abs(got[0] - 0.20) < 0.005, 'peak hours x2 -> ￥0.20', str(got))
    check('○ off-peak' in line, 'off-peak label rendered from built-in defaults', line)


def test_weekend_not_peak(tmp):
    tp = os.path.join(tmp, 'weekend.jsonl')
    # 2026-10-04 is a Sunday
    write_transcript(tp, [assistant('m3', '2026-10-04T10:00:00+08:00',
                                   'deepseek-flash', n_in=100000)])
    line = render(tmp, {'transcript_path': tp}, with_peak(DEEPSEEK))
    got = money_in(line)
    check(got and abs(got[0] - 0.10) < 0.005, 'weekend stays off-peak -> ￥0.10', str(got))


def test_token_rate(tmp):
    """Rate = one turn's output over (user record -> assistant record)."""
    tp = os.path.join(tmp, 'rate.jsonl')
    write_transcript(tp, [
        {'type': 'user', 'timestamp': '2026-09-30T12:30:00+08:00'},
        assistant('m7', '2026-09-30T12:30:03+08:00', 'deepseek-flash',
                  n_in=100, n_out=1600, n_hit=1000),
    ])
    line = render(tmp, {'transcript_path': tp}, DEEPSEEK)
    check('≈533 tok/s' in line, 'token rate = 1600 tok / 3s -> 533 tok/s', line)


def test_compact_json(tmp):
    """Real transcripts are compact JSON ("type":"user", "timestamp":"...").
    Pretty-printed spacing must parse identically."""
    tp = os.path.join(tmp, 'compact.jsonl')
    with open(tp, 'w', encoding='utf-8') as f:
        f.write('{"type":"user","timestamp":"2026-09-30T12:30:00+08:00"}\n')
        f.write('{"type":"assistant","timestamp":"2026-09-30T12:30:03+08:00",'
                '"message":{"id":"mc1","model":"deepseek-flash",'
                '"usage":{"input_tokens":100000,"output_tokens":1600,'
                '"cache_read_input_tokens":0}}}\n')
    line = render(tmp, {'transcript_path': tp}, DEEPSEEK)
    got = money_in(line)
    # 100000 input x 1.0 + 1600 output x 4.0, per 1M -> 0.1064, displayed ￥0.11
    check(got and abs(got[0] - 0.1064) < 0.005, 'compact JSON parses (cost ￥0.11)', str(got))
    check('≈533 tok/s' in line, 'compact JSON: token rate parsed', line)


def test_passthrough(tmp):
    """No price table -> use payload.cost.total_cost_usd."""
    tp = os.path.join(tmp, 'pass.jsonl')
    write_transcript(tp, [assistant('m4', '2026-09-30T12:30:00+08:00',
                                   'claude-opus-4-5', n_in=1000, n_out=100)])
    line = render(tmp, {'transcript_path': tp, 'cost': {'total_cost_usd': 1.23}})
    check('$1.23' in line, 'passthrough shows payload cost', line)


def test_context(tmp):
    tp = os.path.join(tmp, 'ctx.jsonl')
    write_transcript(tp, [assistant('m5', '2026-09-30T12:30:00+08:00',
                                   'deepseek-flash', n_in=1000, n_out=111, n_hit=403489)])
    payload = {'transcript_path': tp,
               'context_window': {'context_window_size': 1000000, 'used_percentage': 40}}
    line = render(tmp, payload)
    check('400K/1M' in line and '40%' in line, 'context from payload -> 400K/1M 40%', line)


def test_balance(tmp):
    """Balance = cached truth - (session cost - anchor), with the edge cases
    that matter: fresh truth, no-op anchor, cost regression, floor at zero."""
    tp = os.path.join(tmp, 'fake_session.jsonl')
    write_transcript(tp, [assistant('m1', '2026-09-30T12:30:00+08:00',
                                   'deepseek-flash', n_in=100000)])
    data = os.path.join(tmp, 'data')
    os.makedirs(data, exist_ok=True)
    BAL = os.path.join(data, 'balance.json')
    STATE = os.path.join(data, 'state.json')
    CNY = 0.10
    T = time.time()

    def run(bal_total, bal_ts, bal_ts_state, bal_base):
        json.dump({'ts': bal_ts, 'total': bal_total, 'cur': 'CNY',
                   'attempt': T, 'err': ''}, open(BAL, 'w', encoding='utf-8'))
        json.dump({tp: {'size': -1, 'offset': 0, 'ids': [], 'peak': 0.0, 'off': 0.0,
                        'tok': 0, 'ctx': 0, 'turns': 0, 'ts': '',
                        'balBase': bal_base, 'balTs': bal_ts_state}},
                  open(STATE, 'w', encoding='utf-8'))
        line = render(tmp, {'transcript_path': tp}, DEEPSEEK)
        nums = money_in(line)
        return (nums[0] if nums else None), (nums[1] if len(nums) > 1 else None)

    cost, bal = run(100.0, T, T, 0.04)
    check(cost is not None and abs(cost - CNY) < 0.005, 'balance case: session cost fixed at ￥0.10', str(cost))
    check(bal is not None and abs(bal - 99.94) < 0.005,
          'anchored: 100.00 - (0.10 - 0.04) = 99.94', str(bal))

    _, bal = run(100.0, T, T, CNY)
    check(bal is not None and abs(bal - 100.00) < 0.005, 'anchor == cost -> no subtraction', str(bal))

    _, bal = run(100.0, T, T - 99, 0.04)
    check(bal is not None and abs(bal - 100.00) < 0.005, 'new truth -> show cached value', str(bal))
    base = json.load(open(STATE, encoding='utf-8')).get(tp, {}).get('balBase')
    check(base is not None and abs(base - CNY) < 0.005, 'new truth anchors balBase in place', str(base))

    _, bal = run(100.0, T, T, 0.50)
    check(bal is not None and abs(bal - 100.00) < 0.005,
          'cost regression -> no phantom credit', str(bal))

    _, bal = run(0.05, T, T, 0.0)
    check(bal is not None and abs(bal - 0.00) < 0.005, 'clamped at zero', str(bal))


def test_robustness(tmp):
    """Never crash, never print a traceback into the status line."""
    tp = os.path.join(tmp, 'ok.jsonl')
    write_transcript(tp, [assistant('m6', '2026-09-30T12:30:00+08:00', 'deepseek-flash', n_in=10)])
    for name, payload in [('empty payload', {}),
                          ('missing transcript', {'transcript_path': '/no/such/file'}),
                          ('null context_window', {'transcript_path': tp, 'context_window': None})]:
        line = render(tmp, payload, DEEPSEEK)
        check('Traceback' not in line, 'robust: %s' % name, line)

    out = subprocess.run([PY, '-S', '-E', SCRIPT], input=b'not json',
                         capture_output=True)
    check(b'Traceback' not in out.stdout, 'robust: garbage stdin',
          out.stdout.decode('utf-8', 'replace'))


def main():
    tests = [test_cost_dedup, test_peak_multiplier, test_weekend_not_peak,
             test_token_rate, test_compact_json, test_passthrough, test_context,
             test_balance, test_robustness]
    for i, fn in enumerate(tests):
        tmp = tempfile.mkdtemp(prefix='sl_test_%d_' % i)
        try:
            fn(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print('\n%d passed, %d failed' % (PASSED, FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
