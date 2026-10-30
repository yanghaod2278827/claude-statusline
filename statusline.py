#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""claude-statusline: a live cost + context status line for Claude Code.

Claude Code pipes a JSON payload to the configured `statusLine` command
(session_id, transcript_path, model, cost, context_window, prompt_cache, ...).
We read only *that session's* transcript, keep running totals incremented
incrementally, and print one ANSI-colored line:

    deepseek-flash │ ￥1.87 │ bal ￥88.51 │ 17.3M tok · 133 turns │
    ctx 64K/1M ▏░░░░░░░░ 6.4% │ hit 99% │ ≈250 tok/s │ ○ off-peak

Pricing modes
-------------
* passthrough (default) — show `payload.cost.total_cost_usd`, the number
  Claude Code already computes. Correct for the Anthropic API.
* custom — if the current model appears in `models`, recompute the cost from
  per-token rates, optionally applying peak/off-peak multipliers and a
  different currency. Needed when the provider's real price sheet differs from
  Claude Code's built-in USD pricing (DeepSeek, for instance). See
  `examples/deepseek.json`.

Config resolution (first file found wins, merged over the built-in defaults):
    $CLAUDE_STATUSLINE_CONFIG
    ~/.claude/statusline.json
    <script dir>/pricing.json

Data (state + balance cache) lives in `~/.claude/statusline/`, overridable via
`$CLAUDE_STATUSLINE_HOME`.

