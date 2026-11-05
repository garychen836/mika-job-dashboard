"""Clean a fetched page in code before any model, quote check or reader sees its text.

Prompt injection hides instructions or false facts in words a person cannot see: a display:none div, white or zero-size
text, text pushed off the page, zero-width characters, an HTML comment. A reader that only strips tags keeps all of it.
`clean_html` takes those words out and leaves everything a person would see, in order. No model, no network.

    clean_html(html)    -> (html without the hidden parts, removed)   tags kept, so the existing readers still work
    visible_text(html)  -> (plain text a visitor reads, removed)

JSON-LD blocks (<script type="application/ld+json">) stay in clean_html's output: they are machine data for the
structured readers and are not part of the visible text. Everything else in <script>, <style>, <template>,
<noscript> and comments goes.

`removed` = {"counts": {kind: n}, "snippets": [up to 3 short removed phrases that look like instructions]}.
When a snippet is found one line goes to the ops log (kind "sanitize"); never more page text than those snippets."""

import html as htmllib
import re
from html.parser import HTMLParser

# `screen-reader-text` is NOT removed: the career-site platform some schools use (e.g. YSE) prints its "Date:" / "Time:" labels in it.
# U+200B-U+200F, U+202A-U+202E, U+2060-U+2064, U+FEFF (zero-width and bidi control characters)
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿]")
_INVISIBLE_ENT = re.compile(r"&#(?:x(?:200[b-f]|202[a-e]|206[0-4]|feff)|(?:820[3-7]|823[4-8]|8288|828[89]|8290|8291|8292|8293|8294|65279));?", re.I)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_GONE = {"template", "noscript", "style", "script"}
_IMPLIED = {"p", "li", "dt", "dd", "tr", "td", "th", "option"}      # an unclosed one ends when the next of its kind starts
_HIDE_CLASS = re.compile(r"(?i)(?:^|[\s_-])(?:sr-only|visually-?hidden)(?:$|[\s_-])")
# Worth a log line: wording aimed at a model, anywhere it was hidden (comments included). Ordinary page furniture
# ("You are attending this event", "system", "cancel") must not set it off, or the log fills with false alarms.
_INSTRUCTION = re.compile(r"(?i)\b(?:ignore|disregard|forget)\b[^.]{0,30}\b(?:previous|prior|above|earlier|all)\b[^.]{0,20}\b(?:instruction|message|prompt|rule)s?"
                          r"|system prompt|\byou are now\b|\bas an ai\b|\bnew instructions?\b|\bdo not (?:tell|reveal|mention)\b|\bassistant:")
# A quiet change of a fact, which only counts in hidden page text (not in comments or scripts, where such words are common).
_FACT = re.compile(r"(?i)\brevised\b|\bcorrect(?:ion|ed)\b|\bmoved to\b|\breschedul|\bcancell?ed\b|\bnew (?:date|time|deadline|price)\b")
LOG = None          # set to a function (kind, text, level) by `_log`; tests set it to a no-op


def _px(value: str) -> float | None:
    m = re.fullmatch(r"\s*(-?\d*\.?\d+)\s*(px|pt|em|rem|%|vw|vh)?\s*", value or "")
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2) or "px"
    return {"px": n, "pt": n * 1.33, "em": n * 16, "rem": n * 16, "%": n * 0.16}.get(unit, n)


