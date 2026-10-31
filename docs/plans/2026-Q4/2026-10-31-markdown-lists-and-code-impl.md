# Lists, code, and forum headings: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New comments, PMs, and forum posts get flat lists, inline code, fenced code blocks, and (forum only) headings; every existing post keeps rendering exactly as today, apart from the `>_<` face fix.

**Architecture:** A `markup_version` column picks the renderer per row. Version 1 is today's `parse_markdown` body, moved to its own module unchanged apart from the face fix. Version 2 is a new block + inline renderer. Both share the escaping, the inline rules (links, bare URLs, bold, italics), and the face test, which move to a shared module. The API exposes `markup_version` on the three response schemas. The frontend adds styles and an e2e spec.

**Tech Stack:** Python 3 / FastAPI / SQLModel / Alembic / Postgres (shuushuu-api); SvelteKit + Svelte 5 + Playwright (shuushuu-frontend).

**Spec:** `docs/plans/2026-Q4/2026-10-30-markdown-lists-and-code-design.md` (read it first; this plan argues from it).

## Global Constraints

- Work in worktrees: API `.worktrees/feat-markdown-lists-and-code` (branch `feat/markdown-lists-and-code` from `origin/main`), frontend `../shuushuu-frontend/.worktrees/feat-markdown-lists-and-code`. Never edit, pull, or check out anything in the main API checkout `/home/dtaylor/shuu/shuushuu-api`; it serves the live dev API. Worktrees have no `.env`: copy it from the main checkout.
- Never `git add -A` in shuushuu-api; add files by path. Never run `npm run format` in the frontend; format only files you touched.
- Version 1 output is byte-identical to `main` for every stored row except rows the face fix changes (merge gate, Task 10).
- Output tags only: `a`, `strong`, `em`, `blockquote`, `small`, `span`, `br`, `ul`, `ol`, `li`, `code`, `pre`, `h2`, `h3`. Attributes only: `href`/`rel`/`target` on `a`, `class="spoiler"` on `span`, `start` on `ol`. No other `<span>`.
- Escape once, first: `html.unescape` then `html.escape` the whole text, then run rules. Link hrefs still go through `is_safe_url`.
- Linear time. Adversarial inputs render in under `RENDER_TIME_LIMIT_SECONDS = 0.5` (the constant already in `tests/unit/test_markdown.py`). Length caps (comments 10,000; forum posts and PMs 20,000) already exist since #432.
- The compatibility report contains private-message text. Never commit it, paste it into a PR, or post it anywhere; give the owner its local path.
- PR descriptions stay neutral: describe behaviour, no exploit recipes.
- Commit messages: conventional prefix (`feat(markdown):`, `refactor(markdown):`, `test:`), ending with the session's attribution lines.

## Review Focus

