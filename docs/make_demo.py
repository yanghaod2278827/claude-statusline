# -*- coding: utf-8 -*-
"""Render docs/demo.svg from a real status line run (no hand-drawn mockup).

    python3 docs/make_demo.py

Uses a throwaway data dir with a seeded balance cache so the screenshot also
shows the balance segment.
"""
import io, os, sys, json, time, tempfile, subprocess, re, shutil

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'statusline.py')
CFG = os.path.join(ROOT, 'examples', 'deepseek.json')
OUT = os.path.join(ROOT, 'docs', 'demo.svg')

COLORS = {
    '90': '#7d8590', '96': '#22d3ee', '92': '#4ade80',
    '93': '#fbbf24', '91': '#f87171', '95': '#e879f9', '1': 'bold',
}
DEFAULT_FG = '#c9d1d9'
ANSI_RE = re.compile(r'\x1b\[([0-9;]*)m')


def run_statusline():
    tmp = tempfile.mkdtemp(prefix='sl_demo_')
    try:
        # a recent transcript so the numbers look plausible
        tp = os.path.join(tmp, 'demo.jsonl')
        with open(tp, 'w', encoding='utf-8') as f:
            for i in range(40):
                f.write(json.dumps({
                    'type': 'user',
                    'timestamp': '2026-10-30T11:%02d:%02d+08:00' % (i % 60, 0)}) + '\n')
                f.write(json.dumps({
                    'type': 'assistant',
                    'timestamp': '2026-10-30T11:%02d:%02d+08:00' % (i % 60, 3),
                    'message': {'id': 'demo%d' % i, 'model': 'deepseek-flash',
                                'usage': {'input_tokens': 300 + i * 10,
                                          'output_tokens': 1600,
                                          'cache_read_input_tokens': 402000 + i * 1000}}}) + '\n')
        data = os.path.join(tmp, 'data')
        os.makedirs(data, exist_ok=True)
        json.dump({'ts': time.time(), 'total': 200.00, 'cur': 'CNY',
                   'attempt': time.time(), 'err': ''},
                  open(os.path.join(data, 'balance.json'), 'w', encoding='utf-8'))
        payload = {'transcript_path': tp,
                   'model': {'display_name': 'deepseek-flash[1m]'},
                   'session_name': 'claude-statusline 开源',
                   'context_window': {'context_window_size': 1000000, 'used_percentage': 6.4},
                   'prompt_cache': {'hit_ratio': 0.99}}
        env = dict(os.environ)
        env.update({'CLAUDE_STATUSLINE_HOME': data, 'CLAUDE_STATUSLINE_CONFIG': CFG})
        out = subprocess.run([sys.executable, '-S', '-E', SCRIPT],
                             input=json.dumps(payload).encode('utf-8'),
                             capture_output=True, env=env)
        return out.stdout.decode('utf-8', 'replace')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def spans(ansi):
    """ANSI string -> [(text, color, bold)]"""
    out, color, bold, pos = [], DEFAULT_FG, False, 0
    for m in ANSI_RE.finditer(ansi):
        if m.start() > pos:
            out.append((ansi[pos:m.start()], color, bold))
        for code in m.group(1).split(';'):
            if code == '0':
                color, bold = DEFAULT_FG, False
            elif code in COLORS:
                if COLORS[code] == 'bold':
                    bold = True
                else:
                    color = COLORS[code]
            elif code == '':
                color, bold = DEFAULT_FG, False
        pos = m.end()
    if pos < len(ansi):
        out.append((ansi[pos:], color, bold))
    return [(t, c, b) for t, c, b in out if t]


def esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def main():
    line = run_statusline().rstrip()
    parts = spans(line)
    plain = ANSI_RE.sub('', line)
    # how wide is the line? count CJK as 2 columns
    cols = sum(2 if ord(ch) > 0x2000 else 1 for ch in plain)
    fs = 14.0
    cw = fs * 0.62                     # rough monospace advance
    width = int(cols * cw + 48)
    width = max(width, 520)
    height = 84

    tsp = []
    for text, color, bold in parts:
        weight = ' font-weight="600"' if bold else ''
        tsp.append('<tspan fill="%s"%s>%s</tspan>' % (color, weight, esc(text)))
    body = ''.join(tsp)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <clipPath id="win"><rect x="0" y="0" width="{width}" height="{height}" rx="10"/></clipPath>
  </defs>
  <g clip-path="url(#win)">
    <rect width="{width}" height="{height}" fill="#0d1117"/>
    <rect width="{width}" height="26" fill="#161b22"/>
    <circle cx="16" cy="13" r="4.5" fill="#ff5f57"/>
    <circle cx="32" cy="13" r="4.5" fill="#febc2e"/>
    <circle cx="48" cy="13" r="4.5" fill="#28c840"/>
    <text x="{width - 12}" y="17" font-size="10.5" fill="#7d8590" text-anchor="end"
          font-family="'JetBrains Mono','Cascadia Mono',Consolas,monospace">claude statusline</text>
    <text x="20" y="58" font-size="{fs}" xml:space="preserve"
          font-family="'JetBrains Mono','Cascadia Mono',Consolas,'Microsoft YaHei',monospace">{body}</text>
  </g>
</svg>
'''
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(svg)
    print('wrote %s (%d x %d)' % (OUT, width, height))


if __name__ == '__main__':
    main()
