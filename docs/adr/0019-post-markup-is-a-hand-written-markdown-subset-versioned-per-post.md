# Post markup is a hand-written Markdown subset, versioned per post

Comments, private messages and forum posts are rendered by our own parser,
`parse_markdown(text, version, headings=False)` in `app/utils/markdown.py`
(api#438, design `docs/plans/2026-Q4/2026-10-30-markdown-lists-and-code-design.md`).
Each row's `markup_version` column (`SMALLINT`, on `posts`, `forum_posts` and
`privmsgs`) chooses the rules:

- **Version 1** (`markdown_v1.py`) is the parser as it stood after api#432,
  moved without rewriting. It applies to every row that existed when migration
  `0008` ran, and to imported phpBB posts.
- **Version 2** (`markdown_v2.py`) runs a block pass and then an inline pass.
  It adds flat lists, inline code, fenced code blocks and, in forum posts
  only, headings. It applies to every row written since.

The two versions share their inline rules in `markdown_inline.py`. The API
returns `markup_version` on `CommentResponse`, `ForumPostResponse` and
`PrivmsgMessage`.

**Editing moves a post to the current rules.** Editing the text of a comment
or forum post sets `markup_version` to `CURRENT_MARKUP_VERSION`. This reverses
the design, which had edits keep the version.

**Version 1 changed in one place, deliberately.** A line starting with `>`,
then at most three non-space characters, then `<` (`>_<`, `>.<`) is a face,
not a quote. Such faces sat at the start of a line in 903 comments, 421
private messages and 12 forum posts, and version 1 mangled each one into a
blockquote.

## What the syntax leaves out

These are left out on purpose:

- **Images:** external, proxied, or thumbnails of site images.
- **Nested lists.**
- **Backslash escapes:** 857 posts contain `\(^o^)/`-style faces, which
  escapes would break.
- **Underscore emphasis.**
- **Underline-style headings** (`---` and `===` under a line).
- **Indented code blocks.**
- **Strikethrough and tables.**
- **Raw HTML:** all input is escaped before any rule runs.
- **Syntax highlighting:** a language name after the opening fence is
  accepted and ignored.

Several rules exist to keep kaomoji and ASCII art as plain text:

- a `* ` line is a list item only next to another bullet line, because 81
  comments use `* nods head*`-style actions;
- a code span needs a letter or digit inside it;
- a heading or list marker needs a space after it.

Headings render only in forum posts:

- `#` and `##` give `<h2>`;
- `###` gives `<h3>`;
- four or more `#` stay plain text.

There is no `<h1>`, because the thread title is the page's `<h1>`. The design
kept a single `#` as plain text. After trying it on dev, the owner asked for
`# text` to render like `## text` (06d3385a). `#1 fan!` stays plain text
because the marker needs a space after it.

## Considered Options

**The parser:**

- **Add steps to the old regex chain.** This would have been the smallest
  change. Rejected because every new pass interacts with the others through
  their order. The newline pass would put `<br>` between list items, and a
  list on the first line of a `[quote]` would be missed. Each fix would be
  another special case in a function already flagged as too complex.
- **A Markdown library (markdown-it-py) with most rules switched off.**
  Rejected. It would still need custom rules for asterisk-only italics,
  backticks that leave apostrophes alone, the heading levels, `[quote]`,
  `[spoiler]`, and links only for http URLs. That is about as much code as
  writing our own parser, plus a dependency whose defaults work against the
  existing posts.
- **A hand-written block pass, then an inline pass (chosen).** New elements
  fit without ordering hacks. The owner approved the rewrite.

**Existing posts:**

- **Escape the text in old posts that the new rules would match.** Rejected.
  The parser has no escape syntax, and adding backslash escapes would break
  the 857 faces. Invisible marker characters would change the stored text,
  which breaks search, editing and quoting.
- **Apply the new rules to every post.** This needs only one parser.
  Rejected by the owner: old posts should keep looking the way their authors
  saw them. 106,338 existing rows would render differently.
- **Version 1 as the new parser with the new rules switched off.** Rejected.
  Reproducing the old pass order's quirks in a block parser is the special
  casing the rewrite exists to avoid. For example, a `>` on a quote's first
  line stays literal in version 1, and 48 comments and 1 forum post rely on
  that.
- **A version per post, with the old parser kept unchanged (chosen).** No
  stored text changes, and old posts render exactly as before. The cost is a
  column on three tables and two parsers to keep.

**Edits:**

- **An edit keeps the post's version.** This was the design. Rejected after
  the build. Someone used to the new syntax would edit an old post and find
  that lists and code don't work, with no way to tell why.
- **An edit moves the post to the current version (chosen).** Edits to old
  posts are rare: about 2% of comment edits come more than 30 days after
  posting. The editor sees the result when they save. Because every post a
  user can edit uses the current rules, the frontend's formatting help
  (shuushuu-frontend#488) never needs to know a post's version.

## Consequences

- **Version 1 is frozen.** Any change to its output changes how old posts
  look. The face fix is the only such change so far.
- **New syntax that would change how existing version 2 posts render needs a
  version 3.** Changing version 2 would rewrite those posts. Raising
  `CURRENT_MARKUP_VERSION` also means raising the column's default, so that
  new posts and edited posts use the same rules.
- **An admin can move a post to the new rules without editing it,** by setting
  its `markup_version` to 2. The column isn't validated: any value other than
  1 or 2 makes `parse_markdown` raise, and every read of that row fails with a
  500.
- **A moderator's edit to someone else's forum post also upgrades it.** The
  author's old text may then render as lists or headings.
- **Quoting an old post copies its text into a new version 2 post,** where the
  new rules apply.
- **Code spans and fences are rendered before quote and spoiler tags.** A
  spoiler's closing tag inside backticks therefore leaves the spoiler
  unmatched, and its text shows outside any spoiler. A later change could make
  code spans give way to spoiler tags.
- **For a few seconds around a deploy,** posts written by the old API after
  the migration flips the default are stored as version 2. In practice those
  are plain text, which renders the same under both versions.