1. **User text containing the placeholder code points** (U+E000, U+E001) must render as U+FFFD, never as another token's HTML. Test in Task 3.
2. **A bare URL directly followed by a code span or container** (``see http://a.com/`x` ``) links only the URL; the placeholder never ends up inside an `href`. Tests in Tasks 4 and 6.
3. **Huge list numbers** (`"9" * 5000 + ". x"`) are not list items and don't raise (`int()` refuses over 4,300 digits). Test in Task 5.
4. **Whitespace-only and newline-edged text** (`"\n\n"`, `"a\n"`, `"\na"`) renders the same at both versions. Parity corpus, Task 3.
5. **Closers blocked by open spoilers, at depth** (`"[quote]" + "[spoiler]" * 1100 + "[/quote]" * 1100`) stays linear. Test in Task 4.

---

## Files

| File | Responsibility |
| --- | --- |
| `app/utils/markdown.py` | Public API: `parse_markdown(text, version, headings=False)` dispatch; keeps `clean_user_input`, `normalize_legacy_entities`, `normalize_entities`, `strip_markdown` as they are |
| `app/utils/markdown_inline.py` (new) | Shared by both versions: `is_safe_url`, `escape_source`, `render_inline`, `is_face_line`, and today's `_sub_links` / `_LINK_PATTERN` |
| `app/utils/markdown_v1.py` (new) | `render_v1`: today's `parse_markdown` body, moved |
| `app/utils/markdown_v2.py` (new) | `render_v2`: the block + inline renderer |
| `tests/unit/test_markdown.py` | Version 1 tests (existing), plus face-fix and dispatch tests |
| `tests/unit/test_markdown_v2.py` (new) | Version 2 tests, including the v1-parity corpus |
| `alembic/versions/0008_markup_version.py` (new) | The column on three tables |
| `tests/integration/test_markup_version_migration.py` (new) | Existing rows become 1; later inserts get 2 |
| `scripts/markdown_snapshot.py` (new) + `tests/unit/test_markdown_snapshot.py` (new) | Render every stored row; compare two snapshots; group differences by cause |
| Frontend `src/lib/components/features/MarkdownBody.svelte` | Styles for the new elements |
| Frontend `tests/e2e/markdown-formatting.spec.ts` (new) | End-to-end checks |

## Version 2 pipeline (all tasks slot into this order)

1. `escape_source(text)`; replace U+E000 and U+E001 with U+FFFD; `\r\n` and lone `\r` → `\n`.
2. Fenced code blocks → placeholder tokens (Task 7).
3. Code spans → tokens (Task 6).
4. Container scan: matched `[quote]` / `[quote="name"]` / `[spoiler]` pairs → tokens, innermost first, each pair's content rendered by steps 5–6 (Task 4).
5. Block pass over lines, trying per line: a line that is only a fence token → `<pre>`; `>` quote line (unless a face); heading (Task 8); list item (Task 5); otherwise a text line (Task 3).
6. Inline pass: `render_inline` on each piece of text between tokens. Tokens never pass through inline regexes, so bold, italics, and links don't cross a code span or container.
7. Expand tokens.

Placeholders are `"\ue000" + str(index) + "\ue001"`. Because the index is digits, a token counts as "has a letter or digit" for the list rule with no extra code.

**Newlines (version 2).**
- Within a run of text lines, a newline becomes `<br>\n`.
- It stays a bare `\n` when what follows starts a quote (a `>` block or a quote container) or ends a quote container. This is version 1's `\n(?!</?blockquote>)` rule restated, and the parity corpus pins it.
- Next to `ul`, `ol`, `pre`, `h2`, and `h3`, the separator is a bare `\n`, and one blank line directly above and one directly below the element are dropped.

---

### Task 1: Move version 1 into its own module, sharing its inline rules

**Files:**
- Create: `app/utils/markdown_inline.py`, `app/utils/markdown_v1.py`
- Modify: `app/utils/markdown.py`, `tests/unit/test_markdown.py`

**Interfaces:**
- Produces:
  - `markdown_inline.is_safe_url(url: str) -> bool` (moved).
  - `markdown_inline.escape_source(text: str) -> str`: `escape(unescape(text))`, the two lines at the top of today's body.
  - `markdown_inline.render_inline(text: str) -> str`: today's links → bare URLs → bold → italics steps, moved verbatim with `replace_link`, `replace_bare_url`, and the anchor split.
  - `markdown_v1.render_v1(text: str) -> str`: today's body, calling `escape_source` and `render_inline` where that code was.
  - `markdown.parse_markdown(text: str) -> str` (still one argument): returns `render_v1(text)`.

- [ ] **Step 1: Pin the spoiler cases the design lists** in `TestParseMarkdown`:

```python
def test_inline_spoiler(self):
    assert parse_markdown("a [spoiler]b[/spoiler] c") == 'a <span class="spoiler">b</span> c'

def test_multiline_spoiler(self):
    assert parse_markdown("[spoiler]a\nb[/spoiler]") == '<span class="spoiler">a<br>\nb</span>'
```

- [ ] **Step 2: Run them.** `uv run pytest tests/unit/test_markdown.py -v`. Expected: PASS. They pin current behaviour, so passing before the move is correct.
- [ ] **Step 3: Move the code** as the Interfaces block says. Change the test import to `from app.utils.markdown_inline import is_safe_url`, and drop `is_safe_url` from `markdown.py`. Nothing else imports it.
- [ ] **Step 4: Verify.** `uv run pytest tests/unit/test_markdown.py tests/unit/test_schemas.py -v` passes with no test edits besides the import. `uv run mypy app/` and `uv run ruff check app tests` are clean.
- [ ] **Step 5: Commit** `refactor(markdown): move the renderer to markdown_v1, share inline rules`.

### Task 2: The face fix (version 1)

**Files:**
- Modify: `app/utils/markdown_inline.py`, `app/utils/markdown_v1.py`, `tests/unit/test_markdown.py`

**Interfaces:**
- Produces: `markdown_inline.is_face_line(stripped: str) -> bool`, which takes an escaped line already passed through `lstrip()`. It matches:

```python
_FACE_LINE = re.compile(r"&gt;(?:&#?\w+;|[^\s&<\ue000]){0,3}&lt;")
```

  An entity counts as one character. A raw `<` (from rendered spoiler or quote HTML) or a placeholder can't sit inside a face. `render_v1`'s `>` loop treats a line as a quote only when `stripped.startswith("&gt;") and not is_face_line(stripped)`.

- [ ] **Step 1: Write the failing tests** (new class `TestParseMarkdownFaces`):

```python
@pytest.mark.parametrize(
    ("text", "html"),
    [
        (">_< oh no", "&gt;_&lt; oh no"),
        (">.<", "&gt;.&lt;"),
        ("><", "&gt;&lt;"),
        (">'<", "&gt;&#x27;&lt;"),
        ("  >w< hi", "  &gt;w&lt; hi"),
        ("a\n>_<\nb", "a<br>\n&gt;_&lt;<br>\nb"),
    ],
)
def test_face_is_not_a_quote(self, text, html):
    assert parse_markdown(text) == html

@pytest.mark.parametrize(
    ("text", "html"),
    [
        (">____< x", "<blockquote>____&lt; x</blockquote>"),
        ("> quote", "<blockquote>quote</blockquote>"),
        (">[spoiler]x[/spoiler]", '<blockquote><span class="spoiler">x</span></blockquote>'),
    ],
)
def test_quote_lines_stay_quotes(self, text, html):
    assert parse_markdown(text) == html
```

- [ ] **Step 2: Run.** `uv run pytest tests/unit/test_markdown.py -k Faces -v`. Expected: `test_face_is_not_a_quote` fails (renders `<blockquote>_&lt; oh no</blockquote>`), `test_quote_lines_stay_quotes` passes.
- [ ] **Step 3: Implement** `is_face_line` and the one-condition change in `render_v1`.
- [ ] **Step 4: Verify.** The whole `tests/unit/test_markdown.py` passes.
- [ ] **Step 5: Commit** `fix(markdown): a >_< face at the start of a line is not a quote`.

### Task 3: Version 2 baseline: text, `>` quotes, inline rules, line endings

**Files:**
- Create: `app/utils/markdown_v2.py`, `tests/unit/test_markdown_v2.py`

**Interfaces:**
- Consumes: `escape_source`, `render_inline`, `is_face_line` (Tasks 1–2); `render_v1` (tests only).
- Produces: `markdown_v2.render_v2(text: str | None, headings: bool) -> str`, which returns `""` for empty or `None` input. Its internals follow the pipeline above, with stubs left out: steps 2–4 arrive in Tasks 4, 6, and 7.

- [ ] **Step 1: Write the parity test.** Version 2 equals version 1 on text that uses only version 1 syntax and none of the design's expected differences:

```python
PARITY_CORPUS = [
    "This is **bold** text", "*italic*", "**bold** and *italic*",
    "[example](https://example.com)", "[x](javascript:alert(1))",
    "[search](https://example.com/search?q=test&page=2)",
    "see https://example.com/a_(b). ok", "**https://example.com**",
    "> a", "> a\n> b", "> a\nb", "x\n> a", "x\n\n> a",
    "Line 1\nLine 2", "a\n\nb", "a\n", "\na", "\n\n",
    "<script>alert('xss')</script>", "&quot;hi&quot; &amp; bye",
    "*a\nb*", "[link\ntext](https://example.com)",
    ">_< oh no", "a\n>_<\nb", "* nods head* yes", "- -", "- _ -", "- ",
    "I don`t think that`s snow", "`_`", "``x``", "```\na", "1234567890. x",
    "> - a", "#1 fan!", "# x", "#### x",
]

