# Lists, code, and forum headings in the markdown parser

Date: 2026-10-30
Status: design approved in discussion; implementation plan follows
Related: `app/utils/markdown.py`; #432 (linear-time quotes and links, post length caps; merged); `<shuushuu-frontend-repo>/src/lib/components/features/MarkdownBody.svelte`

## Motivation

The forum launched recently and its users want more formatting. Lists are the
request that keeps coming up; headings have been mentioned. The project owner
also wants inline code and multi-line code blocks. Comments, private messages,
and forum posts all render through one function, `parse_markdown`, so the new
elements reach all three (headings excepted, below).

`parse_markdown` is a hand-written subset, not a CommonMark library: bold,
asterisk italics, `[text](url)`, bare http(s) URLs, `>` quote lines, BBCode
`[quote]` and `[spoiler]`, and newline → `<br>`. It works as a chain of regex
substitutions over the escaped text, ending with a pass that turns every
remaining newline into `<br>`. It has no notion of a block, so lists and code
blocks can't be added without each one special-casing every other pass. The
function is already marked too complex (`noqa: C901, PLR0915`).

Two facts about existing content shape the design:

- **Kaomoji and ASCII art.** A stock markdown parser would visibly change
  roughly 2–3k of the 556k comments: 857 have a backslash before punctuation
  (`\(>o<)/` would lose its backslash, `¯\_(ツ)_/¯` breaks), 874 have
  underscore pairs that would italicise, about 1,000 use backticks as
  apostrophes or in faces, 109 have a `-----` line that would turn the line
  above into a heading, and 89 have indented lines that would become code
  blocks. None of those rules are adopted here.
- **Old posts were written for today's rules.** 283 comments contain
  `- item` lines (113 of them a single `- ` sign-off line such as
  `- http://saucenao.com`); 81 comments use a line-leading `* nods head*`
  action, which renders as italics today; 21 comments start a line with a
  number and a period that isn't a list item
  (`2012. Coming to a Shuu near you.`). In the forum, 117 of the 118 posts with
  `- ` lines are imported phpBB posts: the importer wrote `[li]` items as `- `
  lines (23 posts have two or more in a row), next to hand-typed sign-offs such
  as `- The Mod Team`. Most of these lines weren't written as lists, and the
  ones that were have always displayed as plain lines. Existing posts therefore
  keep today's rules (see *Markup versions*); only posts written after the
  change get the new ones.

## Decision

Keep the hand-written parser. Write a new one, structured as a block pass and
an inline pass, for posts created from now on. Keep today's parser, unchanged,
for posts that already exist. A `markup_version` column on each post says which
one renders it.

### Approaches considered: the parser

- **A. Add steps to the current regex chain.** Smallest diff, no rewrite. But
  every new pass interacts with the others by order: the final newline pass
  would put `<br>` between list items unless taught another exception (it
  already has one for blockquotes), and a list on the first line of a
  `[quote]` is missed because the quote pass has already prefixed it with
  HTML. Each fix is another special case in a function already flagged too
  complex.
- **B. Block pass, then inline pass (chosen).** The first pass groups lines
  into blocks; the second runs inline rules inside each block. New elements
  slot in without ordering hacks; future elements (images, if ever) are one
  rule. The project owner explicitly approved the rewrite.
- **C. markdown-it-py with most rules disabled.** Rejected. It would still
  need custom rules for asterisk-only italics, apostrophe-safe backticks,
  heading levels, `[quote]`, `[spoiler]`, and http-only autolinks — about as
  much custom code as B, plus a dependency whose defaults fight the content.

### Approaches considered: existing posts

- **Escape the matching text in existing rows.** Rejected. The parser has no
  escape syntax; adding backslash escapes would break the 857 posts with
  `\(^o^)/`-style faces, and invisible marker characters would change stored
  text, which breaks search, editing, and quoting.
- **Apply the new rules everywhere and review the diff.** Simpler: one parser.
  Rejected by the owner: old posts should keep looking the way their authors
  saw them.