def _hides(style: str) -> str | None:
    """The reason an inline style hides its element from a person, or None."""
    css = {}
    for decl in (style or "").lower().split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            css[k.strip()] = re.sub(r"\s*!important", "", v).strip()
    g = css.get
    if g("display") == "none":
        return "display:none"
    if g("visibility") in ("hidden", "collapse"):
        return "visibility:hidden"
    if g("opacity") and re.fullmatch(r"0*\.?0*|0%", g("opacity")):
        return "opacity:0"
    fs = g("font-size")
    if fs and _px(fs) is not None and _px(fs) <= 2:
        return "tiny-text"
    col = (g("color") or "").replace(" ", "")
    if col in ("white", "#fff", "#ffffff", "#ffffffff", "transparent") or re.fullmatch(r"rgba?\(255,255,255(,1(\.0*)?)?\)", col) \
            or re.fullmatch(r"rgba\([^)]*,0(\.0*)?\)", col) or re.fullmatch(r"#[0-9a-f]{6}00|#[0-9a-f]{3}0", col):
        return "invisible-colour"
    if g("position") in ("absolute", "fixed"):
        for side in ("left", "top", "right", "bottom"):
            n = _px(g(side) or "")
            if n is not None and n <= -500:
                return "off-screen"
    ti = _px(g("text-indent") or "")
    if ti is not None and ti <= -999:
        return "off-screen"
    clip, cpath = (g("clip") or "").replace(" ", ""), (g("clip-path") or "").replace(" ", "")
    if re.fullmatch(r"rect\(0(px)?,0(px)?,0(px)?,0(px)?\)|rect\(0(px)?(,0(px)?){3}\)", clip) or re.fullmatch(r"inset\(50%\)|inset\(100%\)|circle\(0(px|%)?\)|polygon\(0[^)]*\)", cpath):
        return "clipped"
    zero = lambda k: g(k) is not None and _px(g(k)) == 0
    if (zero("height") or zero("max-height") or zero("width") or zero("max-width")) and (g("overflow") == "hidden" or g("overflow-y") == "hidden" or g("overflow-x") == "hidden"):
        return "zero-box"
    return None


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.stack: list[tuple[str, str | None]] = []       # (tag, reason it is hidden or None)
        self.hidden_depth = 0
        self.counts: dict[str, int] = {}
        self.pending: list[str] = []                        # words inside the hidden element being removed
        self.snips: list[str] = []
        self._ld = False

    def _count(self, kind):
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def _flush(self):
        text = re.sub(r"\s+", " ", htmllib.unescape(" ".join(self.pending))).strip()
        self.pending = []
        m = text and (_INSTRUCTION.search(text) or _FACT.search(text))
        if m and len(self.snips) < 3:
            lo = max(0, m.start() - 30)
            self.snips.append(text[lo:lo + 90])

    def _pop_to(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for _, why in self.stack[i:]:
                    if why:
                        self.hidden_depth -= 1
                del self.stack[i:]
                if self.hidden_depth == 0:
                    self._flush()
                return True
        return False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in _IMPLIED and self.stack and self.stack[-1][0] == tag:
            self._pop_to(tag)
        if tag in _VOID:
            if not self.hidden_depth:
                self.out.append(self.get_starttag_text())
            return
        if tag == "script" and not self.hidden_depth and "ld+json" in a.get("type", "").lower():
            self._ld = True
            self.out.append(self.get_starttag_text())
            self.stack.append((tag, None))
            return
        why = None
        if tag in _GONE:
            why = tag
        elif "hidden" in a and tag != "input":
            why = "hidden-attribute"
        elif a.get("aria-hidden", "").strip().lower() == "true":
            why = "aria-hidden"
        elif a.get("class") and _HIDE_CLASS.search(a["class"]):
            why = "screen-reader-only"
        elif a.get("style"):
            why = _hides(a["style"])
        if why:
            if not self.hidden_depth:
                self._count(why)
            self.hidden_depth += 1
        elif not self.hidden_depth:
            self.out.append(self.get_starttag_text())
        self.stack.append((tag, why))

    def handle_startendtag(self, tag, attrs):
        if tag in _VOID:
            self.handle_starttag(tag, attrs)
        elif not self.hidden_depth:
            self.out.append(self.get_starttag_text())          # an empty <div/>: no words in it either way

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        i = next((k for k in range(len(self.stack) - 1, -1, -1) if self.stack[k][0] == tag), None)
        if i is None:
            return
        visible = not any(why for _, why in self.stack[:i + 1])
        self._pop_to(tag)
        if tag == "script":
            self._ld = False
        if visible:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.hidden_depth:
            if not (self.stack and self.stack[-1][1] in ("script", "style")):
                self.pending.append(data)
        else:
            self.out.append(data)

    def handle_entityref(self, name):
        (self.pending if self.hidden_depth else self.out).append(f"&{name};")

    def handle_charref(self, name):
        (self.pending if self.hidden_depth else self.out).append(f"&#{name};")

    def handle_comment(self, data):
        self._count("comment")
        text = re.sub(r"\s+", " ", data).strip()
        m = _INSTRUCTION.search(text)
        if m and len(self.snips) < 3:
            self.snips.append(text[max(0, m.start() - 30):][:90])

    def handle_decl(self, decl):
        if not self.hidden_depth:
            self.out.append(f"<!{decl}>")

    def unknown_decl(self, data):
        pass


def _log(where: str, removed: dict) -> None:
    if not removed["snippets"]:
        return
    note = LOG
    if note is None:
        try:
            import opslog
            note = opslog.note
        except Exception:
            return
    if note is False:
        return
    try:
        total = sum(removed["counts"].values())
        note("sanitize", f"{where or 'page'}: removed {total} hidden part(s) incl. instruction-like text: " + " | ".join(removed["snippets"]), "warn")
    except Exception:
        pass


def clean_html(html: str, where: str = "") -> tuple[str, dict]:
    """The page without its hidden parts (tags kept) and what was removed. Never raises: on a parser failure the
    text-only fallback strips every tag-like block that could hide text rather than returning the page unchanged."""
    html = html or ""
    removed = {"counts": {}, "snippets": []}
    n_inv = len(_INVISIBLE.findall(html)) + len(_INVISIBLE_ENT.findall(html))
    html = _INVISIBLE.sub("", _INVISIBLE_ENT.sub("", html))
    c = _Cleaner()
    try:
        c.feed(html)
        c.close()
        out = "".join(c.out)
    except Exception:
        out = re.sub(r"(?is)<!--.*?-->|<(script|style|template|noscript)\b.*?</\1>", " ", html)
        c.counts["parser-fallback"] = 1
    if c.hidden_depth:
        c._flush()
    removed["counts"] = dict(c.counts)
    if n_inv:
        removed["counts"]["zero-width"] = n_inv
    removed["snippets"] = c.snips
    _log(where, removed)
    return out, removed


_BLOCK = re.compile(r"(?is)<(script|style|svg)\b[^>]*>.*?</\1>")


def visible_text(html: str, where: str = "") -> tuple[str, dict]:
    """What a visitor reads, as one line of plain text, and what was removed."""
    cleaned, removed = clean_html(html, where)
    text = re.sub(r"<[^>]+>", " ", _BLOCK.sub(" ", cleaned))
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip(), removed