@pytest.mark.parametrize("text", PARITY_CORPUS)
def test_matches_version_1(text):
    assert render_v2(text, headings=False) == render_v1(text)
```

  Later tasks append their own parity cases to `PARITY_CORPUS`.

- [ ] **Step 2: Write the version-2-only tests:**

```python
def test_crlf_is_normalised():
    assert render_v2("a\r\nb\rc", headings=False) == "a<br>\nb<br>\nc"

def test_placeholder_code_points_are_neutralised():
    assert render_v2("x\ue0000\ue001y", headings=False) == "x\ufffd0\ufffdy"

def test_empty():
    assert render_v2("", headings=False) == "" and render_v2(None, headings=False) == ""

@pytest.mark.parametrize("text", ["[" * 40000, "[" * 40000 + "](", "[x](" * 20000])
def test_bracket_runs_are_linear(text):
    start = time.perf_counter()
    render_v2(text, headings=False)
    assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS
```

  Import `RENDER_TIME_LIMIT_SECONDS` from `tests.unit.test_markdown`; don't define a second one.

- [ ] **Step 3: Run.** `uv run pytest tests/unit/test_markdown_v2.py -v`. Expected: FAIL with ImportError on `render_v2`.
- [ ] **Step 4: Implement `render_v2`** with pipeline steps 1, 5 (fence-token, heading, and list branches not yet present), 6, and 7. Follow the newline rules above.
- [ ] **Step 5: Verify.** The v2 tests and `tests/unit/test_markdown.py` pass; mypy is clean.
- [ ] **Step 6: Commit** `feat(markdown): version 2 renderer, matching version 1 on today's syntax`.

### Task 4: Version 2 containers

**Files:**
- Modify: `app/utils/markdown_v2.py`, `tests/unit/test_markdown_v2.py`

**Interfaces:**
- Consumes: `render_v2` internals (Task 3).
- Tag spellings are version 1's, matched on the escaped text: `[quote]`, `[quote=&quot;NAME&quot;]` (NAME has no `&`), `[/quote]`, `[spoiler]`, `[/spoiler]`.
- Output is version 1's:
  - `<blockquote>…</blockquote>`;
  - `<blockquote><small>{escape(name)} said:</small> …</blockquote>`, with the name unescaped first as version 1 does;
  - `<span class="spoiler">…</span>`.
  A named or anonymous quote's content that ends in a newline keeps it as a bare `\n`.

**Scan.**
- Walk the tags left to right with a stack of open tags. Keep, per kind, a stack of positions into it, so that both "nearest open tag of kind K" and "is a spoiler open above it" are O(1).
- A closer pairs with the nearest opener of its kind, unless a spoiler is open above that opener; then the closer is unmatched.
- Openers still open between a closer and its partner are unmatched.
- Each open-stack entry buffers its content as source text plus tokens. When a pair closes:
  1. Merge the unmatched entries above it into its buffer, in order and with their tag text dropped.
  2. Render the buffer with the block and inline passes (and `headings`).
  3. Append the resulting token to the new top's buffer.
- At the end, merge every remaining entry into the root the same way.
- Each tag is pushed and popped once, and each character is rendered once. Nothing recurses.

- [ ] **Step 1: Add parity cases** to `PARITY_CORPUS`:

```python
'[quote="alice"]hi[/quote]', "[quote]a\nb[/quote]", "[quote]\na\n[/quote]",
"x\n[quote]a[/quote]\ny", "[quote]a[/quote]\n[quote]b[/quote]",
'[quote="a"][quote="b"]x[/quote] y[/quote]', "a [spoiler]b[/spoiler] c",
"[spoiler]a\nb[/spoiler]", "[spoiler]a\n[/spoiler]",
"[spoiler]a [spoiler]b[/spoiler] c[/spoiler]", "[spoiler]a [quote] b[/spoiler]",
'[spoiler][quote="a"]x[/quote][/spoiler]', "[spoiler]unclosed", "[/quote] x",
"[quote]unclosed",
```

