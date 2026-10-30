#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wire claude-statusline into Claude Code's settings.json.

    python3 install.py                        # generic (USD passthrough)
    python3 install.py --provider deepseek    # also install the DeepSeek config
    python3 install.py --dry-run              # show what would change
    python3 install.py --uninstall            # remove the statusLine entry

Writes ~/.claude/settings.json (backing it up first) and, when asked, copies
examples/<provider>.json to ~/.claude/statusline.json.
"""
import argparse, io, json, os, shutil, sys, time

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, 'statusline.py')
SETTINGS = os.path.expanduser('~/.claude/settings.json')
CONFIG = os.path.expanduser('~/.claude/statusline.json')


def quote(p):
    return '"%s"' % p


def load_settings():
    try:
        with open(SETTINGS, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError as e:
        sys.exit('settings.json is not valid JSON: %s' % e)


def save_settings(data):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    backup = SETTINGS + '.bak-' + time.strftime('%Y%m%d-%H%M%S')
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, backup)
        print('backup: %s' % backup)
    tmp = SETTINGS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, SETTINGS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--provider', choices=['none', 'deepseek', 'anthropic'], default='none',
                    help='also install the matching example config')
    ap.add_argument('--python', default=sys.executable, help='interpreter for the status line')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--uninstall', action='store_true')
    args = ap.parse_args()

    if not os.path.exists(SCRIPT):
        sys.exit('statusline.py not found next to install.py')

    data = load_settings()

    if args.uninstall:
        removed = data.pop('statusLine', None)
        if not removed:
            print('no statusLine entry; nothing to do')
            return
        if args.dry_run:
            print('[dry-run] would remove statusLine: %s' % json.dumps(removed, ensure_ascii=False))
            return
        save_settings(data)
        print('removed statusLine from %s' % SETTINGS)
        return

    command = '%s -S -E %s' % (quote(args.python), quote(SCRIPT))
    entry = {'type': 'command', 'command': command}

    print('settings : %s' % SETTINGS)
    print('command  : %s' % command)

    cfg_src = os.path.join(ROOT, 'examples', '%s.json' % args.provider) \
        if args.provider != 'none' else None
    if cfg_src:
        print('config   : %s -> %s' % (cfg_src, CONFIG))
    else:
        print('config   : none (defaults: USD passthrough)')

    if args.dry_run:
        print('\n[dry-run] no files written')
        return

    if cfg_src:
        if os.path.exists(CONFIG):
            print('note: %s already exists, leaving it untouched' % CONFIG)
        else:
            shutil.copy2(cfg_src, CONFIG)
            print('wrote %s' % CONFIG)

    data['statusLine'] = entry
    save_settings(data)
    print('\ninstalled. Restart Claude Code (or start a new session) to see the status line.')
    print('Uninstall with: python3 install.py --uninstall')


if __name__ == '__main__':
    main()