Zero dependencies, Python 3.8+, POSIX and Windows.
"""

import sys, io, os, json, re, time

# Claude Code decodes our stdout as UTF-8 and writes the payload as UTF-8, while
# Python attached to a pipe on Windows defaults to the ANSI code page (cp1252 /
# cp936). Without this, box-drawing glyphs raise UnicodeEncodeError (the status
# line goes blank) and CJK session names come back mojibake.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.expanduser(os.environ.get('CLAUDE_STATUSLINE_HOME') or '~/.claude/statusline')
STATE = os.path.join(DATA_DIR, 'state.json')
BAL_CACHE = os.path.join(DATA_DIR, 'balance.json')

# ── defaults ──────────────────────────────────────────────────────────────────
# Everything here can be overridden by the config file; see CONFIG.md.
DEFAULT_CONFIG = {
    # Currency used by custom pricing. `symbol` is purely cosmetic.
    'currency': {'symbol': '$', 'code': 'USD'},

    # model id -> [cache-hit, input, output] in `currency` per 1M tokens.
    # Empty = passthrough mode (trust payload.cost.total_cost_usd).
    'models': {},

    # Optional peak/off-peak multiplier. `null` disables it.
    #   weekdays_only: Mon-Fri only (weekends always off-peak)
    #   windows: [start_hour, end_hour) pairs, local time
    #   holidays: date ranges that count as off-peak regardless of weekday,
    #             as "YYYY-MM-DD" or "YYYY-MM-DD..YYYY-MM-DD"
    'peak': None,
    # {  'multiplier': 2.0, 'weekdays_only': true, 'windows': [[9, 12], [14, 18]],
    #    'holidays': ['2026-10-01..2026-10-07'] }

    # Optional account balance probe. `null` disables it.
    #   url:    may contain {base} = ANTHROPIC_BASE_URL minus the "/anthropic" tail
    #   format: "deepseek" (balance_infos[0].total_balance) or "path" (dot path)
    #   path:   dot path used when format == "path", e.g. "data.credits"
    'balance': None,
    # {  'url': '{base}/user/balance', 'format': 'deepseek', 'ttl': 120, 'retry': 120 }

    'labels': {'balance': 'bal', 'turns': 'turns', 'peak': '◉ peak', 'offpeak': '○ off-peak'},
    'show': {'session_name': True, 'cache_hit': True, 'balance': True, 'token_rate': True},
    # 'turn' = most recent request; 'avg' = session mean (needs cost payload)
    'rate_mode': 'turn',
}

# ANSI
RESET, DIM, BOLD = '\033[0m', '\033[90m', '\033[1m'
CYAN, GREEN, YELLOW, RED, MAGENTA = '\033[96m', '\033[92m', '\033[93m', '\033[91m', '\033[95m'

# Only the timestamp is needed from user/tool-result lines; they can be tens of
# KB of base64 screenshots, so a full json.loads() on each is wasteful.
TS_RE = re.compile(rb'"timestamp":\s*"([^"]+)"')
# A trailing "[1m]" (Claude Code's client-side marker for a 1M context window)
RE_WINDOW = re.compile(r'\[[^\]]*\]\s*$')


# ── config ────────────────────────────────────────────────────────────────────
def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def config_candidates():
    env = os.environ.get('CLAUDE_STATUSLINE_CONFIG')
    if env:
        return [env]
    return [os.path.expanduser('~/.claude/statusline.json'),
            os.path.join(SCRIPT_DIR, 'pricing.json')]


def load_config():
    cfg = DEFAULT_CONFIG
    for path in config_candidates():
        blob = read_json(path)
        if blob:
            cfg = deep_merge(cfg, blob)
            break
    return cfg


# ── state helpers ─────────────────────────────────────────────────────────────
def atomic_write_json(path, obj):
    """The status line is invoked concurrently and often; two processes writing
    the same file directly will truncate each other. Write to a pid-suffixed
    temp file, then os.replace() (atomic on both POSIX and Windows)."""
    tmp = '{0}.{1}.tmp'.format(path, os.getpid())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def fresh():
    return {'size': -1, 'offset': 0, 'ids': [], 'peak': 0.0, 'off': 0.0,
            'tok': 0, 'ctx': 0, 'turns': 0, 'ts': '',
            'cacheHit': 0, 'cacheMiss': 0,
            # token-rate bookkeeping: cumulative output, last user timestamp,
            # and the output/duration of the most recent completed turn
            'outTok': 0, 'lastUser': '', 'lastOut': 0, 'lastMs': 0,
            # balance extrapolation anchor: value of `cny` when the current
            # cached truth was fetched — see balance_section()
            'balBase': None, 'balTs': 0}


def load_states():
    blob = read_json(STATE, {})
    return blob if isinstance(blob, dict) else {}


def save_state(path, entry, all_states):
    all_states[path] = entry
    # Keep the 8 most recent sessions so the file cannot grow without bound.
    if len(all_states) > 8:
        keep = sorted(all_states.items(), key=lambda kv: kv[1].get('ts') or '', reverse=True)[:8]
        all_states = dict(keep)
    atomic_write_json(STATE, all_states)


# ── transcript scan ───────────────────────────────────────────────────────────
def to_epoch(ts):
    """ISO timestamp -> epoch seconds. Naive values are read as local time; the
    same rule applies to both ends of a difference, so deltas stay correct."""
    from datetime import datetime
    return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()


def parse_holidays(spec):
    """['2026-10-01..2026-10-07', '2026-01-01'] -> [(date, date), ...]

    Invalid entries are skipped rather than raising: a typo in the config must
    not take the status line down.
    """
    from datetime import date
    out = []
    for item in spec or ():
        try:
            text = str(item).strip()
            if '..' in text:
                a, b = text.split('..', 1)
                out.append((date.fromisoformat(a.strip()), date.fromisoformat(b.strip())))
            else:
                d = date.fromisoformat(text)
                out.append((d, d))
        except ValueError:
            continue
    return out


def peak_multiplier(dt, peak, holidays=()):
    """Multiplier in force at `dt`. Holidays win over the weekday rule: public
    holidays are billed off-peak all day even when they fall Mon-Fri."""
    if not peak:
        return 1.0
    day = dt.date()
    for start, end in holidays:
        if start <= day <= end:
            return 1.0
    if peak.get('weekdays_only', True) and dt.weekday() >= 5:
        return 1.0
    h = dt.hour
    for start, end in peak.get('windows') or []:
        if start <= h < end:
            return float(peak.get('multiplier', 2.0))
    return 1.0


def scan(path, st, cfg):
    """Incrementally parse new bytes of the transcript, updating `st` in place."""
    from datetime import datetime
    try:
        size = os.path.getsize(path)
    except OSError:
        return st
    if size == st.get('size'):
        return st                                  # nothing new
    if size < st.get('offset', 0):                 # truncated / rebuilt
        st = fresh()
    try:
        with open(path, 'rb') as f:
            f.seek(st.get('offset', 0))
            data = f.read()
    except OSError:
        return st
    cut = data.rfind(b'\n')
    if cut == -1:
        return st                                  # partial line; retry later
    chunk, consumed = data[:cut], cut + 1

    models = cfg.get('models') or {}
    peak_cfg = cfg.get('peak')
    holidays = parse_holidays((peak_cfg or {}).get('holidays'))

    ids = set(st.get('ids') or [])
    peak, off = st.get('peak', 0.0), st.get('off', 0.0)
    tok, ctx, turns = st.get('tok', 0), st.get('ctx', 0), st.get('turns', 0)
    cache_hit, cache_miss = st.get('cacheHit', 0), st.get('cacheMiss', 0)
    last_ts = st.get('ts', '')
    out_tok = st.get('outTok', 0)
    last_user = st.get('lastUser', '')
    last_out, last_ms = st.get('lastOut', 0), st.get('lastMs', 0)

    for line in chunk.split(b'\n'):
        # user / tool_result records mark the start of a new turn. Only the
        # timestamp is needed; the first 400 bytes are enough to classify.
        # Accept both compact and pretty-printed spacing.
        head = line[:400]
        if b'"type":"user"' in head or b'"type": "user"' in head:
            mu = TS_RE.search(line)
            if mu:
                tu = mu.group(1).decode('utf-8', 'replace')
                if tu > last_user:
                    last_user = tu
            continue
        if b'"assistant"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get('type') != 'assistant':
            continue
        msg = rec.get('message') or {}
        usage = msg.get('usage') or {}
        if not usage:
            continue
        mid = msg.get('id') or rec.get('uuid')
        if mid in ids:
            # Claude Code writes one line per content block (thinking / text /
            # tool_use) with identical `usage` on each. Deduplicating by
            # message.id is what keeps totals from inflating ~2.4x.
            continue
        ids.add(mid)
        ts = rec.get('timestamp') or ''
        if ts > last_ts:
            last_ts = ts
        model = msg.get('model') or ''
        if model == '<synthetic>':          # local placeholder, no API call
            continue
        rate = models.get(model)
        n_in = (usage.get('input_tokens') or 0) + (usage.get('cache_creation_input_tokens') or 0)
        n_out = usage.get('output_tokens') or 0
        n_hit = usage.get('cache_read_input_tokens') or 0
        tok += n_in + n_out + n_hit
        turns += 1
        out_tok += n_out

        # Token rate = "one user/tool_result record -> this assistant record".
        # The transcript has no streaming timestamps (all content blocks of one
        # response land within ~1s of each other, so block-to-block deltas
        # produce absurd 1800 tok/s figures). This interval is the only real
        # wall-clock signal available. It still contains queueing + time-to-
        # first-token, so the number is a *perceived* rate, not raw decode
        # throughput — hence the "≈". Two guards drop noisy samples.
        if last_user and ts:
            try:
                ms = (to_epoch(ts) - to_epoch(last_user)) * 1000
            except ValueError:
                ms = 0
            if ms >= 400 and n_out >= 50 and n_out * 1000.0 / ms <= 1500:
                last_out, last_ms = n_out, ms

        cache_hit += n_hit
        cache_miss += n_in
        ctx = n_in + n_hit + n_out          # context = this request's input + output

        if rate:
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone()
                mult = peak_multiplier(dt, peak_cfg, holidays)
            except ValueError:
                mult = 1.0
            c = (n_hit * rate[0] + n_in * rate[1] + n_out * rate[2]) / 1e6 * mult
            if mult > 1.0:
                peak += c
            else:
                off += c

    st.update({'size': size, 'offset': st.get('offset', 0) + consumed, 'ids': sorted(ids),
               'peak': peak, 'off': off, 'tok': tok, 'ctx': ctx, 'turns': turns,
               'cacheHit': cache_hit, 'cacheMiss': cache_miss, 'ts': last_ts,
               'outTok': out_tok, 'lastUser': last_user,
               'lastOut': last_out, 'lastMs': last_ms})
    return st


# ── balance ───────────────────────────────────────────────────────────────────
def balance_url(cfg):
    """Resolve the balance endpoint; {base} is ANTHROPIC_BASE_URL without its
    /anthropic tail (DeepSeek: https://api.deepseek.com/anthropic -> .../v1)."""
    raw = (cfg.get('balance') or {}).get('url') or ''
    base = os.environ.get('ANTHROPIC_BASE_URL') or 'https://api.deepseek.com/anthropic'
    base = base.split('/anthropic')[0].rstrip('/')
    return raw.replace('{base}', base)


def balance_value(data, cfg):
    """Extract a float from the provider's response, or raise."""
    spec = (cfg.get('balance') or {})
    if spec.get('format') == 'path':
        cur = data
        for part in (spec.get('path') or '').split('.'):
            if not part:
                continue
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        return float(cur)
    # default: DeepSeek's /user/balance
    info = (data.get('balance_infos') or [{}])[0]
    return float(info['total_balance'])


def load_balance():
    blob = read_json(BAL_CACHE, {})
    return blob if isinstance(blob, dict) else {}


def refresh_balance():
    """Background entry point (--refresh-balance): fetch, atomically cache, unlock.

    Only the *truth* (total + ts) is written here. Each session's render process
    derives its own extrapolation anchor from it, so concurrent sessions do not
    fight. On failure keep the previous total — a stale balance is better than a
    disappearing one — and record `attempt`/`err` for backoff.
    """
    cfg = load_config()
    key = os.environ.get('ANTHROPIC_AUTH_TOKEN') or os.environ.get('ANTHROPIC_API_KEY') or ''
    old = load_balance()
    rec = {'ts': old.get('ts', 0), 'total': old.get('total'), 'cur': old.get('cur') or '',
           'attempt': time.time(), 'err': ''}
    try:
        import urllib.request
        if not key:
            raise RuntimeError('ANTHROPIC_AUTH_TOKEN not set')
        req = urllib.request.Request(balance_url(cfg), headers={
            'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode('utf-8'))
        total = balance_value(data, cfg)      # evaluate first: never half-write
        rec.update({'ts': time.time(), 'total': total,
                    'cur': (cfg.get('balance') or {}).get('currency') or ''})
    except Exception as e:
        rec['err'] = '%s: %s' % (type(e).__name__, e)
    atomic_write_json(BAL_CACHE, rec)
    try:
        os.remove(BAL_CACHE + '.lock')
    except OSError:
        pass


def maybe_spawn_refresh(bal, cfg):
    """Spawn a detached refresh when the cache is stale, guarded by an O_EXCL
    lock so a burst of concurrent renders forks at most one fetcher.

    Never fetch synchronously here: one slow request can take 8s and the status
    line would freeze for that long.
    """
    spec = cfg.get('balance') or {}
    ttl = float(spec.get('ttl') or 120)
    retry = float(spec.get('retry') or 120)
    now = time.time()
    if bal.get('err'):
        if now - (bal.get('attempt') or 0) < retry:
            return
    elif bal.get('total') is not None and now - (bal.get('ts') or 0) < ttl:
        return
    lock = BAL_CACHE + '.lock'
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        # Lost the race, or a crashed refresher left a stale lock: reap it after
        # `retry` seconds so the next render can try again.
        try:
            if now - os.stat(lock).st_mtime > retry:
                os.remove(lock)
        except OSError:
            pass
        return
    try:
        import subprocess
        flags = 0
        if os.name == 'nt':
            flags = 0x00000008 | 0x08000000     # DETACHED_PROCESS | CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, '-S', '-E', os.path.abspath(__file__),
                          '--refresh-balance'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError:
        try:
            os.remove(lock)
        except OSError:
            pass


# ── formatting ────────────────────────────────────────────────────────────────
def fmt_tok(n):
    if n >= 1e6:
        return f'{n / 1e6:.1f}M'
    if n >= 1e3:
        return f'{n / 1e3:.0f}K'
    return f'{n:.0f}'


def fmt_window(n):
    """1000000 -> '1M' (fmt_tok would say '1.0M', one digit too many)"""
    return f'{n / 1e6:g}M' if n >= 1e6 else fmt_tok(n)


def clip(s, width):
    """Truncate to a display width; CJK characters count as two columns."""
    out, w = '', 0
    for ch in str(s):
        cw = 2 if ord(ch) > 0x2000 else 1
        if w + cw > width:
            return out + '…'
        out += ch
        w += cw
    return out


# 1/8 blocks: a 10-cell bar has 80 ticks (1.25% each). With whole cells a 1M
# window would sit at "0 cells" for most of a session and look frozen.
BLOCKS = ' ▏▎▍▌▋▊▉'


def bar(pct, width=10):
    pct = max(0.0, min(100.0, pct))
    eighths = int(round(pct / 100 * width * 8))
    full, rem = divmod(eighths, 8)
    if rem:
        return '█' * full + BLOCKS[rem] + '░' * (width - full - 1)
    return '█' * full + '░' * (width - full)


def money(cfg, amount):
    cur = cfg.get('currency') or {}
    sym = cur.get('symbol', '$')
    return f'{sym}{amount:.2f}'


def model_name(payload):
    m = payload.get('model') or {}
    if isinstance(m, dict):
        name = m.get('display_name') or m.get('id') or ''
    else:
        name = str(m or '')
    return name or os.environ.get('ANTHROPIC_MODEL', '')


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    from datetime import datetime

    cfg = load_config()
    show = cfg.get('show') or {}
    labels = cfg.get('labels') or DEFAULT_CONFIG['labels']

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}

    tp = payload.get('transcript_path') or ''
    if not tp:
        # Manual testing fallback: newest transcript on disk.
        import glob
        cands = glob.glob(os.path.expanduser('~/.claude/projects/*/*.jsonl'))
        tp = max(cands, key=os.path.getmtime) if cands else ''

    model = model_name(payload)
    # Claude Code's client-side "[1m]" marker means a 1M window; drop it for
    # display but remember it for the fallback window size.
    window = 1_000_000 if '[1m]' in str(model) else 200_000
    model = RE_WINDOW.sub('', str(model)) or model

    # Manual window override for debugging / using another scale. Must take
    # priority over payload.context_window_size, which Claude Code sends on
    # every render and would otherwise always win.
    try:
        force_win = int(os.environ.get('CC_STATUS_WINDOW') or 0)
    except ValueError:
        force_win = 0

    all_states = load_states()
    st = (all_states.get(tp) or fresh()) if tp else fresh()
    if tp:
        st = scan(tp, st, cfg)
        save_state(tp, st, all_states)

    custom = st.get('peak', 0.0) + st.get('off', 0.0)
    # Passthrough: Claude Code already computed the USD figure for us.
    usd = (payload.get('cost') or {}).get('total_cost_usd')
    if custom > 0:
        cost_part = f'{GREEN}{money(cfg, custom)}{RESET}'
    elif usd is not None:
        cost_part = f'{GREEN}${usd:.2f}{RESET}'
    else:
        cost_part = f'{GREEN}$0.00{RESET}'

    # Context: prefer Claude Code's own context_window (it knows the real window
    # and used_percentage). used_percentage is null before the first API call.
    cw = payload.get('context_window') or {}
    pct = cw.get('used_percentage')
    if force_win:
        # A different reference window makes Claude's percentage meaningless;
        # mixing them yields "32K/200K … 6.8%" style contradictions.
        window, pct = force_win, None
    elif cw.get('context_window_size'):
        window = cw['context_window_size']
    ctx_est = st.get('ctx', 0)
    if pct is None:
        pct = (ctx_est / window * 100) if window else 0
        used_tok = ctx_est                  # both from the same estimate
    else:
        used_tok = pct / 100 * window       # derive the absolute from the official %
    c = RED if pct >= 80 else (YELLOW if pct >= 50 else GREEN)

    # Cache hit ratio: official prompt_cache.hit_ratio when present, else the
    # transcript tally. Hit tokens cost 1/50 of misses on many providers.
    ratio = (payload.get('prompt_cache') or {}).get('hit_ratio')
    if ratio is None:
        denom = st.get('cacheHit', 0) + st.get('cacheMiss', 0)
        ratio = (st.get('cacheHit', 0) / denom) if denom else None

    parts = [f'{CYAN}{BOLD}{model or "?"}{RESET}', cost_part]

    # ── balance: render from cache only; refresh happens in the background ──
    bal_part = None
    if show.get('balance', True) and cfg.get('balance'):
        bal = load_balance()
        maybe_spawn_refresh(bal, cfg)
        total = None
        try:
            total = float(bal['total'])
        except (KeyError, TypeError, ValueError):
            pass
        if total is not None:
            bts = bal.get('ts') or 0
            if tp and st.get('balTs') == bts and st.get('balBase') is not None:
                # Extrapolate from the last true reading so the balance moves on
                # the same frame as the cost, instead of jumping every TTL. Only
                # apply when cost grew: a shrink means the transcript was
                # rebuilt, and subtracting a negative would inflate the balance.
                try:
                    delta = custom - float(st['balBase'])
                    if delta > 0:
                        total = max(0.0, total - delta)
                except (TypeError, ValueError):
                    pass
            elif tp:
                # First sight of this reading in this session: anchor here.
                st['balBase'], st['balTs'] = custom, bts
                save_state(tp, st, all_states)
            if bal.get('err'):
                bal_part = f'{DIM}{labels["balance"]} {money(cfg, total)}{RESET}'
            else:
                bc = GREEN if total >= 20 else (YELLOW if total >= 5 else RED)
                bal_part = f'{bc}{labels["balance"]} {money(cfg, total)}{RESET}'

    # ── token rate ──
    rate_part = None
    if show.get('token_rate', True):
        rps = None
        if (os.environ.get('CC_RATE_MODE') or cfg.get('rate_mode')) == 'avg':
            api_ms = (payload.get('cost') or {}).get('total_api_duration_ms') or 0
            if api_ms > 0 and st.get('outTok'):
                rps = st['outTok'] / (api_ms / 1000.0)
        if rps is None and st.get('lastMs'):
            rps = st['lastOut'] / (st['lastMs'] / 1000.0)
        if rps is not None:
            rate_part = f'{DIM}≈{rps:.0f} tok/s{RESET}'

    # Absolute value (57K/1M) is the number that visibly moves; on a 1M window the
    # percentage alone looks frozen. Keep one decimal below 10%.
    ctx_abs = f'{fmt_tok(used_tok)}/{fmt_window(window)} ' if used_tok else ''
    pct_s = f'{pct:.1f}' if pct < 10 else f'{pct:.0f}'

    tail = [
        f'{DIM}{fmt_tok(st.get("tok", 0))} tok · {st.get("turns", 0)} {labels["turns"]}{RESET}',
        f'{c}ctx {ctx_abs}{bar(pct)} {pct_s}%{RESET}',
    ]
    if show.get('cache_hit', True) and ratio is not None:
        hp = ratio * 100
        hc = GREEN if hp >= 95 else (YELLOW if hp >= 80 else RED)
        tail.append(f'{hc}hit {hp:.0f}%{RESET}')
    if rate_part:
        tail.append(rate_part)
    if cfg.get('peak'):
        peak_now = peak_multiplier(datetime.now().astimezone(), cfg['peak'],
                                   parse_holidays(cfg['peak'].get('holidays'))) > 1.0
        tail.append(f'{MAGENTA}{labels["peak"]}{RESET}' if peak_now
                    else f'{DIM}{labels["offpeak"]}{RESET}')

    parts.append(bal_part) if bal_part else None
    parts += tail

    name = payload.get('session_name') or ''
    if show.get('session_name', True) and name:
        parts.append(f'{DIM}{clip(name, 36)}{RESET}')

    sys.stdout.write(f' {DIM}│{RESET} '.join(parts) + ' ')


if __name__ == '__main__':
    if '--refresh-balance' in sys.argv:     # background fetch mode: no stdin
        try:
            refresh_balance()
        except Exception:
            pass
        sys.exit(0)
    try:
        main()
    except Exception:
        sys.stdout.write('')                # a status line must never crash