- **A markup version per post (chosen).** No stored text changes, and old posts
  render exactly as today. The cost is a column on three tables and keeping the
  old parser alongside the new one.

## Goals

1. Flat lists: bulleted and numbered.
2. Inline code with backticks.
3. Fenced multi-line code blocks.
4. Headings in forum posts only, at two fixed sizes.
5. Every existing comment, private message, and forum post renders exactly as
   it does today, with one deliberate exception: the face fix under *Markup
   versions*.

## Non-goals

- Nested lists. Flat lists cover what's been asked for; nesting is the most
  complex part of list parsing.
- Syntax highlighting. A language name after the opening fence is accepted
  and ignored, so it can be added later.
- Images of any kind (external, proxied, or site-native thumbnail cards).
- Tag descriptions. They stay plain text with the frontend linkifier; they
  may reuse this parser later.
- Composer changes: no formatting help or toolbar. A preview is wanted but is
  a separate change. It would render new text at the current version, and an
  edit at the post's own `markup_version`.
- Moving existing posts to the new rules, including when they're edited.
- Backslash escapes, underscore emphasis, `---`/`===` underline headings,
  indented code blocks, strikethrough, tables.
- Re-running the forum import to map phpBB `[code]` (9 posts) or numbered
  `[list=1]` more faithfully.
- `strip_markdown`. Nothing in `app/` calls it (only its own tests do); this
  work leaves it untouched. Whether to delete it is a separate decision.

## Anti-goals

The work has failed, even if it technically works, if any of these happen:

- An existing post changes appearance (beyond the face fix).
- Kaomoji or ASCII art written in a new post the way people write it today
  (`>_<`, `- -`, `` `_` ``) turns into a list, code, or a quote.
- The text of a matched `[spoiler]…[/spoiler]` pair renders anywhere outside
  the spoiler.
- A forgotten closing fence turns the rest of a post into code.
- Any user input reaches the page as unescaped HTML.

## Markup versions

- A `markup_version` column (`SMALLINT`, not null) on `posts`,
  `forum_posts`, and `privmsgs`. The models declare it with
  `sa_column=Column(SmallInteger, ...)`, as `app/models/ml_raw_prediction.py`
  does; a plain `int` field would map to `INTEGER`.
- One migration adds it with a database default of `1`
  (`op.add_column(..., server_default="1")`), so every existing row becomes
  version 1, then changes the default to `2` (`op.alter_column(...,
  server_default="2")`), so every later insert is version 2 whichever code path
  writes it. Both steps are dialect-neutral.
- The table models also carry a Python-side default of 2, so ORM inserts write
  the version explicitly instead of relying on the database default.
