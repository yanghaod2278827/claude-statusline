# Prior art

Other Claude Code status lines, what they do well, and how this one differs.

**Collected 2026-10-30** from GitHub search and public READMEs. Where a claim
goes beyond what a README states, the source file is cited — two repositories
were inspected directly (`CCometixLine`, `yuchigaocen/claude-code-deepseek-statusline`).
Star counts are a snapshot and will drift.

If you are choosing between these, read the [Which one should you use](#which-one-should-you-use)
section at the end — for some setups another project is the better answer.

---

## The three clusters

### 1. Subscription quota & appearance (the big ones)

| Project | Stars | Stack | Focus |
|:---|---:|:---|:---|
| [Haleclipse/CCometixLine](https://github.com/Haleclipse/CCometixLine) | 3.5k | Rust | 30+ metrics, full-screen configurator, 4 skins, 13 palettes, 8 presets |
| [nilbuild/claude-statusline](https://github.com/nilbuild/claude-statusline) | 1.4k | bash + jq | 5h/7d limits, directory, git branch |
| [GaoSSR/best-claude-hud](https://github.com/GaoSSR/best-claude-hud) | 998 | Rust | Minimal HUD |
| [rz1989s/claude-code-statusline](https://github.com/rz1989s/claude-code-statusline) | 479 | bash | 28 components across 1–9 lines, themes |
| [leeguooooo/claude-code-usage-bar](https://github.com/leeguooooo/claude-code-usage-bar) | 387 | — | 5h/7d usage, reset countdowns |
| [Astro-Han/claude-pace](https://github.com/Astro-Han/claude-pace) | 244 | — | Pace-aware quota monitoring |
| [Wangnov/claude-code-statusline-pro](https://github.com/Wangnov/claude-code-statusline-pro) | 239 | — | Feature-rich, Chinese |

These are excellent at what they do, and what they do is **subscription
bookkeeping**: how much of the 5-hour and 7-day allowance is gone, and how that
pace compares to the clock. Every one of them is worth looking at if you run
Claude Code on a Pro/Max plan.

They all read cost from `payload.cost.total_cost_usd`. That is correct for
Anthropic's own API and wrong everywhere else — see
[the cost problem](#the-cost-problem).

`CCometixLine` does support third-party models, but only for **names and context
windows**, not prices. From `src/models/builtin.rs`:

```rust
pub struct BuiltinRule {
    pub pattern: &'static str,
    pub name: &'static str,
    pub window: u64,
}
```

`models.toml` accepts the same three fields. There is no price field anywhere in
the schema, so cost for a non-Anthropic model has nowhere to come from except the
payload.

### 2. General-purpose, segment-based

[jacob-bd/claude-code-statusline](https://github.com/jacob-bd/claude-code-statusline)
(19 stars, bash + jq) offers 33 toggleable segments, an interactive setup
wizard, multi-line flex layout, and auto-wrapping. Cost is again the native
payload value; there is no price table.

[ilia-pluzhnikov/claude-code-statusline](https://github.com/ilia-pluzhnikov/claude-code-statusline)
(119 stars, Node) is a dependency-free Node implementation with model, tokens
and cost segments.

### 3. DeepSeek-specific

| Project | Stars | Stack | Covers |
|:---|---:|:---|:---|
| [panan3562-svg/claude-deepseek-statusline](https://github.com/panan3562-svg/claude-deepseek-statusline) | 0 | Node | Peak/off-peak badge, cache hit ratio. No cost. |
| [c4llemasy/claude-code-deepseek-statusline](https://github.com/c4llemasy/claude-code-deepseek-statusline) | 0 | Python | Balance via a SessionStart hook, token counts, estimated cost |
| [yuchigaocen/claude-code-deepseek-statusline](https://github.com/yuchigaocen/claude-code-deepseek-statusline) | 1 | Node | Peak countdown, per-message pricing, balance, holiday table, `/ds-peak` and `/ds-usage` commands, self-check |

**`yuchigaocen` is the most complete of these and deserves a direct comparison**,
because it shares two of this project's core ideas and implements them well.

From its source (`scripts/ds-usage.cjs`, `scripts/peak-hours.cjs`):

- **It de-duplicates by `message.id`** — with an extra fallback this project
  doesn't have: when a record has no id, treat an adjacent identical record as a
  duplicate.
- **It prices each message at the rate in force at that message's timestamp**,
  exactly as this project does.
- **It ships a Chinese public-holiday table** (`HOLIDAY_RANGES`), which this
  project only added later, and it warns in the UI when the table has no data
  for the current year.
- Its rate table is correct (`pro: {hit: [0.30, 0.15], miss: [9, 4.5], out: [27, 13.5]}`
  — peak first, off-peak second), and its README correctly calls out the bogus
  `¥3 / ¥6 / ¥0.025` figures that circulate online.

Its models and rates are hardcoded to DeepSeek, and it is a multi-file Node
package installed through a plugin/marketplace flow.

---

## The cost problem

Claude Code computes `cost.total_cost_usd` itself. For models it recognizes
(Anthropic's own) that number is right. For anything else it prices with Claude's
rates, which for a cheap third-party model is off by an order of magnitude.

Measured on a real session on 2026-10-30 — same transcript, same tokens:

| Source | Figure |
|:---|:---|
| `payload.cost.total_cost_usd` | `$45.45` |
| Priced from the provider's official sheet | `￥4.66` |

Roughly **69x apart**. Any status line that displays the payload value directly
shows the first number.

## Feature comparison

Legend: ● yes · ◐ partial · ○ no · ? not stated in the README / not inspected

| | CCometixLine | jacob-bd | yuchigaocen (DeepSeek) | **this** |
|:---|:---:|:---:|:---:|:---:|
| Custom price table for any provider | ○ | ○ | ○ (hardcoded) | **●** |
| Peak / off-peak pricing | ? | ? | ● | ● |
| Public holidays | ○ | ○ | ● | ● |
| Balance + between-poll extrapolation | ○ | ○ | ◐ (shows reading age) | ● |
| `message.id` de-duplication | ? | ? | ● | ● |
| 5h / 7d subscription quota | ● | ● | ○ | ○ (n/a for API) |
| Themes / skins / layout engine | ● | ● | ◐ | ○ |
| Configurator UI | ● | ● | ○ | ○ |
| Interactive commands | ○ | ○ | ● | ○ |
| Single-file, no build step | ○ (binary) | ◐ (bash) | ○ (Node package) | **●** |
| Zero third-party deps | ● | ◐ (needs jq) | ○ | **●** |

`?` means the README doesn't say and the source wasn't inspected. Treat those as
unknown rather than absent.

## What this project actually adds

Being honest about the overlap:

- **The price table is configuration, not code.** This is the one capability no
  other project in this list has. It is what makes the tool work for GLM, Kimi,
  OpenRouter, a regional reseller or a self-hosted gateway without a fork.
- **One file, standard library only.** No `jq`, no Node, no build, no install
  wizard. `curl`-one-file plus a `settings.json` entry.
- **Balance extrapolation.** `yuchigaocen` shows how old the reading is; this one
  subtracts the session's spend since the reading so the balance moves on the
  same frame as the cost counter. Different trade-off, not strictly better.

Where this project is behind:

- No quota tracking (deliberate — it doesn't exist on an API), no themes, no
  configurator, no interactive commands.
- The holiday calendar is shipped as data the user appends to, not as an
  auto-updating table with a UI warning when it is stale.
- `yuchigaocen`'s duplicate detection is more thorough than this one.

## Which one should you use?

- **Anthropic subscription (Pro/Max)** → `CCometixLine` if you want depth and
  looks, `nilbuild` if you want something small.
- **DeepSeek only, and you're happy with Node** → `yuchigaocen` already does
  most of what this project does, plus commands and a holiday table. It is a
  reasonable choice.
- **Any other third-party API**, or you switch providers regularly → this
  project, because it is the only one where the price sheet is a config file.
- **Windows with no `jq`, no Node, no Rust toolchain** → this project: plain
  Python 3 is already a Claude Code prerequisite on most machines.

## Verifying these claims

Everything above is reproducible:

```bash
gh search repos "claude code statusline" --sort stars --limit 25
gh api repos/Haleclipse/CCometixLine/contents/src/models/builtin.rs -H "Accept: application/vnd.github.raw"
gh api repos/yuchigaocen/claude-code-deepseek-statusline/contents/scripts/peak-hours.cjs -H "Accept: application/vnd.github.raw"
curl -s https://api-docs.deepseek.com/zh-cn/quick_start/pricing   # rate sheet
```

Corrections are welcome — open an issue with a source.