- [ ] **Step 2: Write the version-2-only tests:**

```python
def test_mis_nested_spoiler_keeps_its_text_hidden():
    assert render_v2("[quote][spoiler]x[/quote][/spoiler]", headings=False) == (
        '<span class="spoiler">x</span>'
    )

def test_unclosed_named_quote_is_stripped():
    assert render_v2('[quote="alice"]unclosed', headings=False) == "unclosed"

def test_missing_inner_closer_pairs_innermost():
    text = '[quote="alice"][quote="bob"]question\nanswer[/quote]\nreply'
    assert render_v2(text, headings=False) == (
        "<blockquote><small>bob said:</small> question<br>\nanswer</blockquote><br>\nreply"
    )

def test_quotes_nest_past_ten():
    text = "[quote]" * 12 + "x" + "[/quote]" * 12
    assert render_v2(text, headings=False) == "<blockquote>" * 12 + "x" + "</blockquote>" * 12

def test_url_before_container_links_only_the_url():
    html = render_v2("see http://a.com/[spoiler]x[/spoiler]", headings=False)
    assert html == (
        'see <a href="http://a.com/" rel="nofollow noopener" target="_blank">http://a.com/</a>'
        '<span class="spoiler">x</span>'
    )

@pytest.mark.parametrize(
    "text",
    [
        "[quote]" * 700 + "[/quote]" * 700,
        "[spoiler]" * 700 + "[/spoiler]" * 700,
        '[quote="x"]' * 700 + "[/quote]" * 700,
        '[quote="x"]' * 5900,
        "[quote]" * 10000,
        "[quote]" + "[spoiler]" * 1100 + "[/quote]" * 1100,
    ],
    ids=["quote-700", "spoiler-700", "named-700", "named-openers", "anon-openers", "blocked-closers"],
)
def test_container_scan_is_linear(text):
    start = time.perf_counter()
    render_v2(text, headings=False)
    assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS
```

- [ ] **Step 3: Run.** Expected: FAIL. The new parity cases and tests render tags as text.
- [ ] **Step 4: Implement** the scan and the rendering of container content.
- [ ] **Step 5: Verify.** All markdown tests pass, and the time-bound tests are under the limit.
- [ ] **Step 6: Commit** `feat(markdown): quotes and spoilers in version 2, nested without recursion`.

### Task 5: Version 2 lists

**Files:**
- Modify: `app/utils/markdown_v2.py`, `tests/unit/test_markdown_v2.py`