- The version is fixed when the post is created. Editing a comment or forum
  post keeps its version. (Private messages can't be edited.)
- `parse_markdown(text, version, headings=False)` dispatches: version 1 to
  today's parser, version 2 to the new one. `version` is required, for the
  same reason the response field has no default: a caller that forgets it
  fails instead of rendering a new post with the old rules. The three callers
  pass the row's version.
- **Version 1 is today's code as of #432, moved, not rewritten.** It is not
  the new parser with rules switched off: reproducing the old pass order's
  quirks in a block parser (a `>` on a quote's first line stays literal today;
  48 comments and 1 forum post rely on it) is the special-casing approach B
  exists to avoid.
- **The face fix applies to version 1 as well.** A line starting with `>`,
  then at most three non-space characters, then `<` (`>_<`, `>.<`, `>w<`) is a
  face, not a quote line. Characters are counted in the source text, so an
  escaped character (`'` becomes `&#x27;`) counts as one and `>'<` is a face.
  Leading whitespace is handled the same way as in version 1's quote detection
  (`line.lstrip()`). Today 903 comments, 421 private messages, and 12 forum
  posts have such a face at the start of a line, and it is mangled into a
  blockquote holding `_<`. This is the one change to version 1's output.
- Imported forum posts are existing rows, so they are version 1; their `- `
  lines stay plain text, as today. The importer
  (`scripts/import_forum_archive.py`) passes `markup_version=1` explicitly, so a
  later re-run doesn't write phpBB content as version 2.
- Quoting an old post into a new one copies its text into a version 2 post,
  where it renders with the new rules. Accepted.
- The API exposes the version as a required `markup_version: int` on
  `CommentResponse`, `ForumPostResponse`, and `PrivmsgMessage`, the three
  schemas that render `post_text_html` / `text_html`. Nothing reads it yet; a
  later preview or editor will need to know which rules a post renders with.
  The field has no default, so a construction site that forgets it fails
  validation instead of silently claiming a version. It is declared on each
  response class, not on the `CommentBase` / `PrivmsgBase` bases the tables
  share, where it would inherit the table's default. `ForumPostResponse` is
  built by hand in `_post_response`, and `PrivmsgMessage` from dicts in
  `app/api/v1/privmsgs.py`; each of those sites passes it. Tests that build
  these schemas by keyword (`tests/unit/test_schemas.py`,
  `tests/unit/test_privmsg_schemas.py`, `tests/schemas/test_forum.py`) gain the
  field.

## Syntax (version 2)

### Line endings

`\r\n` and a lone `\r` become `\n` right after escaping, before any other rule.
49,208 comments and 56,526 PMs contain `\r`, and recent writes still do; without
this, "a line that is exactly ```` ``` ````" never matches.

### Which rule a line gets

Each line matches at most one block rule, tried in this order: code fence,
`>` quote line (unless it's a face), heading, list item, paragraph line. So
`> - a` is a quote line holding `- a`, and `- > a` is a list item holding
`> a`.

### Lists

- A line starting with `- ` followed by text is a bullet item. A line starting
  with `* ` followed by text is a bullet item only when the line directly above
  or below it is also a bullet item: a lone `* nods head* yes` stays on the
  inline path and renders as italics, as today.
- A line starting with digits, `.`, a space, and text (`1. item`) is a numbered
  item.
- Item text must contain at least one letter or digit (any script), or the
  line is not an item: `- -` and `- _ -` are faces. A code span or container
  already swapped for a placeholder counts as having one, so
  `- [spoiler]x[/spoiler]` and ``- `x` `` are items.
- Leading whitespace before the marker is allowed and ignored (an indented
  item is just another flat item).
- Consecutive item lines form one list. Any other line, including a blank
  one, ends it. Switching between bullets and numbers starts a new list;
  `-` and `*` mix freely within a bullet list.
- A numbered list starts at its first item's number (`<ol start="3">` when it
  isn't 1). Later numbers are ignored. A prose line such as `2012. Coming soon.`
  becomes a one-item `<ol start="2012">`; it renders nearly the same.
- A single `- ` line is a one-item list (a `- name` sign-off gets a bullet).
- Item text gets the inline rules. `* ` consumed as a marker never pairs
  with a later `*` as italics.
- Output: `<ul><li>…</li></ul>` / `<ol><li>…</li></ol>`.

### Inline code

- A single backtick opens a span when it isn't preceded by a word character
  or another backtick; the span closes at the next backtick on the same line
  that isn't followed by a word character or another backtick. The content
  contains no backtick or newline, and **at least one ASCII letter or digit**.
  That first eligible closing backtick decides: if its content has no ASCII
  letter or digit, the opening backtick is plain text and scanning resumes
  after it. The scan never skips ahead to a later closer, which would let
  `` [spoiler]`_`[/spoiler] oh `w` `` swallow `[/spoiler]` and expose the
  spoiler.
- This leaves the apostrophe use (`I don`t think that`s snow`) and backtick
  faces (`` `_` ``, `` `-` ``, `` `(>▂<)´¯` ``) as plain text while `` `sure` ``,
  `` `v` `` and `` `image_id` `` become code. "Word character" is Python's
  Unicode `\w`.
- Content is literal: no other rule (bold, links, URLs, spoilers) applies
  inside it. Output: `<code>…</code>`.
- No double-backtick spans; a run of two or more backticks is literal text.

### Code blocks

- A line that is exactly ```` ``` ````, optionally followed by a language
  name, opens a block; the next line that is exactly ```` ``` ```` closes it.
- An opening fence with no closing fence is not a code block: the fence line
  renders as plain text and the following lines are parsed normally.
- Content is literal and keeps its line breaks; no `<br>` inside. Output:
  `<pre><code>…</code></pre>`, with no newline before `</code>` (a trailing
  newline can show as an extra blank line). The language name is dropped.

### Code first

Fences and code spans are found before anything else, so `[quote]`,
`[spoiler]`, list markers, and `#` inside code are literal. A fence or code span
that straddles a container tag therefore breaks that pair, and the unmatched tag
is stripped; the spoiler guarantee covers matched pairs only.

### Headings (forum posts only)

- `## text` → `<h2>`, `### text` → `<h3>`. A single `#`, or four or more,
  stays plain text — the thread title is the page's `<h1>`, and `#1 fan!`
  must not become a banner.
- The marker needs a space and then text after it: `##text`, `###hashtag`,
  and a bare `## ` stay plain text. Leading whitespace is allowed, as for list
  items.
- Heading text gets the inline rules.
- Enabled by `headings=True`, passed only by the forum post schema
  (`app/schemas/forum.py`), and it applies inside quotes and spoilers in that
  post too. Comments and private messages render `## text` as plain text.
  Version 1 ignores it.
- Built last; it can be dropped from this round without affecting the rest.

### Existing syntax in version 2

- **Containers.** `[quote]`, `[quote="name"]`, and `[spoiler]` tags start
  mid-line and span lines, so they are matched on the escaped text before the
  block pass: one left-to-right scan with a stack, linear in the input.
  - A closing tag pairs with the nearest open tag of its own kind. If a
    `[spoiler]` opened after that tag is still open, the closing tag is
    unmatched instead, so a spoiler is never cut short by another tag:
    `[quote][spoiler]x[/quote][/spoiler]` keeps `x` hidden. (Version 1 emits
    mis-nested `<blockquote><span class="spoiler">x</blockquote></span>`.) Any
    other tag still open between a closing tag and its partner is unmatched:
    in `[spoiler]a [quote] b[/spoiler]` the stray `[quote]` is unmatched and
    the spoiler hides `a  b`.
  - Every unmatched tag is stripped, named `[quote="name"]` openers included.
    (Version 1 leaves an unclosed named opener visible as text, and pairs a
    named opener that is missing an inner closer differently; both differences
    show up in the compatibility report.)
  - Each matched pair's content is parsed with the full block and inline
    rules (so a list or code block inside a quote or spoiler works), and the
    rendered result is spliced back as an opaque token before the block pass
    runs over the surrounding text. Pairs are rendered innermost first, each
    over content whose inner pairs are already tokens, so there is no
    recursion and nesting depth is limited only by the length cap. A
    recursive parse would not do: a balanced `[quote]` costs 15 characters a
    level, so a 10,000-character comment can nest about 660 deep, past
    Python's recursion limit.
  - Rendered markup is unchanged (`<blockquote><small>bob said:</small> …`,
    `<span class="spoiler">…</span>`), and an inline spoiler mid-sentence
    still renders inline.
- **`>` quote lines** keep today's behaviour: consecutive lines join into one
  `<blockquote>` with spaces, and their content gets inline rules only. The
  face rule above applies.
- **Paragraphs.** A paragraph is a run of consecutive plain lines, joined with
  `<br>`, and the inline rules run over the joined text, exactly as today:
  `*a\nb*` is one italic run (2,175 comments and 125 forum posts have italics
  spanning a line break).

## Constraints

- **Escape once, first.** As today: unescape legacy entities, HTML-escape the
  whole text, then run rules. Rules emit only fixed markup plus
  already-escaped text. Link hrefs still go through `is_safe_url`.
- **Fixed tag set.** Output contains only `a`, `strong`, `em`, `blockquote`,
  `small`, `span`, `br`, `ul`, `ol`, `li`, `code`, `pre`, `h2`, `h3`.
  Attributes: `href`/`rel`/`target` on `a`, `class="spoiler"` on `span`,
  `start` on `ol`.
- **No other `<span>`.** The frontend finds spoilers with a regex over
  `<span class="spoiler">…</span>` (`MarkdownBody.svelte`), so the parser must
  not emit any other `span`.
- **Linear time.** `parse_markdown` runs on every response for every comment,
  PM, and forum post. Version 2 must stay roughly linear in input length.
  #432 capped input at 10,000 characters for comments and 20,000 for forum
  posts and PMs, and made version 1's quotes and links linear.
- **API shape only grows.** `post_text_html` / `text_html` stay as they are;
  only the content of new posts gets richer. The one addition is the
  `markup_version` field.

## Compatibility

Proof is a before/after run over real content. A script in `scripts/` renders
every comment, private message, and forum post in the dev database (a prod
restore) and writes `{kind, id, html}` lines. The old output comes from the
unmodified code on `main`, not a copy kept on the branch. The script is
committed so the next parser change can reuse it. It produces two results:

1. **Version 1 is unchanged.** Every row rendered at version 1 on the branch
   is byte-identical to `main`'s output, except rows the face fix changes,
   which are listed separately with their before and after. This is a merge
   gate.
2. **Version 2 over old content.** Every row is also rendered at version 2 and
   compared with its version 1 output. No existing post renders this way; the
   point is to see how the new grammar treats the way people actually write.
   Before comparing, normalise newlines narrowly: `<br>\n` → `<br>`, a `\n`
   next to `<blockquote>` or `</blockquote>` removed. Any other newline
   difference counts, since a newline between inline runs renders as a space.
   Group the changed rows by cause. Causes the design already implies:
   - list lines, including sign-offs, year lines, and two consecutive
     `* action*` lines becoming a two-item list;
   - code spans and code blocks;
   - nested quotes past the old cap;
   - a `>` line at the start of `[quote]` content becoming a blockquote;
   - bold, italics, or links that cross a `>` quote line or a container
     boundary, which version 1's whole-text inline pass allows and version 2's
     per-block pass doesn't;
   - unmatched named `[quote="name"]` tags being stripped, and a named quote
     missing an inner closer pairing innermost-first;
   - tag spellings one scanner accepts and the other doesn't.

   The face fix is not on this list because it applies to both versions. The
   owner reviews every group, expected or not, before the API PR merges, and
   accepts it or sends it back.

## Testing

- **Version 1 stays pinned.** `tests/unit/test_markdown.py` already covers
  bold, italics, links, autolinks, `>` quotes, nested and unclosed
  `[spoiler]`, and (since #432) named, anonymous, nested, and unmatched
  `[quote]`. Before moving the code, add inline and multi-line `[spoiler]`.
  Afterwards they must pass at version 1 with only the version argument added
  (for example a `partial(parse_markdown, version=1)` helper in the test file)
  and no expected output changed. Add tests for the face fix, including `>'<`.
- **Migration.** Existing rows read as version 1; a row inserted afterwards by
  the ORM and by plain SQL is version 2; editing a comment or forum post keeps
  its version; each response renders with its row's version and reports it as
  `markup_version`, including the response to the create request itself.
- **TDD each version 2 element** in `tests/unit/test_markdown.py`, including:
  code inside spoilers and spoilers inside code, lists inside quotes, lone and
  paired `* ` lines, `- -` and backtick faces, apostrophe backticks, the
  `` [spoiler]`_`[/spoiler] oh `w` `` closer case, `- [spoiler]x[/spoiler]` and
  ``- `x` `` as items, `[spoiler]a [quote] b[/spoiler]` and
  `[quote][spoiler]x[/quote][/spoiler]` keeping their spoiler, CRLF input,
  italics spanning lines, unclosed fences, no trailing newline inside
  `<pre><code>`, headings off by default and on inside a forum post's quote,
  and quotes nested past ten rendering fully nested.
- **Adversarial input with time bounds**, at both versions where it applies:
  `'[quote="x"]' * 5900`; balanced nesting at the length cap
  (`'[quote]' * 700 + '[/quote]' * 700`, and the same for `[spoiler]` and named
  quotes), which must not raise; thousands of backticks, unclosed fences,
  unmatched container tags, list markers, `[` runs.
- **Frontend e2e (Playwright).** Post a comment with a list, inline code, and
  a code block; assert the elements render and that list bullets are visible
  (Tailwind's preflight removes `list-style`, so an unstyled list shows no
  bullets). Post a forum reply with `## Heading` and assert an `h2` inside the
  post; the same text in a comment renders no heading.

## Frontend

`MarkdownBody.svelte` styles every rendered post; it needs rules for `ul`,
`ol`, `li`, `code`, `pre`, `h2`, and `h3`, using the theme variables in
`layout.css`. Code blocks scroll horizontally rather than stretching the
page on a phone. Headings stay visibly smaller than the thread title. Check
that a list or code block inside a spoiler still blurs and reveals, and that
code stays readable inside a sent PM bubble (`bg-primary` with
`text-primary-foreground`), where a scrolling `<pre>` in the `max-w-[70%]`
flex bubble needs `min-width: 0`.

## Rollout

Two PRs. The API PR (migration, parser, and the `markup_version` response
field) merges first, then the frontend PR (styles and the e2e spec), so the
spec never sits on `main` ahead of the API it tests. Frontend CI doesn't run
Playwright, so the spec is verified locally against the API branch before
merging. Both deploy together, as usual for a change spanning both repos;
deploying the frontend first is also safe, since the styles are harmless until
version 2 posts exist. After deploy, every existing post is version 1 and
renders as before; the new elements appear as people write new posts.

The frontend's generated API types pick up `markup_version` the next time
frontend code needs it; no frontend code reads it in this change. Whoever
regenerates them adds the field to the hand-built comment fixtures in
`src/routes/home-load-bundled-comments.spec.ts`,
`src/routes/comments/comments-load-concurrent-walks.spec.ts`, and
`src/lib/components/features/CommentCard.spec.ts`, which `svelte-check` will
otherwise reject.

## Left to the builder

- The internal shape of the version 2 passes, and how the two versions'
  code is organised in `app/utils/markdown.py` (version 1 kept as it is apart
  from the face fix).
- Newline handling next to block elements, as long as a block never gets a
  doubled gap above or below it.
- Exact CSS values for the new elements.
- The snapshot script's shape and how it groups changes by cause. To get
  `main`'s output, run the script from a separate `git worktree` of `main`
  (copy the script and `.env` in, run with `uv run`), never from the main
  checkout, which serves the live dev API. `main`'s `parse_markdown` takes no
  version, so the script calls it without one when the signature has none.
- Whether to also speed up version 1's remaining slow path, trimming a
  trailing `)` from bare URLs (about 0.12 s at 20,000 characters), as long as
  its output stays byte-identical.
- Small mechanics:
  - Emit the first code line straight after `<pre><code>`. A browser drops
    a newline right after `<pre>`, but not after `<pre><code>`.
  - Tokenise code spans out before any other inline rule. Placeholders (for
    code spans and containers) use characters no rule matches: no `]`, `)`,
    `*`, or `\n`, so bold can't cross a span, and nothing that could make a
    line look like a list item, heading, `>` line, face, or fence. Private-use
    code points around an index work.
  - Once no closing fence exists after line *i*, none exists after *i+1*;
    mark the rest literal in one go so unclosed fences stay linear.

## After it ships

Record the decision in an ADR: why the parser stays hand-written, which
markdown rules are deliberately missing and why, and why posts are versioned
instead of rewritten. It is the decision most likely to be re-litigated.