**Rules** (the spec's *Lists* section, plus one call of mine):
- Bullet: `^\s*[-*] +(\S.*)$`. Numbered: `^\s*(\d{1,9})\. +(\S.*)$`. The 1–9 digit limit is mine: longer numbers are prose, and it keeps `int()` safe.
- An item's text needs `any(c.isalnum() for c in text)`; a token's digits satisfy this.
- `-` candidates are items. A `*` candidate is an item only when the line directly above or below is a bullet candidate.
- `start="N"` appears when `int(digits) != 1`.
- Item text gets the inline pass.

- [ ] **Step 1: Write the failing tests:**

```python
@pytest.mark.parametrize(
    ("text", "html"),
    [
        ("- a", "<ul><li>a</li></ul>"),
        ("- a\n- b", "<ul><li>a</li><li>b</li></ul>"),
        ("* a\n* b", "<ul><li>a</li><li>b</li></ul>"),
        ("- a\n* b", "<ul><li>a</li><li>b</li></ul>"),
        ("* a*\n* b*", "<ul><li>a*</li><li>b*</li></ul>"),
        ("1. a\n2. b", "<ol><li>a</li><li>b</li></ol>"),
        ("3. a\n7. b", '<ol start="3"><li>a</li><li>b</li></ol>'),
        ("0001. a", "<ol><li>a</li></ol>"),
        ("2012. Coming soon.", '<ol start="2012"><li>Coming soon.</li></ol>'),
        ("- a\n1. b", "<ul><li>a</li></ul>\n<ol><li>b</li></ol>"),
        ("  - a\n    - b", "<ul><li>a</li><li>b</li></ul>"),
        ("- 日本", "<ul><li>日本</li></ul>"),
        ("- **b**", "<ul><li><strong>b</strong></li></ul>"),
        ("- > a", "<ul><li>&gt; a</li></ul>"),
        ("- [spoiler]x[/spoiler]", '<ul><li><span class="spoiler">x</span></li></ul>'),
        ("x\n- a\n- b\ny", "x\n<ul><li>a</li><li>b</li></ul>\ny"),
        ("x\n\n- a\n\ny", "x\n<ul><li>a</li></ul>\ny"),
        ("- a\n\n\ny", "<ul><li>a</li></ul>\n<br>\ny"),
        ("[quote]- a\n- b[/quote]", "<blockquote><ul><li>a</li><li>b</li></ul></blockquote>"),
        ("[quote]\n- a\n[/quote]", "<blockquote><ul><li>a</li></ul></blockquote>"),
    ],
)
def test_lists(text, html):
    assert render_v2(text, headings=False) == html

def test_huge_list_number_is_text():
    text = "9" * 5000 + ". x"
    assert render_v2(text, headings=False) == render_v1(text)

@pytest.mark.parametrize("text", ["- a\n" * 4000, "* a\n" * 4000, "1. a\n" * 4000, "- -\n" * 4000])
def test_many_list_lines_are_linear(text):
    start = time.perf_counter()
    render_v2(text, headings=False)
    assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS
```

- [ ] **Step 2: Run.** Expected: FAIL; list lines render as text.
- [ ] **Step 3: Implement** the list branch of the block pass and the blank-line rule.
- [ ] **Step 4: Verify.** All markdown tests pass. The parity corpus still holds, including `* nods head* yes` and `- -`.
- [ ] **Step 5: Commit** `feat(markdown): flat bulleted and numbered lists in version 2`.

### Task 6: Version 2 inline code

**Files:**
- Modify: `app/utils/markdown_v2.py`, `tests/unit/test_markdown_v2.py`

**Rule** (pipeline step 3, on the escaped text, before the container scan):
- At each backtick *i* that isn't preceded by `\w` or a backtick and isn't followed by a backtick, let *j* be the next backtick.
- It's a span when:
  - *j* is on the same line;
  - `text[j+1]` is neither `\w` nor a backtick;
  - `text[i+1:j]` has an ASCII letter or digit.
- Otherwise backtick *i* is literal, and the scan resumes at *i + 1*.
- Output `<code>{content}</code>`.

- [ ] **Step 1: Write the failing tests:**

```python
@pytest.mark.parametrize(
    ("text", "html"),
    [
        ("a `v` b", "a <code>v</code> b"),
        ("`image_id`", "<code>image_id</code>"),
        ("(`a`)", "(<code>a</code>)"),
        ("`**x**`", "<code>**x**</code>"),
        ("`<b>`", "<code>&lt;b&gt;</code>"),
        ("`[spoiler]x[/spoiler]`", "<code>[spoiler]x[/spoiler]</code>"),
        ("[spoiler]`x`[/spoiler]", '<span class="spoiler"><code>x</code></span>'),
        ("[spoiler]`_`[/spoiler] oh `w`", '<span class="spoiler">`_`</span> oh <code>w</code>'),
        ("- `x`", "<ul><li><code>x</code></li></ul>"),
        ("> use `x`", "<blockquote>use <code>x</code></blockquote>"),
        ("see http://a.com/`x`",
         'see <a href="http://a.com/" rel="nofollow noopener" target="_blank">http://a.com/</a>'
         "<code>x</code>"),
    ],
)
def test_inline_code(text, html):
    assert render_v2(text, headings=False) == html

def test_many_backticks_are_linear():
    for text in ("`" * 20000, "`a" * 10000, "`a`b" * 5000):
        start = time.perf_counter()
        render_v2(text, headings=False)
        assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS
```

  Also add `"x`a` y"` and ``"`a`b`"`` to `PARITY_CORPUS`.

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify.** All markdown tests pass.
- [ ] **Step 5: Commit** `feat(markdown): inline code spans in version 2`.

### Task 7: Version 2 fenced code blocks

**Files:**
- Modify: `app/utils/markdown_v2.py`, `tests/unit/test_markdown_v2.py`

**Rule** (pipeline step 2, after line-ending normalisation):
- Lines are compared after `rstrip()`. An opening fence matches `^```[^\s`]*$`; a closing fence is exactly ```` ``` ````.
- An opener pairs with the next closing line.
- Precompute the index of the last closing line. An opener at or after it is literal, as are all later ones.
- Replace the opener through the closer with one line holding a token for `<pre><code>{lines between, joined by "\n"}</code></pre>`. That means no newline after `<code>` or before `</code>`.

- [ ] **Step 1: Write the failing tests:**

```python
@pytest.mark.parametrize(
    ("text", "html"),
    [
        ("```\nx = 1\n```", "<pre><code>x = 1</code></pre>"),
        ("```python\nx = 1\n```", "<pre><code>x = 1</code></pre>"),
        ("```\na\n\nb\n```", "<pre><code>a\n\nb</code></pre>"),
        ("```\n```", "<pre><code></code></pre>"),
        ("```\n<b>&amp;\n```", "<pre><code>&lt;b&gt;&amp;</code></pre>"),
        ("```\n**x** [spoiler]y[/spoiler] - z\n## h\n```",
         "<pre><code>**x** [spoiler]y[/spoiler] - z\n## h</code></pre>"),
        ("x\n```\nc\n```\ny", "x\n<pre><code>c</code></pre>\ny"),
        ("```\r\nx\r\n```", "<pre><code>x</code></pre>"),
        ("[spoiler]\n```\nx\n```\n[/spoiler]", '<span class="spoiler"><pre><code>x</code></pre></span>'),
    ],
)
def test_code_blocks(text, html):
    assert render_v2(text, headings=False) == html

def test_fence_breaks_a_straddling_spoiler():
    html = render_v2("[spoiler]\n```\n[/spoiler]\n```", headings=False)
    assert "spoiler" not in html.replace("[/spoiler]", "")
    assert "<pre><code>[/spoiler]</code></pre>" in html

def test_unclosed_fences_are_linear():
    text = "```\n" * 5000
    start = time.perf_counter()
    render_v2(text, headings=False)
    assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS
```

  `"```\na"` is already in `PARITY_CORPUS` and must stay there.

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify.** All markdown tests pass.
- [ ] **Step 5: Commit** `feat(markdown): fenced code blocks in version 2`.

### Task 8: Version 2 headings (forum only)

**Files:**
- Modify: `app/utils/markdown_v2.py`, `tests/unit/test_markdown_v2.py`

**Rule:**
- When `headings` is true, a line matching `^\s*(#{2,3}) +(\S.*)$` becomes `<h2>` or `<h3>`, and its text gets the inline pass.
- `headings` reaches container content.

- [ ] **Step 1: Write the failing tests:**

```python
@pytest.mark.parametrize(
    ("text", "html"),
    [
        ("## Title", "<h2>Title</h2>"),
        ("### Sub", "<h3>Sub</h3>"),
        ("  ## Title", "<h2>Title</h2>"),
        ("## **b**", "<h2><strong>b</strong></h2>"),
        ("a\n## T\nb", "a\n<h2>T</h2>\nb"),
        ("[quote]## T[/quote]", "<blockquote><h2>T</h2></blockquote>"),
    ],
)
def test_headings_in_forum_posts(text, html):
    assert render_v2(text, headings=True) == html

@pytest.mark.parametrize("text", ["# x", "#### x", "##x", "###hashtag", "## ", "#1 fan!"])
def test_not_headings(text):
    assert render_v2(text, headings=True) == render_v1(text)

def test_headings_off_by_default():
    assert render_v2("## Title", headings=False) == "## Title"
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify.** All markdown tests pass.
- [ ] **Step 5: Commit** `feat(markdown): forum headings in version 2`.

### Task 9: `markup_version` column

**Files:**
- Create: `alembic/versions/0008_markup_version.py`, `tests/integration/test_markup_version_migration.py`
- Modify: `app/models/comment.py` (`Comments`, not `CommentBase`), `app/models/forum.py` (`ForumPosts`), `app/models/privmsg.py` (`Privmsgs`, not `PrivmsgBase`), `scripts/import_forum_archive.py`, `tests/integration/test_forum_import.py`

**Interfaces:**
- Produces: on each of the three table classes,

```python
markup_version: int = Field(
    default=2, sa_column=Column(SmallInteger, nullable=False, server_default=text("2"))
)
```

- Migration `0008_markup_version`:
  - `down_revision` is whatever `uv run alembic heads` prints when you build it (`0007_forum_import_provenance` today). Don't guess it.
  - Per table, `op.add_column(table, sa.Column("markup_version", sa.SmallInteger(), nullable=False, server_default="1"))`, then `op.alter_column(table, "markup_version", server_default="2")`.
  - `downgrade` drops the three columns.

- [ ] **Step 1: Write the failing migration test.** Copy `tests/integration/test_schema_sync.py`'s scratch-database pattern: `_fresh_database`, and alembic run in a subprocess with `ALEMBIC_DB_URL`. Mark it `pytest.mark.integration` and `pytest.mark.xdist_group("pg_markup_version_migration")`. Use the database `shuushuu_markup_version_pg`.
  1. Upgrade to the previous revision.
  2. Insert one row each into `posts`, `forum_posts`, and `privmsgs` with plain SQL, after `SET session_replication_role = replica` so no parent rows are needed. The test user is the container superuser.
  3. Upgrade to `head`.
  4. Assert all three rows read `markup_version = 1`.
  5. Insert a second row per table with plain SQL that omits the column, and assert it reads 2.

  Test name: `test_existing_rows_become_version_1_and_new_rows_version_2`.
- [ ] **Step 2: Write the ORM default test** in the same file. `test_orm_insert_defaults_to_version_2(db_session)` adds a `Comments(...)` without the field, commits, and asserts `markup_version == 2`.
- [ ] **Step 3: Pin the importer.** In `test_import_one_forum`, add `assert all(p.markup_version == 1 for p in posts)`.
- [ ] **Step 4: Run.** `uv run pytest tests/integration/test_markup_version_migration.py tests/integration/test_forum_import.py -v`. Expected: FAIL (no column / no attribute).
- [ ] **Step 5: Implement.**
  - The migration and the three model fields.
  - `markup_version=1` in the importer's `ForumPosts(...)` call (`scripts/import_forum_archive.py`, the call inside the posts loop).
  - Check `scripts/import_forum_archive.py` for any other `ForumPosts(` or `ForumThreads` opening-post construction and pass 1 there too.
- [ ] **Step 6: Verify.**
  - The Step 4 command passes.
  - `uv run pytest tests/integration/test_schema_sync.py --schema-sync -v` passes, so the models and the chain agree on the `2` default.
  - `uv run mypy app/ scripts/import_forum_archive.py` is clean.
- [ ] **Step 7: Commit** `feat(markdown): markup_version column (existing rows 1, new rows 2)`.

### Task 10: Dispatch and API exposure

**Files:**
- Modify:
  - `app/utils/markdown.py`;
  - `app/schemas/comment.py` (`CommentResponse`), `app/schemas/forum.py` (`ForumPostResponse`), `app/schemas/privmsg.py` (`PrivmsgMessage`);
  - `app/api/v1/forum.py` (`_post_response`), `app/api/v1/privmsgs.py` (the three `data = {…}` dicts).
- Tests:
  - `tests/unit/test_markdown.py`;
  - `tests/unit/test_schemas.py`, `tests/unit/test_privmsg_schemas.py`, `tests/schemas/test_forum.py`;
  - `tests/api/v1/test_comments.py`, `tests/api/v1/test_forum_posts.py`, `tests/api/v1/test_privmsgs.py`.

**Interfaces:**
- Produces: `parse_markdown(text: str | None, version: int, headings: bool = False) -> str`.
  - `version` is required.
  - Version 1 → `render_v1(text)`, ignoring `headings`. Version 2 → `render_v2(text, headings)`. Any other value → `ValueError`.
  - Update the module docstring's feature list.
- Each response class gains a required `markup_version: int`, declared on the response class itself and not on the shared base.
- The computed HTML passes `self.markup_version`. Only `ForumPostResponse` passes `headings=True`.

- [ ] **Step 1: Convert the version 1 tests.**
  - At the top of `tests/unit/test_markdown.py`, add `v1 = partial(parse_markdown, version=1)`.
  - Replace every `parse_markdown(` call in the parse and scaling classes with `v1(`. Change no expected values.
  - Add dispatch tests:

```python
def test_dispatch():
    assert parse_markdown("- a", 1) == "- a"
    assert parse_markdown("- a", 2) == "<ul><li>a</li></ul>"
    assert parse_markdown("## a", 2) == "## a"
    assert parse_markdown("## a", 2, headings=True) == "<h2>a</h2>"
    assert parse_markdown("## a", 1, headings=True) == "## a"
    assert parse_markdown(None, 2) == ""
    with pytest.raises(ValueError):
        parse_markdown("a", 3)
```

- [ ] **Step 2: Write the failing API tests.** Insert old rows with `db_session` as the neighbouring tests do.
  - Comments:
    - `test_new_comment_is_version_2`: POST `"- a\n- b"` → `markup_version == 2` and `post_text_html == "<ul><li>a</li><li>b</li></ul>"`.
    - `test_existing_comment_renders_with_version_1`: a `Comments(markup_version=1, post_text="- a\n- b")` → GET returns `markup_version == 1` and `post_text_html == "- a<br>\n- b"`.
    - `test_editing_keeps_version_1`: PATCH that row to `"- c\n- d"` → `markup_version == 1` and `"- c<br>\n- d"`.
    - `test_comment_renders_no_heading`: POST `"## Title"` → `post_text_html == "## Title"`.
  - Forum posts:
    - `test_new_reply_is_version_2_with_headings`: reply `"## Title"` → `post_text_html == "<h2>Title</h2>"`, `markup_version == 2`.
    - `test_editing_v1_post_keeps_version`: PATCH a `ForumPosts(markup_version=1, …)` → still 1, rendered at version 1.
  - PMs:
    - `test_new_message_is_version_2`: send `"- a"` → `text_html == "<ul><li>a</li></ul>"`.
    - `test_v1_message_renders_v1`: a `Privmsgs(markup_version=1, text="- a")` read through the thread endpoint → `text_html == "- a"`.
- [ ] **Step 3: Run.** `uv run pytest tests/unit/test_markdown.py tests/api/v1/test_comments.py tests/api/v1/test_forum_posts.py tests/api/v1/test_privmsgs.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement** the signature, the schema fields and calls, `_post_response`'s `markup_version=post.markup_version`, and `"markup_version": msg.markup_version` in each privmsgs dict.
- [ ] **Step 5: Fix the hand-built schema instances** in the three schema test files by adding `markup_version=...`. Run the full suite: `uv run pytest tests/ -n auto --dist loadgroup --schema-sync`. A missed construction site fails validation here; fix it at its source. Expected: all pass, output clean. Also run `uv run mypy app/`.
- [ ] **Step 6: Commit** `feat(markdown): render by markup_version and expose it in responses`.

### Task 11: Compatibility snapshot and report

**Files:**
- Create: `scripts/markdown_snapshot.py`, `tests/unit/test_markdown_snapshot.py`

**Interfaces:**
- `uv run python scripts/markdown_snapshot.py render --out FILE [--version N]`
  - Streams every row, ordered by id, to JSONL `{"kind": "comment"|"pm"|"forum", "id": int, "html": str}`.
  - Inputs match the API exactly: comments and PMs pass through `normalize_legacy_entities` first; forum posts render with `headings=True` at version 2.
  - When `inspect.signature(parse_markdown)` has no `version`, it calls `parse_markdown(text)`, so the same file runs on `main`. Use a server-side cursor; it covers 556k comments.
- `uv run python scripts/markdown_snapshot.py compare A B --report FILE [--normalise-newlines]` writes a markdown report.
  - Per kind: the count of changed rows.
  - Rows grouped by cause, each group with up to 20 examples (id, a source excerpt, before, after).
- `normalise_newlines(html: str) -> str`: `<br>\n` → `<br>`, and a `\n` next to `<blockquote>` or `</blockquote>` removed.
- `causes(source: str) -> set[str]` classifies from the source text.
  - Names, one per expected cause in the spec's *Compatibility* list: `list-line`, `star-pair`, `code-span`, `code-block`, `quote-depth`, `quote-first-line-gt`, `inline-across-quote`, `unmatched-named-quote`, `face`.
  - A row matching none is `unexplained`.

- [ ] **Step 1: Write the failing unit tests:**
  - `normalise_newlines` on `"a<br>\nb"`, `"\n<blockquote>x\n</blockquote>\n"`, and `"a\nb"` (unchanged).
  - `causes` on `"- a"` → `{"list-line"}`; `"* a\n* b"` → `{"star-pair", "list-line"}`; ``"`x`"`` → `{"code-span"}`; `"[quote]> x[/quote]"` → `{"quote-first-line-gt"}`; `"plain"` → `set()`.
- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Verify** the tests and `uv run mypy scripts/markdown_snapshot.py`.
- [ ] **Step 5: Produce the gate snapshot.** Write all outputs to the scratchpad, never into the repo.
  - `main`'s output comes from a separate `git worktree add` of `origin/main` with `.env` and the script copied in.
  - Run `render` there, and `render --version 1` on the branch.
  - `compare` the two. Every changed row must be classified `face`; anything else is a bug to fix before going on.
- [ ] **Step 6: Produce the version 2 report.** Run `render --version 2` on the branch, then `compare` it with the branch's version 1 output, using `--normalise-newlines`.
- [ ] **Step 7: Commit** the script and its tests, `feat(scripts): markdown snapshot and comparison`. Then stop and give the owner the two report paths. The API PR merges only after the owner has accepted or sent back every group.

### Task 12: Frontend styles and e2e

Runs after the API PR merges and the owner has updated the dev API (pulled and migrated). The spec needs version 2 rendering from a real API, and the dev API serves the main checkout.

**Files:**
- Modify: `src/lib/components/features/MarkdownBody.svelte` (frontend)
- Create: `tests/e2e/markdown-formatting.spec.ts` (frontend)

**Styles** (`:global` rules beside the existing ones, colours from `layout.css` variables only):
- `ul`/`ol` get `list-style` back (`disc` / `decimal`), with left padding and `0.25rem` vertical margins.
- `code` gets a `--muted` background, a monospace font, and small padding.
- `pre` gets `overflow-x: auto`, `min-width: 0`, `max-width: 100%`, padding, and `var(--radius)`, and its `code` has no background.
- `h2`/`h3` are bold, sized below the thread title's `h1` (the test checks this).
- In `.chat-bubble-sent`, `code` and `pre` use a translucent background and `color: inherit`, so text stays readable on `bg-primary`.

- [ ] **Step 1: Write the failing spec.** Use the real API through `page.request`. Follow `comment-delete.spec.ts` for posting a comment, `helpers/forum.ts` (`getSandboxCategoryId`, `createThreadViaApi`, `replyViaApi`) for forum posts, and `messages-sent-bubble-links.spec.ts` for PMs. Delete the comments it creates at the end. Tests:
  Post texts are given below as lines; build them with `.join('\n')` so no backtick escaping is needed.
  1. `a comment shows lists, inline code, and code blocks`:
     - Post the lines `- one`, `- two`, a blank line, ``use `x` here``, a blank line, ```` ``` ````, `long line`, ```` ``` ````.
     - The comment's `.markdown-body` has 2 `ul > li`, and the `li` computed `list-style-type` is `disc`.
     - It has `code` (not in `pre`) with text `x`, and `pre > code` with text `long line`.
     - `pre`'s computed `overflow-x` is `auto`.
  2. `a heading in a comment stays text`: post `## Not a heading` → no `h2` in its `.markdown-body`; the text is visible.
  3. `a heading in a forum reply sits between body text and the thread title`:
     - Reply with the lines `## Section` and `body text`.
     - The post has an `h2` with text `Section`.
     - Its computed font size is larger than the post's `.markdown-body` font size and smaller than the page `h1`'s.
  4. `a list inside a spoiler blurs and reveals`:
     - Post the lines `[spoiler]`, `- secret one`, `- secret two`, `[/spoiler]`.
     - `.spoiler-content ul li` count is 2, and `.spoiler-content`'s `filter` contains `blur`.
     - After clicking `.spoiler-wrapper`, the filter is `none`.
  5. `a long code line in a sent message scrolls inside its bubble`:
     - Send the lines ```` ``` ````, `"x".repeat(300)`, ```` ``` ````.
     - On the thread page, the `.chat-bubble-sent pre`'s computed `overflow-x` is `auto`, and its `scrollWidth > clientWidth`.
     - Its right edge is within the bubble's right edge.
     - The bubble's width is at most 70% of its parent's width plus 1px.
- [ ] **Step 2: Run.** Run against a preview on this worktree's own `PREVIEW_PORT`, with `set -a; source .env.local; set +a`: `npx playwright test tests/e2e/markdown-formatting.spec.ts`. Expected:
  - Tests 1, 3, and 5 fail on the style assertions: preflight strips list bullets and heading sizes, and `pre` doesn't scroll.
  - Tests 2 and 4 may already pass. They guard behaviour the styles must not break.
- [ ] **Step 3: Implement the styles.**
- [ ] **Step 4: Verify.**
  - The spec passes.
  - `npm run check` shows no new warnings in `MarkdownBody.svelte`.
  - `npx prettier --check` passes on the two files.
  - `npm run lint` is clean.
  - Look at a screenshot of test 1's comment and test 5's bubble in Classic and in one dark theme.
- [ ] **Step 5: Commit** `feat(markdown): style lists, code, and forum headings`, then open the frontend PR naming this plan.

## Rollout

1. The API PR (Tasks 1–11) opens with `Plan: docs/plans/2026-Q4/2026-10-31-markdown-lists-and-code-impl.md`. It merges after CI is green and the owner signs off the report.
2. The owner updates the dev API. Task 12 runs, and the frontend PR merges.
3. Both deploy together, and the API images are rebuilt (the migration runs on deploy).
4. After deploy: close out the plan and write the ADR the spec's *After it ships* section asks for.
