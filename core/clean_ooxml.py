# -*- coding: utf-8 -*-
"""Cleaning OOXML packages: docx, xlsx, pptx (phase 2).

    clean_ooxml(src, dst, profile=Profile.STEALTH, keep=None) -> CleanResult
    verify_ooxml(src, dst) -> dict

Stdlib only (expat, zipfile, struct) plus the project's image and PDF
cleaners for recursion. python-docx and openpyxl are NOT used: they rebuild
the package and lose parts they don't understand. Unpacking and packing is
core/zipfix.py's job; this module only computes what goes into replace and
drop.

The inspector is the oracle: after cleaning, the dispatcher runs
inspect_file(dst), and that decides whether the file is clean. Our action
lists are not proof.

THE MAIN PRINCIPLE: EDIT, DON'T REBUILD -- and that applies not only to the ZIP
but to the XML too. A part we change is edited BYTE-WISE: expat reports the
offset of every tag, we cut or rewrite only the needed spans (an attribute
value, an element's text, the element itself), and everything else stays the
source bytes. Serializing through lxml/ElementTree would leave a processing
fingerprint on every edited part: lxml moves xmlns declarations before the
attributes (Word puts `mc:Ignorable` among them), replaces `\\r\\n` with `\\n`,
writes `<a/>` instead of `<a></a>`. Verified on real docx/xlsx: Word's
untouched parts would survive an lxml round trip, Excel's would not (the root
of every part and the nested <ext uri=... xmlns:...> in workbook.xml).

Self-test and metrics:
    PYTHONIOENCODING=utf-8 python -m core.clean_ooxml                  synthetic, ~1300 checks
    PYTHONIOENCODING=utf-8 python -m core.clean_ooxml --smoke          project fixtures and pathologies
    PYTHONIOENCODING=utf-8 python -m core.clean_ooxml --real file...   real files (contents and personal
                                                                       data are not printed)

DECISIONS (each deliberate):

* STEALTH KEEPS Application and AppVersion, rsid, the package structure, the
  core.xml dates, TotalTime, cp:revision, app.xml statistics, dc:language,
  theme/fonts/styles. That's the truth about the file, and an empty or
  substituted Application with Word's structure intact is an instant cleaning
  trace. PARANOID removes all PROVENANCE and says honestly in the note that
  the result looks processed.
* dc:creator and cp:lastModifiedBy are BLANKED; the elements stay, empty (this
  is how Word writes them with no user name set). A missing element is a
  trace. A SIDE EFFECT that can't be fixed from this module: the inspector
  treats a pair of empty creator and lastModifiedBy as a kind="scrubbed"
  signal (insp_ooxml._sec_conclusions, two-trace threshold). That's a
  contradiction between the inspector's heuristic and the STEALTH profile
  itself.
* Dates are NOT substituted. core.xml: dcterms:created/modified stay as is
  (STEALTH) or are removed (PARANOID). Revision and comment dates are removed
  (the w:date attribute is optional); those required by the schema (dT of
  threadedComment, created of modern PowerPoint comments) stay and are
  marked KEPT.
* Revision and comment authors -> "Author". That's what Word itself writes
  with "Remove personal information on save" enabled. Revisions can't be
  thrown away: they're content.
* ZIP: zipfix copies untouched entries as raw bytes. The only exception is
  timestamps: if the entries have real times (the file was built by a
  library) and Application claims Microsoft or nothing at all, they're
  normalized to 1980-01-01 00:00:00 like Word's; otherwise the working time
  leaks through the container even with a perfect core.xml (the inspector
  counts that as ENVIRONMENT). A Word file already has 1980-01-01 and isn't
  changed. For other applications (LibreOffice etc.) the real times stay.
* Hidden bytes outside ZIP entries (head, gaps, archive comment) are removed
  (strip_slack=True): Office files have none; it's a classic hiding place.
* Only the listed parts are removed from the package: thumbnail, people.xml,
  printerSettings, LabelInfo.xml, custom.xml/app.xml/core.xml (only if no
  property is left: Office never writes such empty parts, and the inspector
  counts them as a parse error), .rels that became empty, and in PARANOID
  vbaProject and OLE embeddings; with force_signed, signatures.
  [Content_Types].xml loses only the Overrides of removed parts and Defaults
  for extensions no longer in the package.
* External paths in .rels (file:///, C:\\, UNC) are replaced with the FILE
  NAME: formulas and objects rely on such relationships, so removing the
  relationship entirely isn't possible. mailto links and the
  template/mail-merge source are removed together with the element that
  referenced them. These edits go together with the relationship reference in
  the owner's XML (w:hyperlink is unwrapped, the text stays; pageSetup loses
  r:id); if there's no safe way to remove the reference, the part stays and
  this is recorded as UNREMOVABLE.
* A part that needs editing but won't parse is a FAILURE: ok=False, no file is
  handed over. A partially clean file is worse than a refusal (as in
  clean_pdf).
* A signed package (_xmlsignatures/) is refused without force_signed: any edit
  breaks the signature. With force_signed the signatures are removed.
* Everything that can't be removed is honestly marked Act.UNREMOVABLE:
  embedded OLE objects in STEALTH (they have their own docProps inside, and
  OLE2 can't be cleaned in place), Power Query DataMashup, images in formats
  clean_image doesn't take.

Privacy: no logs; errors hold texts without paths. File values are
untrusted: they go through model.clip() and never into a shell. The input is
never modified: sha256 before and after; the output is written to a temp file
next to it and renamed after verification.

keep is the user's whitelist: a set of strings compared case-insensitively
with a field's short key or a part name: "dc:title", "Company", "TotalTime",
"thumbnail", "people", "printerSettings", "docProps/core.xml". Images
understand keep={"icc"}.
"""

from __future__ import annotations

import hashlib
import io
import os
import posixpath
import re
import shutil
import struct
import sys
import tempfile
import urllib.parse
import xml.parsers.expat as expat
import zipfile

from . import sniff, zipfix
from .cleanmodel import Act, CleanResult, Profile
from .insp_ooxml import (_PERSON_NAME_RE, _SP_NAMES, _TC_PLACEHOLDER_RE, _XLCOMMENTS_RE,
                         _device_name, _emails, _has_path)
from .model import clip
from core.i18n import N_, tr

# --- limits ----------------------------------------------------------------
MAX_INPUT = 512 * 1024 * 1024        # same as zipfix and the dispatcher
MAX_XML = 128 * 1024 * 1024          # one XML part we edit
MAX_NESTED = 64 * 1024 * 1024        # one embedded item (image, embedded file)
MAX_DEPTH = 2                        # like MAX_RECURSE_DEPTH of the inspection dispatcher
MAX_ACTS_PER_PART = 40               # actions of one kind per part; beyond that, a single summary line
MAX_XML_DEPTH = 3000                 # element nesting: Office uses dozens; more means a hostile file

W_NSES = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
          "http://purl.oclc.org/ooxml/wordprocessingml/main"}
R_NSES = {"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "http://purl.oclc.org/ooxml/officeDocument/relationships"}
EPOCH = (1980, 1, 1, 0, 0, 0)        # what Word sets on every ZIP entry
AUTHOR = "Author"                    # how Word signs revisions with "remove personal information"

_ANON_ATTRS = {"displayName", "name", "author", "lastModifiedBy"}   # -> Author (parts with comments)
_APP_FLAGS = {"DocSecurity", "ScaleCrop", "LinksUpToDate", "SharedDoc", "HyperlinksChanged"}

# Parts with revision text: headers/footers, footnotes, comments, the AutoText
# glossary. Wider than the inspector's: plus commentsExtensible.xml (it has
# w16cex:dateUtc).
_BODY_RE = re.compile(
    r"^word/(document\d*\.xml|glossary/document\d*\.xml|header\d*\.xml|footer\d*\.xml"
    r"|footnotes\.xml|endnotes\.xml|comments\.xml|commentsExtensible\.xml)$")
# Parts where authors live in attributes, non-Word (Word has its own: _BODY_RE).
_PERSON_RE = re.compile(
    r"^(xl/persons/.+\.xml|xl/threadedComments/.+\.xml|ppt/commentAuthors\.xml|ppt/authors\.xml"
    r"|ppt/comments/.+\.xml|ppt/[^/]+/comments\d*\.xml)$")
_PIVOT_RE = re.compile(r"^xl/pivotCache/pivotCacheDefinition\d*\.xml$")
_CUSTOMXML_RE = re.compile(r"^customXml/item\d*\.xml$")

# customXml roots Office puts in from SharePoint/document management: library
# columns, content type schemas, form templates. The content is blanked down
# to an empty root.
# A NECESSARY DUPLICATE: the same list lives in insp_ooxml._CX_SHAREPOINT_NS,
# and the lists must match. The inspector is the oracle: if it treats as a leak
# a root that isn't listed here, a correctly cleaned file gets the verdict
# "not fully cleaned". A test guards this duplicate.
_SP_ROOT_NS = ("http://schemas.microsoft.com/office/2006/metadata/properties",
               "http://schemas.microsoft.com/office/2006/metadata/contentType",
               "http://schemas.microsoft.com/office/2006/metadata/longProperties",
               "http://schemas.microsoft.com/office/2006/metadata/customXsn",
               "http://schemas.microsoft.com/sharepoint/v3/contenttype/forms")

# Roots that mean document CONTENT: they can't be removed, and they aren't a leak.
# A bibliography is visible to the reader in the text; the names in it are the
# authors of the cited works.
_CONTENT_ROOT_NS = ("http://schemas.openxmlformats.org/officeDocument/2006/bibliography",)
_MACRO_CT = {
    "application/vnd.ms-word.document.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
}
# Password protection attributes: the hash and everything needed to crack it.
_HASH_ATTRS = {"hash", "salt", "cryptProviderType", "cryptAlgorithmClass", "cryptAlgorithmType",
               "cryptAlgorithmSid", "cryptSpinCount", "cryptProvider", "algIdExt",
               "algIdExtSource", "cryptProviderTypeExt", "cryptProviderTypeExtSource",
               "hashValue", "saltValue", "spinCount", "algorithmName",
               "workbookPassword", "workbookPasswordCharacterSet", "workbookHashValue",
               "workbookSaltValue", "workbookSpinCount", "workbookAlgorithmName",
               "revisionsPassword", "revisionsPasswordCharacterSet", "revisionsHashValue",
               "revisionsSaltValue", "revisionsSpinCount", "revisionsAlgorithmName"}
# ODBC/OLEDB connection string keys that carry the server, database and account.
_CONN_SECRET = {"server", "data source", "address", "addr", "network address", "initial catalog",
                "database", "dbq", "uid", "user id", "user", "pwd", "password", "dsn",
                "workstation id", "host", "hostname", "port", "file", "filedsn"}
_CONN_SOURCE_ATTRS = ("connection", "command", "url", "sourceFile", "serverCommand")


class _Bad(Exception):
    """A section failure or refusal: text without paths."""


class _Refuse(Exception):
    """Cleaning refused entirely (signed package etc.): ok=False."""


# ============================================================================
#  XML layer: byte-level editing
# ============================================================================
# Start tag regex: quotes in attribute values are respected (they can contain '>').
_TAG_RE = re.compile(rb"""<[^>"']*(?:(?:"[^"]*"|'[^']*')[^>"']*)*>""")
_ATTR_RE = re.compile(rb"""(\s+)([^\s=/>]+)(\s*=\s*)("([^"]*)"|'([^']*)')""")
_NAME_RE = re.compile(rb"<[^\s/>]+")
_ENC_RE = re.compile(rb"""<\?xml[^>]*encoding\s*=\s*["']([^"']+)["']""")


class _N:
    """An element: tag boundaries in the source bytes, attributes in document order.

    a..b is the start tag, c..d the end tag (c == -1 for an empty tag <x/>)."""

    __slots__ = ("ns", "name", "attrs", "a", "b", "c", "d", "parent", "kids", "_txt", "_sp")

    def __init__(self, ns, name, attrs, a, b):
        self.ns, self.name, self.attrs, self.a, self.b = ns, name, attrs, a, b
        self.c = self.d = -1
        self.parent = None
        self.kids = ()           # lists are created on demand: with a million elements this is memory
        self._txt = ()
        self._sp = None

    @property
    def empty(self):
        return self.c < 0

    @property
    def end(self):
        return self.b if self.c < 0 else self.d

    def get(self, local, ns=None):
        """Value of the first attribute with this local name (and namespace, if given:
        a string, a set, or False for "no namespace")."""
        for ans, aloc, val in self.attrs:
            if aloc != local:
                continue
            if ns is None or (ns is False and not ans) or (ns and ans == ns) \
                    or (isinstance(ns, (set, frozenset)) and ans in ns):
                return val
        return None

    def text(self):
        return "".join(self._txt)


def _enc_ok(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        return False
    m = _ENC_RE.match(raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw)
    return not m or m.group(1).lower().replace(b"_", b"-") in (b"utf-8", b"utf8")


def _parse(raw, want=None, text=False):
    """Parse with expat: a list of elements (only those matching want) with tag boundaries.

    Security: DOCTYPE and entity declarations never occur in OOXML, so their
    presence is a refusal; expat doesn't resolve external entities without a
    handler. Raises _Bad."""
    if not _enc_ok(raw):
        raise _Bad(tr("the part's encoding isn't UTF-8: byte-level editing is impossible"))
    p = expat.ParserCreate(namespace_separator=" ")
    p.ordered_attributes = True
    p.buffer_text = True
    stack, nodes = [], []
    intern = sys.intern

    def no_dtd(*_a):
        raise _Bad(tr("the part has a DOCTYPE or an entity declaration: OOXML never has them"))

    p.StartDoctypeDeclHandler = no_dtd
    p.EntityDeclHandler = no_dtd

    def start(name, attrs):
        if len(stack) > MAX_XML_DEPTH:
            raise _Bad(tr("XML nesting deeper than %d: Office doesn't write files like that") % MAX_XML_DEPTH)
        ns, _, loc = name.rpartition(" ")
        al = []
        for i in range(0, len(attrs), 2):
            ans, _, aloc = attrs[i].rpartition(" ")
            al.append((intern(ans), intern(aloc), attrs[i + 1]))
        ns, loc = intern(ns), intern(loc)
        node = None
        if want is None or want(ns, loc, al):
            a = p.CurrentByteIndex
            m = _TAG_RE.match(raw, a)
            if m is None:
                raise _Bad(tr("could not find the tag boundaries"))
            node = _N(ns, loc, al, a, m.end())
            if raw[node.b - 2:node.b] == b"/>":
                node.c = node.d = -1
            else:
                node.c = 0          # temporarily "not empty"; end() writes the end boundaries
            for anc in reversed(stack):
                if anc is not None:
                    node.parent = anc
                    if not anc.kids:
                        anc.kids = []
                    anc.kids.append(node)
                    break
            nodes.append(node)
        stack.append(node)

    def end(_name):
        node = stack.pop()
        if node is not None and node.c >= 0:
            node.c = p.CurrentByteIndex
            node.d = raw.index(b">", node.c) + 1

    def chars(data):
        for s in stack:
            if s is not None:
                if not s._txt:
                    s._txt = []
                s._txt.append(data)

    p.StartElementHandler = start
    p.EndElementHandler = end
    if text:
        p.CharacterDataHandler = chars
    try:
        p.Parse(raw, True)
    except expat.ExpatError as exc:
        raise _Bad(tr("the XML doesn't parse: %s") % expat.ErrorString(exc.code)) from None
    return nodes


def _well_formed(raw):
    """Independent check of the edit result: expat without handlers and lxml with a strict parser."""
    if not _enc_ok(raw):
        raise _Bad(tr("after the edit the part's encoding isn't UTF-8"))
    try:
        expat.ParserCreate().Parse(raw, True)
    except expat.ExpatError as exc:
        raise _Bad(tr("after the edit the XML doesn't parse: %s") % expat.ErrorString(exc.code)) from None
    try:
        from lxml import etree
        kw = dict(resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False, recover=False)
        if len(raw) <= 4 * 1024 * 1024:
            etree.fromstring(raw, parser=etree.XMLParser(**kw))
        else:                                  # streaming: no 100 MB tree is built
            for _ev, el in etree.iterparse(io.BytesIO(raw), events=("end",), **kw):
                el.clear()
                parent = el.getparent()
                while parent is not None and el.getprevious() is not None:
                    del parent[0]
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        raise _Bad(tr("after the edit the XML fails strict parsing (%s)") % type(exc).__name__) from None


def _esc_attr(s, quote):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("\r", "&#13;").replace("\n", "&#10;").replace("\t", "&#9;")
    return s.replace('"' if quote == 34 else "'", "&quot;" if quote == 34 else "&apos;")


def _esc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Patch:
    """A set of edits to one XML part. Edits accumulate as (start, end, bytes) and
    are applied all at once in finish(); everything outside these spans stays the
    source bytes."""

    def __init__(self, raw, want=None, text=False):
        self.raw = raw
        self.nodes = _parse(raw, want, text)
        self.edits = []

    def by(self, local, ns=None):
        return [n for n in self.nodes if n.name == local and (
            ns is None or n.ns == ns or (isinstance(ns, (set, frozenset)) and n.ns in ns))]

    def _spans(self, n):
        """Attributes of a start tag with byte boundaries. They're matched to n.attrs by
        order: expat returns attributes in document order and doesn't return xmlns
        declarations."""
        if n._sp is None:
            tag = self.raw[n.a:n.b]
            pos = _NAME_RE.match(tag).end()
            found = [m for m in _ATTR_RE.finditer(tag, pos)
                     if m.group(2) != b"xmlns" and not m.group(2).startswith(b"xmlns:")]
            if len(found) != len(n.attrs):
                raise _Bad(tr("could not match the tag's attributes"))
            n._sp = [(ans, aloc, val, n.a + m.start(), n.a + m.end(), n.a + m.start(4) + 1,
                      n.a + m.end(4) - 1)
                     for (ans, aloc, val), m in zip(n.attrs, found)]
        return n._sp

    def _find(self, n, key):
        ns, loc = key if isinstance(key, tuple) else (None, key)
        for sp in self._spans(n):
            if sp[1] == loc and (ns is None or sp[0] == ns or
                                 (isinstance(ns, (set, frozenset)) and sp[0] in ns)):
                return sp
        return None

    def set_attr(self, n, key, value):
        sp = self._find(n, key)
        if sp is None:
            raise _Bad(tr("attribute %s is not in the tag") % (key[1] if isinstance(key, tuple) else key))
        q = self.raw[sp[5] - 1]
        self.edits.append((sp[5], sp[6], _esc_attr(value, q).encode("utf-8")))

    def del_attr(self, n, key):
        sp = self._find(n, key)
        if sp is None:
            return False
        self.edits.append((sp[3], sp[4], b""))
        return True

    def set_text(self, n, text):
        """Replace the content between the tags. An empty tag <x/> becomes <x>text</x>."""
        body = _esc_text(text).encode("utf-8")
        if n.empty:
            if not text:
                return
            qn = _NAME_RE.match(self.raw[n.a:n.b]).group(0)[1:]
            self.edits.append((n.a, n.b, self.raw[n.a:n.b - 2].rstrip() + b">" + body
                               + b"</" + qn + b">"))
        else:
            self.edits.append((n.b, n.c, body))

    def remove(self, n):
        self.edits.append((n.a, n.end, b""))

    def unwrap(self, n):
        """Remove the element itself, keeping its content (w:hyperlink)."""
        if n.empty:
            self.remove(n)
        else:
            self.edits.append((n.a, n.b, b""))
            self.edits.append((n.c, n.d, b""))

    def finish(self):
        out, pos = [], 0
        prev = None
        for a, b, rep in sorted(set(self.edits), key=lambda e: (e[0], -e[1])):
            if prev is not None and a < prev[1]:
                if b <= prev[1] and not prev[2]:
                    continue                      # an edit inside a removed span
                raise _Bad(tr("XML edits overlap"))
            out.append(self.raw[pos:a])
            out.append(rep)
            pos = b
            prev = (a, b, rep)
        out.append(self.raw[pos:])
        res = b"".join(out)
        _well_formed(res)
        return res


# ============================================================================
#  Package model: part names, relationships, removing parts with their references
# ============================================================================


def _type_tail(typ):
    return typ.rsplit("/", 1)[-1]


def _rels_owner(rn):
    """word/_rels/document.xml.rels -> word/document.xml; _rels/.rels -> "" (the whole package)."""
    head, _, base = rn.rpartition("/")
    if head == "_rels" or head.endswith("/_rels"):
        head = head[:-5].rstrip("/")
    base = base[:-5] if base.endswith(".rels") else base
    return "/".join(x for x in (head, base) if x)


def _rels_name(part):
    """word/document.xml -> word/_rels/document.xml.rels; "" -> _rels/.rels."""
    head, _, base = part.rpartition("/")
    return (head + "/" if head else "") + "_rels/" + base + ".rels"


def _resolve(rn, target):
    """Name of the part the internal Target of a relationship from rn points to. None if it doesn't resolve."""
    base = posixpath.dirname(_rels_owner(rn))
    t = urllib.parse.unquote(target.replace("\\", "/"))
    p = posixpath.normpath(t.lstrip("/") if t.startswith("/") else posixpath.join(base, t))
    return None if p.startswith("..") or p in (".", "") else p


def _relativize(target):
    """file:///C:/Users/x/Dir/book.xlsx#frag -> book.xlsx#frag. Empty if there's no file name
    (a directory or a bare share name): then the link has to be removed entirely."""
    t, sep, frag = target.partition("#")
    if not t.strip() or t.strip()[-1] in "/\\":
        return ""
    seg = [s for s in re.split(r"[\\/]+", t) if s]
    if not seg or "." not in seg[-1] or re.fullmatch(r"[A-Za-z]:", seg[-1]):
        return ""
    return seg[-1] + (sep + frag if sep else "")


def _is_pathlike(target):
    return _has_path(target) or target.lower().startswith("file:")


def _ole_target(n):
    """What to cut together with a reference to an embedded object: w:object (Word) or graphicFrame
    (PowerPoint), and if it's wrapped in mc:AlternateContent, the whole wrapper (it has a duplicate in Fallback)."""
    a, hops = n, 0
    while a is not None and hops < 6:
        a = a.parent
        hops += 1
        if a is not None and ((a.name == "object" and a.ns in W_NSES) or a.name == "graphicFrame"):
            p = a.parent
            if p is not None and p.name in ("Choice", "Fallback") and p.parent is not None \
                    and p.parent.name == "AlternateContent":
                return p.parent
            return a
    return None


def _ref_action(n, aloc):
    """What to do with an element referencing a relationship being removed. None if we can't do it safely."""
    nm = n.name
    if nm == "hyperlink":
        return "unwrap" if n.ns in W_NSES else "remove"
    if nm in ("hlinkClick", "hlinkHover", "hlinkMouseOver", "attachedTemplate"):
        return "remove"
    if nm == "pageSetup" and aloc == "id":
        return "dropattr"
    if nm in ("OLEObject", "oleObj", "oleObject") and _ole_target(n) is not None:
        return "container"
    return None


_SCRUB_WANT = {"hyperlinks", "hyperlink", "oleObjects", "object", "graphicFrame",
               "AlternateContent", "Choice", "Fallback"}


class _Pkg:
    """Cleaning state of one package: the source ZIP for reading, accumulated replacements and removals."""

    def __init__(self, res, zf, profile, keep, depth, tmp, force_signed=False):
        self.res, self.zf = res, zf
        self.profile = profile
        self.paranoid = profile is not Profile.STEALTH
        self.keep = {str(k).strip().casefold() for k in ([keep] if isinstance(keep, str) else (keep or ()))}
        self.depth, self.tmp, self.force_signed = depth, tmp, force_signed
        infos = zf.infolist()
        self.names = [i.filename for i in infos]
        self.nameset = set(self.names)
        self.info = {i.filename: i for i in infos}
        self.cur = {}              # changed parts: name -> new bytes
        self.drop = set()
        self.rels = {}             # .rels name -> [(Id, Type, Target, TargetMode)]
        self.idents = set()        # this document's personal strings (lowercase)
        self.appname = ""          # Application from the source app.xml
        self.nested_n = 0

    # --- helpers ----------------------------------------------------
    def act(self, a, loc, label, before="", after="", note=""):
        return self.res.act(a, loc, label, before, after, note)

    def held(self, loc, key):
        """The user asked to keep the field: record KEPT and ask to leave it alone."""
        if self.keep and (key.casefold() in self.keep or loc.casefold() in self.keep):
            self.act(Act.KEPT, loc, key, note=tr("kept per the user's whitelist (keep)"))
            return True
        return False

    def add_ident(self, value):
        v = " ".join(str(value or "").split())
        if len(v) >= 6:
            self.idents.add(v.casefold())

    def leaky(self, value):
        """The value looks like personal data: an email, a path, or a string already found as personal."""
        if not value:
            return False
        low = value.casefold()
        return bool(_emails(value)) or _has_path(value) or any(i in low for i in self.idents)

    def exists(self, name):
        return name in self.nameset and name not in self.drop

    def raw(self, name, limit=None):
        if name in self.cur:
            return self.cur[name]
        limit = MAX_XML if limit is None else limit
        info = self.info.get(name)
        if info is None:
            raise _Bad(tr("part %s is not in the package") % name)
        if info.file_size > limit:
            raise _Bad(tr("part %s is too large (%d MB): editing is impossible") % (name, info.file_size >> 20))
        return self.zf.read(info)

    def patch(self, name, want=None, text=False, raw=None):
        try:
            return _Patch(self.raw(name) if raw is None else raw, want, text)
        except _Bad as exc:
            raise _Bad("%s: %s" % (name, exc)) from None

    def commit(self, name, patch):
        """Apply the accumulated edits. True if the part changed."""
        if not patch.edits:
            return False
        try:
            new = patch.finish()
        except _Bad as exc:
            raise _Bad("%s: %s" % (name, exc)) from None
        if new == patch.raw:
            return False
        self.cur[name] = new
        return True

    def values(self, name, locals_):
        """Values of attributes with the given local names across the whole part (read only)."""
        out = []
        try:
            for n in _parse(self.raw(name), lambda _ns, _l, a: any(x[1] in locals_ for x in a)):
                out += [v for _, loc, v in n.attrs if loc in locals_ and v]
        except _Bad:
            pass
        return out

    # --- relationships ----------------------------------------------------------
    def index_rels(self):
        self.rels = {}
        for rn in self.names:
            if rn.endswith(".rels") and rn not in self.drop:
                self.rels[rn] = self._read_rels(rn)

    def _read_rels(self, rn):
        return [(n.get("Id") or "", n.get("Type") or "", n.get("Target") or "", n.get("TargetMode") or "")
                for n in self.patch(rn).by("Relationship")]

    def rel_target(self, owner, rid):
        for r in self.rels.get(_rels_name(owner), ()):
            if r[0] == rid:
                return r[2]
        return None

    def rels_remove(self, rn, rids):
        rids = set(rids)
        P = self.patch(rn)
        hit = [n for n in P.by("Relationship") if n.get("Id") in rids]
        if not hit:
            return
        if len(P.by("Relationship")) == len(hit):
            # Word never writes an empty .rels: the part is removed entirely.
            self.drop.add(rn)
            self.cur.pop(rn, None)
            self.rels.pop(rn, None)
            return
        for n in hit:
            P.remove(n)
        self.commit(rn, P)
        self.rels[rn] = [r for r in self.rels.get(rn, ()) if r[0] not in rids]

    def rels_set_target(self, rn, rid, new):
        P = self.patch(rn)
        for n in P.by("Relationship"):
            if n.get("Id") == rid:
                P.set_attr(n, "Target", new)
        self.commit(rn, P)
        self.rels[rn] = [(a, b, new if a == rid else c, d) for a, b, c, d in self.rels.get(rn, ())]

    def incoming(self, name):
        return [(rn, rid, typ) for rn, lst in self.rels.items() for rid, typ, tgt, mode in lst
                if mode != "External" and _resolve(rn, tgt) == name]

    def plan_refs(self, owner, rids):
        """What to cut from the owner part's XML so the relationships being removed aren't left dangling.
        Returns (patch, plan); patch is None if the XML has no references at all (decided from the
        bytes, without parsing: for a document of hundreds of megabytes that's the difference between
        seconds and a minute). False means one of the references can't be removed safely: then
        nothing is touched."""
        try:
            raw = self.raw(owner)
        except _Bad:
            return False                    # the part is too large to edit: there's no way to remove the reference
        if not any(q + r.encode("utf-8") + q in raw for r in rids for q in (b'"', b"'")):
            return None, []

        def want(_ns, loc, attrs):
            return loc in _SCRUB_WANT or any(a in R_NSES and v in rids for a, _l, v in attrs)

        P = self.patch(owner, want, raw=raw)
        plan = []
        for n in P.nodes:
            for ans, aloc, val in n.attrs:
                if ans in R_NSES and val in rids:
                    act = _ref_action(n, aloc)
                    if act is None:
                        return False
                    plan.append((n, act))
        return P, plan

    def apply_refs(self, owner, P, plan):
        if P is None:
            return
        gone, per_parent = set(), {}
        for n, act in plan:
            if id(n) in gone:
                continue
            if act == "unwrap":
                P.unwrap(n)
            elif act == "dropattr":
                P.del_attr(n, (R_NSES, "id"))
            else:
                t = n if act == "remove" else _ole_target(n)
                if id(t) in gone:
                    continue
                P.remove(t)
                gone.add(id(t))
                if t.parent is not None:
                    per_parent.setdefault(id(t.parent), [t.parent, 0])[1] += 1
        for par, cnt in per_parent.values():
            if par.name in ("hyperlinks", "oleObjects") and cnt == len(par.kids):
                P.remove(par)
        self.commit(owner, P)

    def unlink(self, rn, rids):
        """Remove relationships and the references to them from the owner's XML. Returns the set of rIds
        that could NOT be removed (the element referencing the relationship can't be cut safely): their
        relationships stay untouched."""
        rids = set(rids)
        owner = _rels_owner(rn)
        failed = set()
        if owner and self.exists(owner) and owner.endswith((".xml", ".vml")):
            plan = self.plan_refs(owner, rids)
            if plan is not False:
                self.apply_refs(owner, *plan)
            else:                                   # one of the references can't be removed: find out one by one
                for rid in sorted(rids):
                    one = self.plan_refs(owner, {rid})
                    if one is not False:
                        self.apply_refs(owner, *one)
                    else:
                        failed.add(rid)
        self.rels_remove(rn, rids - failed)
        return failed

    def drop_part(self, name):
        """Remove a part together with the references to it. True if removed. False if the reference in
        the owner's XML can't be removed safely: the part stays and nothing is touched."""
        if name in self.drop:
            return True
        by_rn, by_owner, plans = {}, {}, []
        for rn, rid, typ in self.incoming(name):
            by_rn.setdefault(rn, set()).add(rid)
            owner = _rels_owner(rn)
            # A reference to a relationship from the owner's XML can exist for any type (printerSettings
            # has pageSetup/@r:id in Excel, oleObject has the object itself). Whether there is one is
            # decided by a byte search in plan_refs; if there is one and it can't be removed, the part stays.
            if owner and self.exists(owner) and owner.endswith((".xml", ".vml")):
                by_owner.setdefault(owner, set()).add(rid)
        for owner, rids in by_owner.items():
            plan = self.plan_refs(owner, rids)
            if plan is False:
                return False
            plans.append((owner,) + plan)
        for owner, P, plan in plans:
            self.apply_refs(owner, P, plan)
        for rn, rids in by_rn.items():
            self.rels_remove(rn, rids)
        self.drop.add(name)
        self.cur.pop(name, None)
        own = _rels_name(name)
        if own in self.nameset and own not in self.drop:      # the part had its own .rels
            self.drop.add(own)
            self.cur.pop(own, None)
            self.rels.pop(own, None)
        return True

    def result(self):
        """(replace, drop) for zipfix.rebuild."""
        rep = {n: b for n, b in self.cur.items() if n in self.nameset and n not in self.drop}
        return rep, set(self.drop)


# ============================================================================
#  Cleaning sections. Each runs in its own try (see _step): one failing doesn't take down
#  the others, but any failure makes the result ok=False: a partially clean file is worse
#  than a refusal.
# ============================================================================


def _step(pkg, name, fn):
    try:
        fn(pkg)
    except _Refuse:
        raise
    except _Bad as exc:
        pkg.act(Act.FAILED, name, name, note=tr("section failed: %s") % exc)
        pkg.res.err(tr("Section \"%s\": %s") % (name, exc))
    except Exception as exc:  # noqa: BLE001 -- people send us broken files on purpose
        pkg.act(Act.FAILED, name, name, note=tr("section failed (%s), details in errors") % type(exc).__name__)
        pkg.res.err(tr("Section \"%s\": %s: %s") % (name, type(exc).__name__, exc))


def _descendants(n):
    for k in n.kids:
        yield k
        yield from _descendants(k)


def _drop(pkg, name, key, label, before, note):
    """Remove a part entirely. If the reference to it from the owner's XML can't be removed safely,
    the part stays, and this is honestly recorded as UNREMOVABLE."""
    if pkg.held(name, key):
        return
    if pkg.drop_part(name):
        pkg.act(Act.REMOVED, name, label, before, note=note)
    else:
        pkg.act(Act.UNREMOVABLE, name, label, before,
                note=tr("The reference to the part from the owner's XML could not be removed "
                        "safely, so the part was kept. ") + note)


# --- 0. signature and the document's personal strings -------------------------------------


def _sec_signature(pkg):
    sig = [n for n in pkg.names if n.startswith("_xmlsignatures/")]
    if not sig:
        return
    if not pkg.force_signed:
        raise _Refuse(tr("The package is signed (%d parts in _xmlsignatures/): any edit "
                         "invalidates the signature. File NOT cleaned; to clean it anyway and "
                         "remove the signature, force_signed is required.") % len(sig))
    origin = [n for n in sig if n.endswith(".sigs")]
    for n in origin:
        pkg.drop_part(n)
    for n in sig:
        if n not in pkg.drop:
            pkg.drop.add(n)
            pkg.cur.pop(n, None)
    pkg.act(Act.REMOVED, "_xmlsignatures/", tr("Package digital signature"), tr("%d parts") % len(sig),
            note=tr("The signature doesn't survive a metadata edit, so it was removed entirely "
                    "(force_signed)."))


def _sec_idents(pkg):
    """Collect the document's personal strings BEFORE editing: they're needed to find the same
    strings in the title, subject and keywords (the inspector counts such a match as a leak)."""
    for name, keys in (("docProps/core.xml", ("creator", "lastModifiedBy")),
                       ("docProps/app.xml", ("Company", "Manager", "Application"))):
        if pkg.exists(name):
            for n in _parse(pkg.raw(name), None, True):
                if n.name in keys:
                    if n.name == "Application":
                        pkg.appname = n.text().strip()
                    else:
                        pkg.add_ident(n.text())
    if pkg.exists("word/people.xml"):
        for v in pkg.values("word/people.xml", {"author", "userId"}):
            pkg.add_ident(v)
    if pkg.exists("word/comments.xml"):
        for v in pkg.values("word/comments.xml", {"author"}):
            pkg.add_ident(v)
    for name in pkg.names:
        if pkg.exists(name) and _PERSON_RE.match(name):
            for v in pkg.values(name, _ANON_ATTRS | {"userId", "email"}):
                pkg.add_ident(v)
    if pkg.exists("docProps/custom.xml"):
        for n in _parse(pkg.raw("docProps/custom.xml"), None, True):
            if n.name == "property" and (_emails(n.text()) or _PERSON_NAME_RE.search(n.get("name") or "")):
                pkg.add_ident(n.text())


def _emptied(P, root):
    """All child elements of the root were cut (and there were some). The inspector counts an empty
    core.xml/app.xml as a parse error, and Office never writes them like that: the part is then
    removed entirely."""
    gone = set(P.edits)
    return bool(root.kids) and all((k.a, k.end, b"") in gone for k in root.kids)


def _drop_emptied(pkg, name, P, root, why):
    if _emptied(P, root) and pkg.drop_part(name):
        pkg.act(Act.REMOVED, name, name, "", "", tr("After cleaning no property is left in the "
                                                    "part: %s") % why)
        return True
    return False


# --- A. docProps/core.xml ------------------------------------------------------

# local name -> (key, label, STEALTH, PARANOID); prov: keep if there's no personal data
_CORE_RULES = {
    "creator": ("dc:creator", N_("author"), "blank", "blank"),
    "lastModifiedBy": ("cp:lastModifiedBy", N_("last saved by"), "blank", "blank"),
    "created": ("dcterms:created", N_("created"), "keep", "remove"),
    "modified": ("dcterms:modified", N_("modified"), "keep", "remove"),
    "revision": ("cp:revision", N_("revision number"), "keep", "remove"),
    "lastPrinted": ("cp:lastPrinted", N_("last printed"), "remove", "remove"),
    "title": ("dc:title", N_("title"), "prov", "remove"),
    "subject": ("dc:subject", N_("subject"), "prov", "remove"),
    "description": ("dc:description", N_("description"), "prov", "remove"),
    "keywords": ("cp:keywords", N_("keywords"), "prov", "remove"),
    "category": ("cp:category", N_("category"), "prov", "remove"),
    "contentStatus": ("cp:contentStatus", N_("status"), "prov", "remove"),
    "identifier": ("dc:identifier", N_("identifier"), "prov", "remove"),
    "version": ("cp:version", N_("version"), "prov", "remove"),
    "language": ("dc:language", N_("language"), "keep", "keep"),
}
_NOTE_PARANOID = N_("PARANOID removes all provenance: the file reads as processed, and the "
                    "profile doesn't hide that.")


def _sec_core(pkg):
    name = "docProps/core.xml"
    if not pkg.exists(name):
        return
    P = pkg.patch(name, text=True)
    if not P.nodes:
        raise _Bad(tr("core.xml has no root element"))
    root = P.nodes[0]
    vals = {n.name: n.text().strip() for n in root.kids}
    same_dates = bool(vals.get("created")) and vals.get("created") == vals.get("modified")
    for n in list(root.kids):
        key, ru, st, pa = _CORE_RULES.get(n.name, ("%s" % n.name, tr("non-standard field"), "prov", "remove"))
        val = n.text().strip()
        label = "%s — %s" % (key, tr(ru))
        mode = pa if pkg.paranoid else st
        if pkg.held(name, key):
            continue
        prov_note = tr("Provenance: STEALTH keeps it, PARANOID removes it.")
        if mode == "prov":
            mode = "blank" if pkg.leaky(val) else "keep"
            if mode == "blank":
                prov_note = (tr("The value contained personal data (an email, a path or a name "
                                "found in other properties): blanked."))
        if mode == "blank":
            if not val:
                pkg.act(Act.KEPT, name, label, "", "", tr("Already empty. ") + (
                    tr("An empty element is an ordinary state (Word writes it like that when no "
                       "name is set).")
                    if n.name in ("creator", "lastModifiedBy") else ""))
                continue
            P.set_text(n, "")
            note = (tr("Identity. The element was left EMPTY rather than removed: Word writes it "
                       "empty when no user name is set, and a missing element is a cleaning "
                       "trace.")
                    if n.name in ("creator", "lastModifiedBy") else prov_note)
            pkg.act(Act.BLANKED, name, label, val, "", note)
        elif mode == "remove":
            if n.name == "lastPrinted":
                note = (tr("Time of the last print. Word doesn't write this element for "
                           "documents never printed, so its absence stands out in no way."))
            else:
                note = tr(_NOTE_PARANOID)
            P.remove(n)
            pkg.act(Act.REMOVED, name, label, val, "", note)
        else:
            if n.name in ("created", "modified"):
                note = (tr("STEALTH: dates are left alone. Collapsing them into one second is a "
                           "cleaning trace, and substituting a date is forging provenance."))
                if same_dates:
                    note += (tr(" In the source, created and modified match to the second: "
                                "that's a property of the file itself; cleaning didn't create it."))
            elif n.name == "revision":
                note = (tr("STEALTH: Word always writes cp:revision; its absence or zeroing is a "
                           "cleaning trace. PARANOID removes it."))
            elif n.name == "language":
                note = tr("Structural: spell checking and screen readers need the document "
                          "language.")
            else:
                note = prov_note
            pkg.act(Act.KEPT, name, label, val, "", note)
    if not _drop_emptied(pkg, name, P, root, tr("Office never writes an empty core.xml, and the "
                                                "inspector takes it for a parse error; the part "
                                                "was removed together with its reference in "
                                                ".rels and its Override.")):
        pkg.commit(name, P)


# --- B. docProps/app.xml ----------------------------------------------------------


def _sec_app(pkg):
    name = "docProps/app.xml"
    if not pkg.exists(name):
        return
    P = pkg.patch(name, text=True)
    if not P.nodes:
        raise _Bad(tr("app.xml has no root element"))
    root = P.nodes[0]
    titles = next((k for k in root.kids if k.name == "TitlesOfParts"), None)
    drop_titles = pkg.paranoid or bool(titles is not None and pkg.leaky(titles.text()))
    stats, removed_stats = [], []
    for n in list(root.kids):
        nm, val = n.name, n.text().strip()
        if pkg.held(name, nm):
            continue
        if nm in ("Application", "AppVersion"):
            if pkg.paranoid:
                P.remove(n)
                pkg.act(Act.REMOVED, name, nm, val, "", tr("PARANOID removes the application and "
                                                           "version: without them the file reads "
                                                           "as processed (Word always writes "
                                                           "app.xml with Application), and the "
                                                           "profile doesn't hide that."))
            else:
                pkg.act(Act.KEPT, name, nm, val, "", tr("STEALTH: kept as is. The file really "
                                                        "was made by this application, and an "
                                                        "empty or substituted Application with "
                                                        "the part structure intact is an instant "
                                                        "sign of processing."))
        elif nm in ("Company", "Manager"):
            if pkg.paranoid:
                P.remove(n)
                pkg.act(Act.REMOVED, name, nm, val, "", tr("Identity: PARANOID removes the "
                                                           "element."))
            elif val:
                P.set_text(n, "")
                pkg.act(Act.BLANKED, name, nm, val, "", tr("Identity: blanked, the element was "
                                                           "left empty (that's how Word writes "
                                                           "an unset field)."))
            else:
                pkg.act(Act.KEPT, name, nm, "", "", tr("Already empty."))
        elif nm == "TotalTime":
            if pkg.paranoid:
                P.remove(n)
                pkg.act(Act.REMOVED, name, nm, val, "", tr("Editing time in minutes. ") + tr(_NOTE_PARANOID))
            else:
                pkg.act(Act.KEPT, name, nm, val, "", tr("STEALTH: provenance is left alone. A "
                                                        "zero value in a large document is a "
                                                        "cleaning trace, and an invented one is "
                                                        "forged provenance; PARANOID removes the "
                                                        "real editing time."))
        elif nm == "Template":
            if pkg.paranoid:
                P.remove(n)
                pkg.act(Act.REMOVED, name, nm, val, "", tr("Template name. ") + tr(_NOTE_PARANOID))
            elif _is_pathlike(val) or pkg.leaky(val):
                new = _relativize(val) if _is_pathlike(val) else ""
                if pkg.leaky(new):
                    new = ""
                P.set_text(n, new)
                pkg.act(Act.NORMALIZED, name, nm, val, new, tr("The template path reveals the "
                                                               "author's user name and "
                                                               "directories: only the file name "
                                                               "was kept."))
            else:
                stats.append(nm)
        elif nm == "DigSig":
            P.remove(n)
            pkg.act(Act.REMOVED, name, nm, "", "", tr("A digital signature in the properties: "
                                                      "the signer's identity."))
        elif nm in ("HeadingPairs", "TitlesOfParts"):
            if drop_titles:
                P.remove(n)
                removed_stats.append(nm)
            else:
                stats.append(nm)
        elif nm in _APP_FLAGS:
            stats.append(nm)
        elif pkg.paranoid:
            P.remove(n)
            removed_stats.append(nm)
        elif pkg.leaky(val):
            P.set_text(n, "")
            pkg.act(Act.BLANKED, name, nm, val, "", tr("The value contained personal data: "
                                                       "blanked."))
        else:
            stats.append(nm)
    if removed_stats:
        pkg.act(Act.REMOVED, name, tr("Document statistics and composition"), ", ".join(removed_stats), "",
                tr("PARANOID removes all provenance (size, part composition, sheet and slide "
                   "names).")
                if pkg.paranoid else tr("Part titles contained personal data: removed together "
                                        "with HeadingPairs."))
    if stats:
        pkg.act(Act.KEPT, name, tr("Statistics and flags"), ", ".join(stats), "",
                tr("STEALTH: document size and flags are left alone; this is provenance without "
                   "personal data."))
    if not _drop_emptied(pkg, name, P, root, tr("Office never writes an empty app.xml, and the "
                                                "inspector takes it for a parse error; the part "
                                                "was removed together with its reference in "
                                                ".rels and its Override. A file without app.xml "
                                                "reads as processed, and the profile doesn't "
                                                "hide that.")):
        pkg.commit(name, P)


# --- C. docProps/custom.xml, Purview labels, thumbnail, people, printers ----------------

_KEEP_UNDERSCORE = {"_MarkAsFinal", "_ReviewingToolsShownOnce"}


def _custom_why(pkg, name, val):
    """Why a property is removed (None means keep). The rules match the inspector's."""
    if name.startswith("MSIP_Label_"):
        return (tr("A Microsoft Purview label: the label GUID, the time it was set, SetBy is a "
                   "real email, SiteId is the tenant GUID. The tenant may notice the label was "
                   "removed: that's the user's decision."))
    if _emails(val):
        return tr("The value contains an email.")
    if val and _PERSON_NAME_RE.search(name):
        return tr("The property holds a person (author, executor, signer).")
    if name in _SP_NAMES or "urn:schemas" in name or name.startswith("_dlc_") or (
            name.startswith("_") and name not in _KEEP_UNDERSCORE):
        return tr("A SharePoint/document management property or an Office service property: ties "
                  "to a portal, library or correspondence.")
    if _has_path(val):
        return tr("The value looks like a local or network path.")
    if pkg.leaky(val):
        return tr("The value contains a personal string from other document properties.")
    return None


def _sec_custom(pkg):
    name = "docProps/custom.xml"
    if pkg.exists(name):
        P = pkg.patch(name, text=True)
        props = P.by("property")
        gone = []
        for n in props:
            pname, val = n.get("name") or "", n.text().strip()
            why = _custom_why(pkg, pname, val)
            if why is None and pkg.paranoid:
                why = tr("PARANOID removes all custom properties.")
            if why is None and getattr(pkg, "drop_custom", False):
                why = (tr("The file was regenerated by LibreOffice: a fresh file has no custom "
                          "properties, so this one was carried over from the source."))
            if why is None or pkg.held(name, pname):
                continue
            P.remove(n)
            gone.append(n)
            pkg.act(Act.REMOVED, name, pname, val, "", why)
        if props and len(gone) == len(props):
            # The inspector counts an empty custom.xml as a parse error, and Word never writes it like that.
            if pkg.drop_part(name):
                pkg.act(Act.REMOVED, name, "docProps/custom.xml", tr("%d properties") % len(props), "",
                        tr("No properties are left: Word never writes an empty custom.xml (and "
                           "the inspector takes it for a parse error), so the part was removed "
                           "together with its reference in .rels and its Override."))
            else:
                pkg.commit(name, P)
        elif gone:
            rest = [n for n in props if n not in gone]
            if [n.get("pid") for n in rest] != [str(i) for i in range(2, 2 + len(rest))]:
                for i, n in enumerate(rest, 2):      # Word numbers pids consecutively from 2
                    if n.get("pid") != str(i):
                        P.set_attr(n, "pid", str(i))
            pkg.commit(name, P)
    for nm in [n for n in pkg.names if n.lower().endswith("labelinfo.xml")]:
        if pkg.exists(nm):
            _drop(pkg, nm, "LabelInfo", tr("Sensitivity label (LabelInfo.xml)"), "",
                  tr("Duplicates the Purview label as a separate part: the label GUID, tenant, "
                     "email. The tenant may notice the label was removed: that's the user's "
                     "decision."))


def _sec_thumbnail(pkg):
    for nm in [n for n in pkg.names if n.startswith("docProps/thumbnail")]:
        if pkg.exists(nm):
            _drop(pkg, nm, "thumbnail", tr("Document thumbnail"), tr("%d bytes") % pkg.info[nm].file_size,
                  tr("The thumbnail is rendered on save and may show a PREVIOUS version of the "
                     "document, including text already deleted; it's also an image with its own "
                     "metadata."))


def _sec_people(pkg):
    if pkg.exists("word/people.xml"):
        _drop(pkg, "word/people.xml", "people", tr("List of authors (people.xml)"), "",
              tr("w15:presenceInfo/@userId is usually a real work email. The part is pure "
                 "identity and was removed entirely together with its reference in "
                 "document.xml.rels and its Override in [Content_Types].xml."))


def _sec_xl_revisions(pkg):
    """Excel's shared-workbook revision log: userName and dateTime of every save by every
    participant; userNames.xml lists everyone who opened the workbook. Removed entirely;
    Excel opens the workbook as an ordinary one (the same as "stop sharing"). revisionHeaders
    goes first: its .rels goes with it, and the logs are left without dangling references."""
    parts = sorted((n for n in pkg.names if n.startswith("xl/revisions/") and n.endswith(".xml")
                    and pkg.exists(n)), key=lambda n: not n.endswith("revisionHeaders.xml"))
    for nm in parts:
        if pkg.exists(nm):
            _drop(pkg, nm, "revisions", tr("Shared workbook revision log (%s)") % nm.rsplit("/", 1)[-1], "",
                  tr("The names of all participants of the shared workbook and the time of each "
                     "of their saves."))


def _sec_printer(pkg):
    for nm in [n for n in pkg.names if "printersettings" in n.lower() and n.lower().endswith(".bin")]:
        if not pkg.exists(nm):
            continue
        try:
            dev = _device_name(pkg.raw(nm, 4 * 1024 * 1024)) or tr("<not parsed>")
        except Exception:  # noqa: BLE001
            dev = tr("<not read>")
        _drop(pkg, nm, "printerSettings", tr("Printer settings (DEVMODE)"), dev,
              tr("The first 32 characters of the structure are the printer name: often a host "
                 "name, room number or office. The part was removed with its references from "
                 ".rels (and r:id in the pageSetup of Excel sheets)."))


def _sec_vba(pkg):
    vba = [n for n in pkg.names if n.lower().endswith("vbaproject.bin") and pkg.exists(n)]
    for nm in vba:
        size = tr("%d bytes") % pkg.info[nm].file_size
        if pkg.paranoid and not pkg.held(nm, "vbaProject"):
            extra = [n for n in pkg.names if n.lower().endswith(("vbaprojectsignature.bin", "vbadata.xml"))
                     and pkg.exists(n) and posixpath.dirname(n) == posixpath.dirname(nm)]
            if pkg.drop_part(nm):
                for e in extra:
                    pkg.drop_part(e)
                pkg.act(Act.REMOVED, nm, tr("VBA project (macros)"), size, "",
                        tr("PARANOID removes the macros entirely: they contain source code, "
                           "module names and the project path. Document functionality that "
                           "relied on them is lost."))
            else:
                pkg.act(Act.UNREMOVABLE, nm, tr("VBA project (macros)"), size,
                        note=tr("The references to the project could not be removed, so the "
                                "macros were kept."))
        else:
            pkg.act(Act.KEPT, nm, tr("VBA project (macros)"), size, "",
                    tr("hazard: the file has macros (active content). STEALTH doesn't remove "
                       "them: that would break the document, and the decision is the user's. The "
                       "project may contain the author's names and path."))


# --- embedded items: images, embedded files, OLE ----------------------------------------


def _clean_blob(pkg, name, data):
    """Run an embedded item through its format's cleaner. Returns (family, sub, new bytes)."""
    pkg.nested_n += 1
    p_in = os.path.join(pkg.tmp, "n%04d.in" % pkg.nested_n)
    p_out = os.path.join(pkg.tmp, "n%04d.out" % pkg.nested_n)
    with open(p_in, "wb") as fh:
        fh.write(data)
    try:
        fam = sniff.family(sniff.sniff(p_in))
        sub = out = None
        if fam == "image":
            from .clean_image import clean_image
            sub = clean_image(p_in, p_out, pkg.profile, pkg.keep)
        elif fam == "ooxml":
            if pkg.depth >= MAX_DEPTH:
                return "depth", None, None
            sub = clean_ooxml(p_in, p_out, pkg.profile, pkg.keep, force_signed=pkg.force_signed,
                              _depth=pkg.depth + 1)
        elif fam == "pdf":
            from .clean_pdf import clean_pdf
            sub = clean_pdf(p_in, p_out, pkg.profile, pkg.keep)
        else:
            return fam, None, None
        if sub.ok and os.path.isfile(p_out):
            with open(p_out, "rb") as fh:
                out = fh.read()
        return fam, sub, out
    finally:
        for p in (p_in, p_out):
            try:
                os.remove(p)
            except OSError:
                pass


def _merge(pkg, name, sub):
    if sub.actions and all(a.act is Act.KEPT for a in sub.actions):
        pkg.act(Act.KEPT, name, tr("Embedded file without metadata"), "", "",
                tr("The embedded item's cleaner removed nothing: only structural elements "
                   "remained (%d).") % len(sub.actions))
        return
    for a in sub.actions:
        pkg.act(a.act, "%s > %s" % (name, a.location), a.label, a.before, a.after, a.note)


def _nested(pkg, name, what):
    """Embedded item -> its format's cleaner. Returns the family if the item was NOT processed
    (OLE, unknown format), the string "big"/"depth" when refused by size or depth, otherwise None."""
    info = pkg.info[name]
    if info.file_size > MAX_NESTED:
        pkg.act(Act.UNREMOVABLE, name, what, tr("%d MB") % (info.file_size >> 20),
                note=tr("Embedded item larger than %d MB: not loaded into memory; the metadata "
                        "inside remains.") % (MAX_NESTED >> 20))
        return "big"
    data = pkg.raw(name, MAX_NESTED)
    fam, sub, out = _clean_blob(pkg, name, data)
    if sub is None:
        if fam == "depth":
            pkg.act(Act.UNREMOVABLE, name, what, note=tr("Nesting deeper than %d: the inner "
                                                         "package isn't cleaned; its metadata "
                                                         "remains.") % MAX_DEPTH)
            return "depth"
        return fam
    if sub.ok and out is not None:
        _merge(pkg, name, sub)
        if out != data:
            pkg.cur[name] = out
        return None
    pkg.act(Act.UNREMOVABLE, name, what, note=tr("The embedded item could not be cleaned (%s): "
                                                 "the metadata inside remains.") % "; ".join(sub.errors)[:300])
    return None


def _sec_media(pkg):
    skipped = {}
    for nm in [n for n in pkg.names if re.match(r"^(word|xl|ppt)/media/", n) and not n.endswith("/")]:
        if not pkg.exists(nm):
            continue
        fam = _nested(pkg, nm, tr("Embedded image"))
        if fam and fam not in ("big", "depth"):
            ext = nm.rsplit(".", 1)[-1].lower() if "." in nm.rsplit("/", 1)[-1] else "?"
            skipped[ext] = skipped.get(ext, 0) + 1
    if skipped:
        pkg.act(Act.KEPT, "media", tr("Non-raster media files"),
                ", ".join("%s: %d" % kv for kv in sorted(skipped.items())), "",
                tr("Formats the image cleaner doesn't parse (EMF, WMF, SVG, video, audio): "
                   "metadata in them was neither searched for nor removed."))


_FAMILY_RU = {"unknown": N_("format not recognized"), "text": N_("text"), "zip": N_("ZIP archive"), "odf": "ODF", "rtf": "RTF",
              "ole": "OLE2"}


def _sec_embeddings(pkg):
    what = tr("Embedded object")
    for nm in [n for n in pkg.names if "/embeddings/" in n and not n.endswith("/")]:
        if not pkg.exists(nm):
            continue
        rest = _nested(pkg, nm, what)
        if rest == "ole":
            size = tr("%d bytes") % pkg.info[nm].file_size
            if pkg.paranoid and not pkg.held(nm, "embeddings"):
                _drop(pkg, nm, "embeddings", tr("Embedded OLE object"), size,
                      tr("PARANOID removes the embedded OLE object entirely together with the "
                         "object in the text: it has its own docProps (author, company, path), "
                         "and OLE2 can't be cleaned in place."))
            else:
                pkg.act(Act.UNREMOVABLE, nm, what, size,
                        note=tr("Embedded OLE object: it has its own docProps inside (author, "
                                "company, path), and OLE2 can't be cleaned in place, "
                                "so the metadata INSIDE the object remains. The paranoid profile "
                                "removes such an object entirely."))
        elif rest and rest not in ("big", "depth"):
            pkg.act(Act.KEPT, nm, what, tr("%d bytes") % pkg.info[nm].file_size, "",
                    tr("Embedded file of a format that has no cleaner (%s): its content wasn't "
                       "parsed.")
                    % tr(_FAMILY_RU.get(rest, rest)))


# --- D. word/settings.xml ---------------------------------------------------------------


def _sec_settings(pkg):
    name = "word/settings.xml"
    if not pkg.exists(name):
        return
    P = pkg.patch(name, text=True)
    rm = set()                                    # r:ids from settings.xml.rels that lose their owner
    for n in P.by("attachedTemplate", W_NSES):
        rid = n.get("id", R_NSES)
        target = pkg.rel_target(name, rid) if rid else None
        P.remove(n)
        if rid:
            rm.add(rid)
        pkg.act(Act.REMOVED, name, tr("w:attachedTemplate — attached template"), target or "r:id=%s" % rid,
                "", tr("The template path is usually file:///C:/Users/NAME/... or UNC: the user "
                       "name and the organization's server. Without an attached template Word "
                       "uses Normal.dotm."))
    for n in P.by("mailMerge", W_NSES):
        bits = []
        for k in _descendants(n):
            for ans, aloc, v in k.attrs:
                if ans in R_NSES:
                    rm.add(v)
                    bits.append("%s=%s" % (k.name, pkg.rel_target(name, v) or v))
                elif aloc in ("val", "id") and k.name in ("dataSource", "headerSource", "query", "src",
                                                            "connectString", "udl", "table", "odso"):
                    bits.append("%s=%s" % (k.name, v))
        P.remove(n)
        pkg.act(Act.REMOVED, name, tr("w:mailMerge — mail merge data source"), "; ".join(bits)[:400], "",
                tr("The database path, table and connection string with the login and server. "
                   "The document stops being a mail merge main document; the text and fields "
                   "stay."))
    for n in P.by("documentProtection", W_NSES) + P.by("writeProtection", W_NSES):
        hashed = [(aloc, v) for _a, aloc, v in n.attrs if aloc in _HASH_ATTRS]
        if hashed:
            for aloc, _v in hashed:
                P.del_attr(n, aloc)
            pkg.act(Act.REMOVED, name, tr("w:%s — password hash and salt") % n.name,
                    "; ".join("%s=%s" % kv for kv in hashed), "",
                    tr("The hash lets the password be cracked offline, and passwords are often "
                       "reused. The protection stays on, but WITHOUT a password: anyone can now "
                       "turn it off."))
    docvars = P.by("docVar", W_NSES)
    gone = 0
    for n in docvars:
        nm_, val = n.get("name", W_NSES) or "", n.get("val", W_NSES) or ""
        # A variable name that looks like a person means identity (same rule as the inspector's).
        if pkg.paranoid or pkg.leaky(val) or pkg.leaky(nm_) or (val and _PERSON_NAME_RE.search(nm_)):
            P.remove(n)
            gone += 1
            pkg.act(Act.REMOVED, name, "w:docVar %s" % nm_, val, "",
                    tr("A document variable: macros, add-ins and document management systems put "
                       "full names, numbers and paths here."))
    for par in P.by("docVars", W_NSES):
        if par.kids and all(k in docvars for k in par.kids) and gone == len(docvars):
            P.remove(par)
    rsid_n = len(P.by("rsid", W_NSES)) + len(P.by("rsidRoot", W_NSES))
    if pkg.paranoid:
        for tag in ("rsids", "proofState"):
            for n in P.by(tag, W_NSES):
                P.remove(n)
        for n in P.by("docId"):
            P.remove(n)
        if rsid_n:
            pkg.act(Act.REMOVED, name, tr("w:rsid — editing session IDs"), tr("%d pcs") % rsid_n, "",
                    tr("PARANOID removes all rsids: they link documents of one Word "
                       "installation. A document that claims Word and has no rsid at all reads "
                       "as not created by Word, and our inspector detects that (the ai signal)."))
    elif rsid_n:
        pkg.act(Act.KEPT, name, tr("w:rsid — editing session IDs"), tr("%d pcs") % rsid_n, "",
                tr("STEALTH: kept. These are random session IDs without direct personal data, "
                   "and their total absence in a document claiming Word means \"not created by "
                   "Word\". Removing rsids is a PARANOID option."))
    pkg.commit(name, P)
    rn = _rels_name(name)
    if rm and rn in pkg.rels:
        pkg.rels_remove(rn, rm)


# --- E. document body: revision authors and dates ----------------------------------------------


def _sec_body(pkg):
    for nm in pkg.names:
        if pkg.exists(nm) and (_BODY_RE.match(nm) or (
                pkg.paranoid and nm in ("word/styles.xml", "word/numbering.xml"))):
            _body_part(pkg, nm)


def _body_part(pkg, name):
    par = pkg.paranoid

    def want(ns, loc, attrs):
        for ans, aloc, _v in attrs:
            if (ans in W_NSES and aloc in ("author", "date", "initials", "ed")) or aloc == "dateUtc":
                return True
            if par and aloc.startswith("rsid"):
                return True
        return par and ns in W_NSES and loc in ("rsid", "rsids", "rsidRoot", "bookmarkStart", "bookmarkEnd")

    raw = pkg.raw(name)
    # Cheap byte-level filter: a document without revisions and (in PARANOID) without rsids isn't parsed at all.
    hint = rb"[\s:](?:author|date|dateUtc|initials|ed)\s*=" + (rb"|rsid|_GoBack" if par else b"")
    if not re.search(hint, raw):
        return
    P = pkg.patch(name, want, raw=raw)
    if not P.nodes:
        return
    authors, eds = {}, set()
    dates = initials = rsid = goback = 0
    gb = set()
    for n in P.nodes:
        if par and n.ns in W_NSES and n.name in ("rsid", "rsids", "rsidRoot"):
            P.remove(n)
            rsid += 1
            continue
        if par and n.ns in W_NSES and n.name == "bookmarkStart" and n.get("name", W_NSES) == "_GoBack":
            gb.add(n.get("id", W_NSES))
            P.remove(n)
            goback += 1
            continue
        if par and n.ns in W_NSES and n.name == "bookmarkEnd" and n.get("id", W_NSES) in gb:
            P.remove(n)
            continue
        for ans, aloc, v in n.attrs:
            key = (ans, aloc)
            if ans in W_NSES and aloc == "author":
                if v != AUTHOR:
                    P.set_attr(n, key, AUTHOR)
                    authors[v] = authors.get(v, 0) + 1
            elif (ans in W_NSES and aloc == "date") or aloc == "dateUtc":
                P.del_attr(n, key)
                dates += 1
            elif ans in W_NSES and aloc == "initials":
                P.del_attr(n, key)
                initials += 1
            elif ans in W_NSES and aloc == "ed" and n.name in ("permStart", "permEnd"):
                P.del_attr(n, key)
                eds.add(v)
            elif par and aloc.startswith("rsid"):
                P.del_attr(n, key)
                rsid += 1
    for who, cnt in sorted(authors.items(), key=lambda kv: -kv[1])[:MAX_ACTS_PER_PART]:
        pkg.act(Act.NORMALIZED, name, tr("Revision or comment author"), who, AUTHOR,
                tr("%d time(s). Revisions (w:ins/w:del) and comments stay: they're the user's "
                   "content; only the authorship was removed. \"Author\" is what Word itself "
                   "writes with \"Remove personal information on save\" enabled.") % cnt)
    if len(authors) > MAX_ACTS_PER_PART:
        pkg.act(Act.NORMALIZED, name, tr("Revision authors (the rest)"), tr("%d authors") % (len(authors) - MAX_ACTS_PER_PART),
                AUTHOR, tr("Not all are shown; the rest are of the same kind."))
    if dates:
        pkg.act(Act.REMOVED, name, tr("Revision and comment dates (w:date, dateUtc)"), tr("%d pcs") % dates, "",
                tr("The exact time of every revision. The attribute is optional in the schema; "
                   "the revisions stay in place."))
    if initials:
        pkg.act(Act.REMOVED, name, tr("Author initials (w:initials)"), tr("%d pcs") % initials, "",
                tr("Initials are part of a comment author's name."))
    if eds:
        pkg.act(Act.REMOVED, name, tr("w:permStart/@ed — name of the permitted editor"), "; ".join(sorted(eds))[:400], "",
                tr("The ranges stay, but the binding to specific people was removed: the "
                   "protection exceptions no longer apply to them."))
    if rsid:
        pkg.act(Act.REMOVED, name, tr("rsid attributes"), tr("%d pcs") % rsid, "", tr("PARANOID removes "
                                                                            "rsids. ") + tr(_NOTE_PARANOID))
    if goback:
        pkg.act(Act.REMOVED, name, tr("_GoBack bookmark"), tr("present"), "",
                tr("Word's save signature. ") + tr(_NOTE_PARANOID))
    pkg.commit(name, P)


# --- H. Excel/PowerPoint comments and authors ----------------------------------------------

_PERSON_ATTRS = _ANON_ATTRS | {"userId", "email", "initials", "providerId", "date", "dt", "dT", "created"}


def _sec_persons(pkg):
    for nm in pkg.names:
        if not pkg.exists(nm):
            continue
        if _PERSON_RE.match(nm):
            _person_part(pkg, nm)
        elif _XLCOMMENTS_RE.match(nm):
            _xl_comments(pkg, nm)


def _person_part(pkg, name):
    P = pkg.patch(name, lambda _ns, _l, attrs: any(x[1] in _PERSON_ATTRS for x in attrs))
    seen, dates, required = {}, 0, 0
    for n in P.nodes:
        for ans, aloc, v in n.attrs:
            key = (ans, aloc)
            if aloc in _ANON_ATTRS or aloc == "userId":
                if v != AUTHOR:
                    P.set_attr(n, key, AUTHOR)
                    seen[v] = seen.get(v, 0) + 1
            elif aloc in ("email", "initials"):
                if v:
                    P.set_attr(n, key, "")
                    seen[v] = seen.get(v, 0) + 1
            elif aloc == "providerId":
                if v != "None":
                    P.set_attr(n, key, "None")
            elif aloc in ("date", "dt"):
                P.del_attr(n, key)
                dates += 1
            elif aloc in ("dT", "created"):
                required += 1
    for who, cnt in sorted(seen.items(), key=lambda kv: -kv[1])[:MAX_ACTS_PER_PART]:
        pkg.act(Act.NORMALIZED, name, tr("Comment author or participant"), who, AUTHOR,
                tr("Names, logins, emails or initials replaced with neutral ones (%d time(s)).") % cnt)
    if dates:
        pkg.act(Act.REMOVED, name, tr("Comment dates"), tr("%d pcs") % dates, "", tr("Optional schema attribute."))
    if required:
        pkg.act(Act.KEPT, name, tr("Comment date (dT, created)"), tr("%d pcs") % required, "",
                tr("A required schema attribute: it can't be removed without damaging the part, "
                   "and substituting it would forge provenance."))
    pkg.commit(name, P)


def _xl_comments(pkg, name):
    P = pkg.patch(name, text=True)
    seen = {}
    for n in P.by("author"):
        v = n.text().strip()
        if v and v != AUTHOR and not _TC_PLACEHOLDER_RE.match(v):
            P.set_text(n, AUTHOR)
            seen[v] = seen.get(v, 0) + 1
    for who, cnt in seen.items():
        pkg.act(Act.NORMALIZED, name, tr("Comment author"), who, AUTHOR,
                tr("The list of authors. A name in the comment text itself (usually the bold "
                   "first line) is content and is left alone."))
    pkg.commit(name, P)


# --- N. Excel specifics ------------------------------------------------------------------------

_FORMULA_PATH = re.compile(r"(?:file:///)?(?:[A-Za-z]:[\\/]|\\\\[^\\/\[\]']+[\\/])[^\[\]']*(?=\[)")


def _sec_workbook(pkg):
    name = "xl/workbook.xml"
    if not pkg.exists(name):
        return
    P = pkg.patch(name, text=True)
    for n in P.by("fileVersion"):
        if pkg.held(name, "fileVersion"):
            continue
        parts = "; ".join("%s=%s" % (a[1], a[2]) for a in n.attrs)
        if pkg.paranoid:
            P.remove(n)
            pkg.act(Act.REMOVED, name, tr("fileVersion — Excel version and build"), parts, "",
                    tr("The exact Excel build (rupBuild). ") + tr(_NOTE_PARANOID))
        else:
            pkg.act(Act.KEPT, name, tr("fileVersion — Excel version and build"), parts, "",
                    tr("STEALTH: the truth about the file, like Application/AppVersion; without "
                       "it a workbook from Excel looks processed."))
    for n in P.by("absPath"):
        ac = n.parent.parent if (n.parent is not None and n.parent.name == "Choice") else None
        target = ac if (ac is not None and ac.name == "AlternateContent" and len(ac.kids) == 1
                        and len(ac.kids[0].kids) == 1) else n
        P.remove(target)
        pkg.act(Act.REMOVED, name, tr("x15ac:absPath — absolute path of the save folder"), n.get("url") or "", "",
                tr("Excel writes the folder where the workbook was saved here, with the OS user "
                   "name. The inspector doesn't see this place."))
    for n in P.by("definedName"):
        val = n.text()
        if _has_path(val):
            new = _FORMULA_PATH.sub("", val)
            if new != val:
                P.set_text(n, new)
                pkg.act(Act.NORMALIZED, name, "definedName %s" % (n.get("name") or "?"), val, new,
                        tr("A link to an external workbook with a local path: only the workbook "
                           "name was kept."))
    for n in P.by("fileSharing"):
        gone = [(aloc, v) for _a, aloc, v in n.attrs if aloc in _HASH_ATTRS or aloc in ("userName", "reservationPassword")]
        if gone:
            for aloc, _v in gone:
                P.del_attr(n, aloc)
            pkg.act(Act.REMOVED, name, tr("fileSharing — write reservation name and password"),
                    "; ".join("%s=%s" % kv for kv in gone), "",
                    tr("userName is the name of the user who reserved writing; the password is "
                       "its hash."))
    for n in P.by("workbookProtection"):
        hashed = [(aloc, v) for _a, aloc, v in n.attrs if aloc in _HASH_ATTRS]
        if hashed:
            for aloc, _v in hashed:
                P.del_attr(n, aloc)
            pkg.act(Act.REMOVED, name, tr("workbookProtection — password hash"), "; ".join("%s=%s" % kv for kv in hashed),
                    "", tr("The protection stays on, but without a password: anyone can now turn "
                           "it off."))
    hidden = [n.get("name") or "?" for n in P.by("sheet") if (n.get("state") or "").lower() in ("hidden", "veryhidden")]
    if hidden:
        pkg.act(Act.KEPT, name, tr("Hidden sheets"), "; ".join(hidden[:20]), "",
                tr("A hidden sheet is workbook content, not metadata: left alone."))
    pkg.commit(name, P)


_SHEET_PROT_RE = re.compile(r"^xl/(worksheets|chartsheets|dialogsheets)/[^/]+\.xml$")


def _stream_has(pkg, name, needle):
    """Whether needle occurs in the part (streaming: for parts not loaded into memory)."""
    tail = b""
    with pkg.zf.open(pkg.info[name]) as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                return False
            if needle in tail + chunk:
                return True
            tail = chunk[-len(needle):]


def _sec_protection(pkg):
    """Password hashes of sheet protection (sheetProtection) and presentation protection
    (modifyVerifier): as with documentProtection, the protection stays on, but without a password.
    Sheets can be huge, so only those where this word occurs in the bytes are parsed."""
    for nm in pkg.names:
        if not pkg.exists(nm):
            continue
        if _SHEET_PROT_RE.match(nm):
            tag, extra = "sheetProtection", ("password",)
        elif nm == "ppt/presentation.xml":
            tag, extra = "modifyVerifier", ()
        else:
            continue
        if pkg.info[nm].file_size > MAX_XML:
            if _stream_has(pkg, nm, tag.encode()):
                pkg.act(Act.UNREMOVABLE, nm, tr("%s — password hash") % tag, "",
                        note=tr("The part is larger than %d MB: editing is impossible, the "
                                "protection password hash remains.") % (MAX_XML >> 20))
            continue
        raw = pkg.raw(nm)
        if tag.encode() not in raw:
            continue
        P = pkg.patch(nm, lambda _ns, loc, _a: loc == tag, raw=raw)
        for n in P.nodes:
            gone = [(aloc, v) for _a, aloc, v in n.attrs if aloc in _HASH_ATTRS or aloc in extra]
            if not gone:
                continue
            if tag == "modifyVerifier":
                # An element without a hash isn't "protection without a password" but an unclear state
                # (PowerPoint might treat the password as empty): write protection is removed entirely.
                P.remove(n)
                pkg.act(Act.REMOVED, nm, tr("p:modifyVerifier — write password"), "; ".join("%s=%s" % kv for kv in gone), "",
                        tr("The hash lets the password be cracked offline. The element was "
                           "removed entirely: write protection is off."))
                continue
            for aloc, _v in gone:
                P.del_attr(n, aloc)
            pkg.act(Act.REMOVED, nm, tr("%s — password hash") % tag, "; ".join("%s=%s" % kv for kv in gone), "",
                    tr("The hash lets the password be cracked offline. The protection stays on, "
                       "but without a password: anyone can now turn it off."))
        pkg.commit(nm, P)


def _sec_pivot(pkg):
    for nm in pkg.names:
        if not (pkg.exists(nm) and _PIVOT_RE.match(nm)):
            continue
        P = pkg.patch(nm)
        if not P.nodes:
            continue
        root = P.nodes[0]
        for ans, aloc, v in root.attrs:
            if aloc == "refreshedBy" and v:
                P.set_attr(root, (ans, aloc), "")
                pkg.act(Act.BLANKED, nm, tr("refreshedBy — who refreshed the pivot table"), v, "",
                        tr("A classic Excel leak: the name of the person who refreshed the cache "
                           "lies outside docProps."))
            elif aloc in ("refreshedDate", "refreshedDateIso"):
                P.del_attr(root, (ans, aloc))
                pkg.act(Act.REMOVED, nm, tr("%s — when it was refreshed") % aloc, v, "", tr("Time the pivot cache was refreshed."))
        pkg.commit(nm, P)


def _split_conn(s):
    return re.findall(r"""(?:[^;"']|"[^"]*"|'[^']*')+""", s)


def _sanitize_conn(s):
    """DRIVER=SQL Server;SERVER=x;UID=y -> DRIVER=SQL Server (internal tokens like $Workbook$ stay)."""
    kept = []
    for part in _split_conn(s):
        k, eq, v = part.partition("=")
        if eq and k.strip().lower() in _CONN_SECRET and not v.strip().startswith("$"):
            continue
        kept.append(part)
    out = ";".join(kept)
    return "" if (_has_path(out) or _emails(out)) else out


def _sec_connections(pkg):
    name = "xl/connections.xml"
    if not pkg.exists(name):
        return
    P = pkg.patch(name)
    for conn in P.by("connection", None):
        if conn.parent is None:
            continue                                  # the <connections> root, not a connection
        mashup = any("microsoft.mashup" in (a[2] or "").lower() for k in [conn] + list(_descendants(conn))
                     for a in k.attrs)
        head = "; ".join("%s=%s" % (a[1], a[2]) for a in conn.attrs if a[1] in ("name", "description"))
        if mashup:
            pkg.act(Act.KEPT, name, tr("Power Query connection"), head, "",
                    tr("An internal connection to the workbook's own data ($Workbook$): it has "
                       "no external server. The query code lives in customXml (DataMashup), see "
                       "the separate action."))
            continue
        before, changed = [], False
        for a in conn.attrs:
            if a[1] in ("description", "odcFile", "sourceFile", "singleSignOnId") and a[2]:
                P.del_attr(conn, (a[0], a[1]))
                before.append("%s=%s" % (a[1], a[2]))
                changed = True
        for k in _descendants(conn):
            for ans, aloc, v in k.attrs:
                if aloc not in _CONN_SOURCE_ATTRS or not v:
                    continue
                new = _sanitize_conn(v) if aloc == "connection" else ""
                if new != v:
                    P.set_attr(k, (ans, aloc), new)
                    before.append("%s=%s" % (aloc, v))
                    changed = True
        if changed:
            pkg.act(Act.BLANKED, name, tr("Data connection"), "; ".join(before)[:400], "",
                    tr("The connection string (server, database, login), the query and paths "
                       "were anonymized: the connection no longer refreshes, but the workbook "
                       "structure and cached data are intact."))
    pkg.commit(name, P)


# --- customXml: SharePoint metadata ----------------------------------------------------------


def _root_info(raw):
    first = []

    def want(_ns, _loc, _a):
        if first:
            return False
        first.append(1)
        return True

    nodes = _parse(raw, want)
    return nodes[0] if nodes else None


def _has_text(raw):
    """Whether the part has any text between tags at all. Needed to tell an empty part
    (which can honestly be kept) from a part with values inside (which can be kept,
    but calling that "kept on purpose" would be wrong)."""
    return bool(re.sub(rb"<[^>]*>", b"", raw).strip())


def _sec_customxml(pkg):
    content, empty, opaque = 0, 0, 0
    for nm in pkg.names:
        if not (pkg.exists(nm) and _CUSTOMXML_RE.match(nm)):
            continue
        raw = pkg.raw(nm)
        try:
            root = _root_info(raw)
        except _Bad:
            root = None                      # not XML: it needs no edits; the inspector will show what's there
        if root is None:
            opaque += 1
            continue
        if root.ns.startswith("http://schemas.microsoft.com/DataMashup"):
            pkg.act(Act.UNREMOVABLE, nm, "Power Query (DataMashup)", tr("%d bytes") % len(raw),
                    note=tr("Inside is an encoded package with the query code: server addresses, "
                            "file paths and source names. Cleaning this container isn't "
                            "implemented; the metadata remains."))
            continue
        if root.ns not in _SP_ROOT_NS:
            # Not SharePoint. The decision depends on whether there are values inside:
            # an empty part can honestly be called kept, a part with values can't, even
            # if it can't be removed.
            if root.ns in _CONTENT_ROOT_NS:
                content += 1
            elif _has_text(raw):
                pkg.act(Act.UNREMOVABLE, nm, tr("Custom XML part"), tr("%d bytes") % len(raw), "",
                        tr("Root \"%s\" wasn't recognized, and there are values inside. It can't "
                           "be removed blindly: content control data binding (w:dataBinding) "
                           "relies on customXml, and blanking the part would erase visible "
                           "document text. The values remain in the file.")
                        % (root.ns or root.name))
            else:
                empty += 1
            continue
        qn = _NAME_RE.match(raw[root.a:root.b]).group(0)[1:]
        prefix = qn.split(b":")[0] if b":" in qn else b""
        ns = root.ns.encode("ascii")
        # The namespace declaration is kept for a root in the default namespace too: a
        # FormTemplates root without xmlns is itself a trace of package editing; Office never writes that.
        decl = (b" xmlns:" + prefix + b'="' + ns + b'"') if prefix else (b' xmlns="' + ns + b'"')
        m = re.match(rb"(\xef\xbb\xbf)?<\?xml[^>]*\?>(\r?\n)?", raw)
        body = b"<documentManagement/>" if root.name == "properties" else b""
        stub = (m.group(0) if m else b"") + b"<" + qn + decl + (
            b">" + body + b"</" + qn + b">" if body else b"/>")
        if stub != raw:
            _well_formed(stub)
            pkg.cur[nm] = stub
            pkg.act(Act.REMOVED, nm, tr("SharePoint metadata (customXml)"),
                    clip(re.sub(rb"<[^>]*>", b" ", raw).decode("utf-8", "replace")), "",
                    tr("Library columns, content type schema, form templates: employee names, "
                       "dates, portal addresses. The content was blanked down to an empty root; "
                       "the part stayed in place."))
        _strip_schema_refs(pkg, nm)
    if content:
        pkg.act(Act.KEPT, "customXml", tr("Bibliography"), tr("%d pcs") % content, "",
                tr("The document's list of references: visible content, not metadata. The names "
                   "in it are the authors of the cited works, not yours. Left alone on purpose."))
    if empty:
        pkg.act(Act.KEPT, "customXml", tr("Empty custom XML parts"), tr("%d pcs") % empty, "",
                tr("The root wasn't recognized and there are no values inside. The part stayed "
                   "in place: its absence would itself be a sign of package editing."))
    if opaque:
        pkg.act(Act.UNREMOVABLE, "customXml", tr("Custom parts that didn't parse as XML"),
                tr("%d pcs") % opaque, "",
                tr("Not XML, so the content wasn't checked or cleaned. The inspector shows "
                   "what's there."))


def _strip_schema_refs(pkg, item):
    rn = _rels_name(item)
    for rid, typ, tgt, mode in pkg.rels.get(rn, ()):
        if _type_tail(typ) != "customXmlProps" or mode == "External":
            continue
        props = _resolve(rn, tgt)
        if not props or not pkg.exists(props):
            continue
        P = pkg.patch(props)
        refs = P.by("schemaRef")
        if not refs:
            continue
        uris = [r.get("uri") or "" for r in refs]
        for r in refs:
            P.remove(r)
        pkg.commit(props, P)
        pkg.act(Act.REMOVED, props, tr("Schema references (schemaRef)"), "; ".join(u for u in uris if u)[:400], "",
                tr("Ties to portal schemas; meaningless once the part's data is blanked."))


# --- I. external relationships -------------------------------------------------------------------------


def _sec_extrels(pkg):
    http = 0
    for rn in list(pkg.rels):
        kill = {}                      # rId -> (label, before, note): the relationship is removed entirely
        for rid, typ, tgt, mode in list(pkg.rels.get(rn, ())):
            low = tgt.strip().lower()
            if low.startswith(("http://", "https://", "ftp://", "tel:")):
                http += 1
            elif low.startswith("mailto:"):
                kill[rid] = (tr("mailto link"), tgt, tr("A direct email in an external "
                                                      "relationship. The link was removed; the "
                                                      "visible text in the document stayed (it's "
                                                      "content)."))
            elif _is_pathlike(tgt) and (mode == "External" or _resolve(rn, tgt) not in pkg.nameset):
                new = _relativize(tgt)
                if _type_tail(typ) in ("attachedTemplate", "mailMergeSource") or not new:
                    kill[rid] = (tr("External link to a file"), tgt, tr("An absolute path or UNC "
                                                                   "reveals the user name, the "
                                                                   "drive and the organization's "
                                                                   "server."))
                else:
                    pkg.rels_set_target(rn, rid, new)
                    pkg.act(Act.NORMALIZED, rn, tr("External link to a file (%s)") % rid, tgt, new,
                            tr("The path reveals the user name, drive and server. The link "
                               "stayed as working structure (formulas and objects rely on it), "
                               "but the directory was removed: only the file name was kept."))
        if kill:
            failed = pkg.unlink(rn, set(kill))
            for rid, (label, before, why) in kill.items():
                if rid in failed:
                    pkg.act(Act.UNREMOVABLE, rn, label, before, note=tr("The element referencing "
                                                                        "the relationship could "
                                                                        "not be removed safely: "
                                                                        "the relationship was "
                                                                        "kept."))
                else:
                    pkg.act(Act.REMOVED, rn, label, before, "", why)
    if http:
        pkg.act(Act.KEPT, "*.rels", tr("External http(s) hyperlinks"), tr("%d pcs") % http, "",
                tr("This is document content, not metadata: left alone. Corporate SharePoint and "
                   "OneDrive addresses with the account name in them remain."))


# --- structure: Content_Types, ZIP level ------------------------------------------------------------


def _sec_content_types(pkg):
    name = "[Content_Types].xml"
    if not pkg.exists(name):
        raise _Bad(tr("the package has no [Content_Types].xml"))
    gone = {n for n in pkg.drop if n in pkg.nameset}
    P = pkg.patch(name)
    left = {n.rsplit(".", 1)[-1].lower() for n in pkg.names if n not in pkg.drop and "." in n.rsplit("/", 1)[-1]}
    ext_gone = {n.rsplit(".", 1)[-1].lower() for n in gone if "." in n.rsplit("/", 1)[-1]}
    dropped = []
    for n in P.by("Override"):
        part = (n.get("PartName") or "").lstrip("/")
        if part in gone:
            P.remove(n)
            dropped.append(part)
        elif pkg.paranoid and (n.get("ContentType") or "") in _MACRO_CT and any(
                p.lower().endswith("vbaproject.bin") for p in gone):
            P.set_attr(n, "ContentType", _MACRO_CT[n.get("ContentType")])
    for n in P.by("Default"):
        if (n.get("Extension") or "").lower() in ext_gone - left:
            P.remove(n)
    if pkg.commit(name, P):
        pkg.act(Act.KEPT, name, "[Content_Types].xml", "", "",
                tr("The part was kept. Only the Overrides of removed parts (%d) and Defaults for "
                   "extensions no longer in the package were removed.") % len(dropped))
    else:
        pkg.act(Act.KEPT, name, "[Content_Types].xml", "", "", tr("The part was kept as is, byte "
                                                                  "for byte."))


_STRUCT_KEPT = (
    ("word/theme/theme1.xml", N_("Theme: its absence means \"not created by Word\".")),
    ("xl/theme/theme1.xml", N_("Theme: its absence means \"not created by Excel\".")),
    ("ppt/theme/theme1.xml", N_("Theme: its absence means \"not created by PowerPoint\".")),
    ("word/fontTable.xml", N_("Font table: a part required for a file from Word.")),
    ("word/styles.xml", N_("Styles: document content.")),
    ("word/webSettings.xml", N_("Web settings: a part required for a file from Word.")),
    ("xl/styles.xml", N_("Styles: workbook content.")),
)


def _sec_structure(pkg):
    for nm, why in _STRUCT_KEPT:
        if pkg.exists(nm):
            pkg.act(Act.KEPT, nm, nm.rsplit("/", 1)[-1], "", "", tr("Structural: kept in every "
                                                                    "profile. ") + tr(why))


_TIME_EXTRAS = {0x5455: "UT", 0x5855: "Unix", 0x7855: "Unix2", 0x000A: "NTFS"}


def _extra_times(zf):
    """Entries whose extra fields hold the exact time (UT, NTFS, Unix): it survives normalizing
    the DOS time to 1980-01-01. Office doesn't write such fields; archivers add them on manual repacking."""
    hit = 0
    for i in zf.infolist():
        ex, q = i.extra, 0
        while q + 4 <= len(ex):
            hid, hs = struct.unpack_from("<HH", ex, q)
            if hid in _TIME_EXTRAS:
                hit += 1
                break
            q += 4 + hs
    return hit


def _plan_times(pkg):
    """Whether ZIP timestamps should be normalized to 1980-01-01. The decision is made here; _set_times executes it."""
    ents = zipfix.entries(pkg.res.src)
    odd = [e for e in ents if tuple(e.date_time) != EPOCH]
    extra = _extra_times(pkg.zf)
    if extra:
        pkg.act(Act.UNREMOVABLE, "ZIP", tr("Exact time in the entries' extra fields"), tr("%d of %d entries") % (extra, len(ents)),
                note=tr("UT/NTFS/Unix fields in ZIP headers store the save time to the second "
                        "and survive normalizing the DOS time. They can't be removed without "
                        "rebuilding the headers, and rebuilding would give the cleaning away: "
                        "the fields were kept."))
    if not odd:
        return False
    low = pkg.appname.lower()
    ms = any(w in low for w in ("microsoft", "word", "excel", "powerpoint"))
    if ms or not pkg.appname or pkg.paranoid:
        lo = min(e.date_time for e in odd)
        hi = max(e.date_time for e in odd)
        pkg.act(Act.NORMALIZED, "ZIP", tr("Container entry timestamps"),
                tr("%04d-%02d-%02d %02d:%02d:%02d … %04d-%02d-%02d %02d:%02d:%02d (%d of %d "
                   "entries)")
                % (tuple(lo) + tuple(hi) + (len(odd), len(ents))), "1980-01-01 00:00:00",
                tr("Office sets 1980-01-01 00:00 on every entry. Real times mean the package was "
                   "built by a library: the working time leaks through the container even with a "
                   "clean core.xml. Normalized to Word's form (this isn't substituting the "
                   "document's date but a container artifact)."))
        return True
    pkg.act(Act.KEPT, "ZIP", tr("Container entry timestamps"), tr("%d of %d entries with real "
                                                                 "times")
            % (len(odd), len(ents)), "", tr("Application=%s: a different application that may "
                                            "write real times itself; normalizing to 1980-01-01 "
                                            "would break the profile's consistency. The save "
                                            "time thus remains visible through the container.") % clip(pkg.appname, 60))
    return False


def _set_times(path):
    """Write 1980-01-01 00:00:00 into the local headers and the central directory in place
    (compressed data is untouched). Raises on anything unexpected: then no file is handed over."""
    ents = zipfix.entries(path)
    with zipfile.ZipFile(path) as zf:
        cd = zf.start_dir
    with open(path, "r+b") as fh:
        for e in ents:
            fh.seek(e.header_offset + 10)
            fh.write(struct.pack("<HH", 0, 0x21))
        fh.seek(cd)
        for _ in ents:
            hdr = fh.read(46)
            if len(hdr) != 46 or hdr[:4] != b"PK\x01\x02":
                raise _Bad(tr("the central directory parsed differently than expected"))
            n, m, k = struct.unpack_from("<3H", hdr, 28)
            at = fh.tell() - 46
            fh.seek(at + 12)
            fh.write(struct.pack("<HH", 0, 0x21))
            fh.seek(at + 46 + n + m + k)


# ============================================================================
#  Verifying the result (written here, not trusted to zipfix or our own edits)
# ============================================================================


def verify_ooxml(src, dst) -> dict:
    """Independent check of the output package against the input. Never raises.

    ok=True if: the ZIP opens and passes testzip(); [Content_Types].xml is present (and first if it
    was first in the input); entry order is the same apart from removed entries; every REPLACED XML
    part parses strictly; no relationship and no Override points to a part we removed; no replaced
    part references by r:id a relationship that no longer exists.
    The rest are invisibility metrics (times_same, order_preserved): they aren't conditions for ok,
    because normalizing timestamps to 1980-01-01 is a deliberate action."""
    out = {"ok": False, "opens": False, "testzip": False, "content_types_present": False,
           "content_types_first": False, "order_preserved": False, "times_same": False,
           "entries_src": 0, "entries_dst": 0, "dropped": [], "edited": [], "missing_targets": [],
           "broken_rels": [], "broken_overrides": [], "bad_xml": [], "dangling_refs": [], "errors": []}
    try:
        es, ed = zipfix.entries(src), zipfix.entries(dst)
        out["entries_src"], out["entries_dst"] = len(es), len(ed)
        sn, dn = [e.name for e in es], [e.name for e in ed]
        sset, dset = set(sn), set(dn)
        out["dropped"] = [n for n in sn if n not in dset]
        out["order_preserved"] = dn == [n for n in sn if n in dset]
        sdt = {e.name: tuple(e.date_time) for e in es}
        out["times_same"] = all(sdt.get(e.name) == tuple(e.date_time) for e in ed)
        scrc = {e.name: e.crc for e in es}
        out["edited"] = [e.name for e in ed if scrc.get(e.name) != e.crc]
        out["content_types_present"] = "[Content_Types].xml" in dset
        out["content_types_first"] = dn[:1] == ["[Content_Types].xml"]
        with zipfile.ZipFile(dst) as zf, zipfile.ZipFile(src) as zs:
            out["opens"] = True
            out["testzip"] = zf.testzip() is None
            for rn in dn:
                if not rn.endswith(".rels"):
                    continue
                for n in _parse(zf.read(rn)):
                    if n.name != "Relationship":
                        continue
                    tgt, mode = n.get("Target") or "", n.get("TargetMode") or ""
                    if mode == "External" or tgt.startswith("#") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", tgt):
                        continue
                    part = _resolve(rn, tgt)
                    if part and part not in dset:
                        out["missing_targets"].append("%s -> %s" % (rn, part))
                        if part in sset:
                            out["broken_rels"].append("%s -> %s" % (rn, part))
            if "[Content_Types].xml" in dset:
                for n in _parse(zf.read("[Content_Types].xml")):
                    part = (n.get("PartName") or "").lstrip("/") if n.name == "Override" else ""
                    if part and part not in dset and part in sset:
                        out["broken_overrides"].append(part)
            for nm in out["edited"]:
                if nm.endswith((".xml", ".rels", ".vml")):
                    try:
                        _well_formed(zf.read(nm))
                    except _Bad as exc:
                        out["bad_xml"].append("%s: %s" % (nm, exc))
            # References to removed relationships: check the owner of EVERY .rels that changed or
            # disappeared, even if the owner itself wasn't edited (it may have been forgotten).
            for rn in sn:
                if not rn.endswith(".rels"):
                    continue
                owner = _rels_owner(rn)
                if not owner or owner not in dset or not owner.endswith((".xml", ".vml")):
                    continue
                lost = _rel_ids(zs, sset, owner) - _rel_ids(zf, dset, owner)
                if not lost:
                    continue
                raw = zf.read(owner)
                if not any(q + r.encode("utf-8") + q in raw for r in lost for q in (b'"', b"'")):
                    continue                          # no removed relationships appear in the text: nothing to parse
                for n in _parse(raw, lambda _ns, _l, a: any(x[0] in R_NSES for x in a)):
                    for ans, _aloc, v in n.attrs:
                        if ans in R_NSES and v in lost:
                            out["dangling_refs"].append("%s: %s" % (owner, v))
    except Exception as exc:  # noqa: BLE001 -- verification never raises
        out["errors"].append(tr("verification didn't finish: %s") % type(exc).__name__)
        return out
    if not out["opens"]:
        out["errors"].append(tr("the result doesn't open as ZIP"))
    if not out["testzip"]:
        out["errors"].append(tr("the result fails the CRC check"))
    if not out["content_types_present"]:
        out["errors"].append(tr("the result has no [Content_Types].xml"))
    if sn[:1] == ["[Content_Types].xml"] and not out["content_types_first"]:
        out["errors"].append(tr("[Content_Types].xml is no longer the first entry"))
    if not out["order_preserved"]:
        out["errors"].append(tr("entry order doesn't match the source"))
    for key, text in (("broken_rels", tr("a relationship points to a removed part")),
                      ("broken_overrides", tr("an Override points to a removed part")),
                      ("bad_xml", tr("a replaced part doesn't parse")),
                      ("dangling_refs", tr("a part still has an r:id of a removed relationship"))):
        for item in out[key][:5]:
            out["errors"].append("%s: %s" % (text, item))
    out["ok"] = not out["errors"]
    return out


def _rel_ids(zf, names, part):
    """Relationship Ids of part (from its .rels in zf); empty if there's no .rels."""
    rn = _rels_name(part)
    if rn not in names:
        return set()
    try:
        return {n.get("Id") for n in _parse(zf.read(rn)) if n.name == "Relationship"}
    except _Bad:
        return set()


# ============================================================================
#  Entry point
# ============================================================================

_SECTIONS = (
    (N_("Relationships (.rels)"), lambda pkg: pkg.index_rels()),
    (N_("Package signature"), _sec_signature),
    (N_("The document's personal strings"), _sec_idents),
    ("docProps/core.xml", _sec_core),
    ("docProps/app.xml", _sec_app),
    ("docProps/custom.xml", _sec_custom),
    (N_("Thumbnail"), _sec_thumbnail),
    ("word/people.xml", _sec_people),
    ("xl/revisions", _sec_xl_revisions),
    (N_("Printer settings"), _sec_printer),
    (N_("VBA macros"), _sec_vba),
    (N_("Embedded objects"), _sec_embeddings),
    (N_("Images"), _sec_media),
    ("word/settings.xml", _sec_settings),
    (N_("Document body"), _sec_body),
    (N_("Comments and authors"), _sec_persons),
    ("xl/workbook.xml", _sec_workbook),
    (N_("Sheet and presentation protection"), _sec_protection),
    (N_("Pivot tables"), _sec_pivot),
    (N_("Excel connections"), _sec_connections),
    ("customXml", _sec_customxml),
    (N_("External links"), _sec_extrels),
    ("[Content_Types].xml", _sec_content_types),
    (N_("Structure"), _sec_structure),
)

_UNREADABLE = {
    "cloud": N_("the file is a cloud provider placeholder: there's no content on disk."),
    "denied": N_("no read access: no permission, or the file is locked by another process."),
    "missing": N_("file not found."),
    "dir": N_("this is a directory, not a file."),
    "reparse": N_("the file is a link or reparse point."),
}


def _first_broken(zf):
    """Name of the first entry that won't decompress or fails the CRC (None if all are intact)."""
    for i in zf.infolist():
        try:
            with zf.open(i) as fh:
                while fh.read(1 << 20):
                    pass
        except Exception:  # noqa: BLE001 -- zlib.error, BadZipFile, NotImplementedError
            return i.filename
    return None


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _same_file(a, b):
    try:
        if os.path.exists(b) and os.path.samefile(a, b):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.realpath(os.path.abspath(a))) == \
        os.path.normcase(os.path.realpath(os.path.abspath(b)))


def _run(res, state, src, dst, profile, keep, force_signed, depth):
    try:
        prof = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        res.err(tr("Unknown cleaning profile."))
        return
    res.profile = prof.value
    if _same_file(src, dst):
        res.err(tr("The output path matches the input path: the input file is never overwritten."))
        return
    fmt = sniff.sniff(src)
    res.fmt = fmt
    if fmt == "unreadable":
        reason, code = sniff.unreadable_reason(src)
        res.err(tr("File not cleaned: %s") % tr(_UNREADABLE.get(reason, tr("the file can't be read (errno "
                                                                  "%s).") % code)))
        return
    size = os.path.getsize(src)
    if size == 0:
        res.err(tr("The file is empty (0 bytes); it may not have finished downloading. File not "
                   "cleaned."))
        return
    ents = zipfix.entries(src)
    if fmt not in ("docx", "xlsx", "pptx"):
        if fmt == "zip" and not ents:
            res.err(tr("The ZIP container doesn't parse: the file is damaged or truncated. File "
                       "not cleaned."))
        else:
            res.err(tr("Not an OOXML package (format \"%s\"): docx/xlsx/pptx cleaning doesn't "
                       "apply to it.") % sniff.pretty(fmt))
        return
    if size > MAX_INPUT:
        res.err(tr("File larger than %d MB: cleaning not performed.") % (MAX_INPUT >> 20))
        return
    if not ents:
        res.err(tr("The ZIP container doesn't parse: the file is damaged, truncated or not a ZIP."))
        return
    unc, cmp_ = sum(e.file_size for e in ents), sum(e.compress_size for e in ents)
    if unc > zipfix.MAX_TOTAL_UNPACKED or (unc > zipfix.MIN_BOMB_UNPACKED and unc / max(cmp_, 1) > zipfix.MAX_RATIO):
        res.err(tr("Suspected zip bomb: %d bytes uncompressed from %d compressed. File not "
                   "cleaned.") % (unc, cmp_))
        return
    sha_before = _sha(src)
    state["tmp"] = tempfile.mkdtemp(prefix=".ooxml_")
    state["work"] = dst + ".ooxml"
    try:
        zf = zipfile.ZipFile(src)
    except Exception as exc:  # noqa: BLE001
        res.err(tr("The ZIP container won't open: %s.") % type(exc).__name__)
        return
    with zf:
        pkg = _Pkg(res, zf, prof, keep, depth, state["tmp"], force_signed)
        pkg.drop_custom = bool(state.get("drop_custom"))
        for title, fn in _SECTIONS:
            _step(pkg, tr(title), fn)
        _step(pkg, tr("ZIP timestamps"), lambda p: state.__setitem__("times", _plan_times(p)))
        if any(a.act is Act.FAILED for a in res.actions):
            res.err(tr("No file handed over: some cleaning sections failed, cleanliness is not "
                       "guaranteed."))
            return
        replace, drop = pkg.result()
        rep = zipfix.rebuild(src, state["work"], replace=replace, drop=drop, strip_slack=True)
        if not rep.ok:
            broken = _first_broken(zf)
            if broken:
                res.err(tr("The source file is damaged: entry \"%s\" can't be read or fails the "
                           "CRC check.") % clip(broken, 80))
            for e in rep.errors:
                res.err(e)
            return
        if rep.slack_bytes or rep.comment_len:
            res.act(Act.REMOVED, "ZIP", tr("Hidden bytes outside container entries"),
                    tr("%d bytes") % rep.slack_bytes, "",
                    tr("The file head, gaps between entries, junk after the end of the "
                       "archive%s. Office doesn't write them: it's a classic hiding place.")
                    % (tr("; archive comment %d bytes") % rep.comment_len if rep.comment_len else ""))
    if state.get("times"):
        _set_times(state["work"])
    v = verify_ooxml(src, state["work"])
    if not v["ok"]:
        for e in v["errors"]:
            res.err(e)
        res.err(tr("Verification of the result failed: no file handed over."))
        return
    if _sha(src) != sha_before:
        res.err(tr("The input file changed during processing: the result is unreliable, no file "
                   "handed over."))
        return
    os.replace(state["work"], dst)
    res.ok = True


def clean_ooxml(src, dst, profile=Profile.STEALTH, keep=None, *, force_signed=False, _depth=0,
                drop_custom=False) -> CleanResult:
    """Clean docx/xlsx/pptx src -> dst. The input is never modified. Never raises: on refusal
    ok=False, the reason is in errors, dst is not created and an existing dst is untouched.

    ok=True means: dst was written and passes verify_ooxml (the ZIP opens, testzip, parts and
    references are intact, replaced parts parse strictly), the input didn't change. That isn't
    what proves cleanliness: the dispatcher's second inspect_file(dst) does.

    profile is STEALTH (default) or PARANOID; REGENERATE is treated as PARANOID.
    keep is the whitelist (see the module docstring). force_signed cleans a signed package,
    destroying the signature. _depth is internal: the nesting depth when recursing into
    embeddings/.
    drop_custom removes ALL custom properties (docProps/custom.xml) in STEALTH too: for the
    second pass after regeneration, where any such property was carried over from the source."""
    res = CleanResult(src=str(src), dst=str(dst), fmt="unknown",
                      profile=str(getattr(profile, "value", profile)))
    state = {"drop_custom": drop_custom}
    try:
        s, d = os.fspath(src), os.fspath(dst)
        os.makedirs(os.path.dirname(os.path.abspath(d)), exist_ok=True)
        _run(res, state, s, d, profile, keep, force_signed, _depth)
    except _Refuse as exc:
        res.err(exc)
    except Exception as exc:  # noqa: BLE001 -- nothing may reach the user
        res.err(tr("Internal OOXML cleaning error: %s: %s") % (type(exc).__name__, exc))
    finally:
        if state.get("tmp"):
            shutil.rmtree(state["tmp"], ignore_errors=True)
        for p in (state.get("work"), (state.get("work") or "") + ".part"):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass
    if not res.ok:
        # No file handed over: a list of "what was done" to a file that wasn't handed over only misleads.
        res.actions[:] = [a for a in res.actions if a.act is Act.FAILED]
    return res


# ============================================================================
#  Self-test and CLI:
#      PYTHONIOENCODING=utf-8 python -m core.clean_ooxml                 -- synthetic data
#      PYTHONIOENCODING=utf-8 python -m core.clean_ooxml --real file...  -- metrics on real
#                                                                           files (contents not printed)
# ============================================================================

_D = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}
_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"

# Known leaks planted in advance: none of these (longer than 5 characters) may survive cleaning.
K = {
    "creator": "Иванов Иван Иванович", "lmb": "Петрова Анна Сергеевна",
    "company": "ООО Ромашка-Тест", "manager": "Сидоров Сергей Петрович",
    "msip": "a.smirnova@romashka-test.ru", "site": "72f988bf-86f1-41af-91ab-2d7cd011db47",
    "ctid": "0x0101009ABC1234567890", "tpl": "file:///C:/Users/ivanov/AppData/Roaming/Microsoft/Templates/Otchet.dotm",
    "mm": "file:///C:/Users/ivanov/Documents/clients.xlsx",
    "conn": "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=\\\\FS01\\buh\\clients.xlsx",
    "hash": "Aq3fGh7KzXw9Pq2LmN4oQ5rS6tU7vW8xYz==", "salt": "Zz09aBcDeFg1HiJk",
    "docvar": "\\\\FS01\\buh\\baza.accdb", "rev": "Кузнецова Анастасия Викторовна",
    "ed": "petrov@romashka-test.ru", "pres": "a.kuznetsova@romashka-test.ru",
    "hdr": "Смирнов Дмитрий Дмитриевич", "printer": "HP LaserJet M404 FLOOR3-NYC",
    "mailto": "mailto:hr@romashka-test.ru", "filelink": "file:///C:/Users/ivanov/Documents/spec.pdf",
    "emb": "Встроенный Автор Табличный",
}


def _zip_write(path, parts, dt=EPOCH, stored=()):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts:
            if isinstance(body, str):
                body = body.encode("utf-8")
            zi = zipfile.ZipInfo(name, dt)
            zi.compress_type = zipfile.ZIP_STORED if name in stored else zipfile.ZIP_DEFLATED
            zf.writestr(zi, body)
    return path


def _rels_xml(items):
    """items: [(Id, Type tail or full Type, Target, External?)]"""
    out = [_D, '<Relationships xmlns="%s">' % _NS["rel"]]
    for rid, typ, tgt, ext in items:
        typ = typ if typ.startswith("http") else _RT + typ
        out.append('<Relationship Id="%s" Type="%s" Target="%s"%s/>'
                   % (rid, typ, tgt.replace("&", "&amp;"), ' TargetMode="External"' if ext else ""))
    out.append("</Relationships>")
    return "".join(out)


def _ct_xml(defaults, overrides):
    out = [_D, '<Types xmlns="%s">' % _NS["ct"]]
    out += ['<Default Extension="%s" ContentType="%s"/>' % kv for kv in defaults]
    out += ['<Override PartName="%s" ContentType="%s"/>' % kv for kv in overrides]
    out.append("</Types>")
    return "".join(out)


def _devmode(name):
    raw = name.encode("utf-16le")
    return raw + b"\x00" * (64 - len(raw)) + b"\x01\x00\x00\x00" * 40


def _core(creator, lmb, title="", extra="", created="2024-03-01T10:00:00Z", modified="2024-03-05T14:30:00Z"):
    return (_D + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>%s</dc:title><dc:creator>%s</dc:creator><cp:keywords>отчёт; квартал</cp:keywords>'
            '<cp:lastModifiedBy>%s</cp:lastModifiedBy><cp:revision>7</cp:revision>%s'
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>'
            % (title, creator, lmb, extra, created, modified))


def _app(app="Microsoft Office Word", ver="16.0000", company="", manager=""):
    return (_D + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            '<Template>Normal.dotm</Template><TotalTime>187</TotalTime><Pages>4</Pages><Words>1250</Words>'
            '<Characters>7100</Characters><Application>%s</Application><DocSecurity>0</DocSecurity>'
            '<Lines>60</Lines><Paragraphs>14</Paragraphs><ScaleCrop>false</ScaleCrop><Manager>%s</Manager>'
            '<Company>%s</Company><LinksUpToDate>false</LinksUpToDate><CharactersWithSpaces>8300</CharactersWithSpaces>'
            '<SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged><AppVersion>%s</AppVersion>'
            '</Properties>' % (app, manager, company, ver))


def _custom(props):
    out = [_D, '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
           'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">']
    for i, (n, v) in enumerate(props, 2):
        out.append('<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="%d" name="%s">'
                   '<vt:lpwstr>%s</vt:lpwstr></property>' % (i, n, v))
    out.append("</Properties>")
    return "".join(out)


def _tiny_jpeg(leaky=True):
    """A JPEG with EXIF (GPS, serial): from the project fixtures. Without tests.fixtures, a minimal JFIF."""
    try:
        from tests.fixtures import make_jpeg
        d = tempfile.mkdtemp(prefix="oox_jpg_")
        try:
            with open(make_jpeg(d)[0], "rb") as fh:
                return fh.read()
        finally:
            shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001
        return (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                b"\xff\xd9")


def _fx_embedded_xlsx():
    """An embedded workbook (like a chart in Word): it has its own docProps with its own author."""
    ct = _ct_xml([("rels", "application/vnd.openxmlformats-package.relationships+xml"),
                  ("xml", "application/xml")],
                 [("/xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
                  ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml")])
    rels = _rels_xml([("rId1", "officeDocument", "xl/workbook.xml", False),
                      ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                       "docProps/core.xml", False)])
    wb = _D + '<workbook xmlns="%s"><sheets><sheet name="Лист1" sheetId="1"/></sheets></workbook>' % _NS["x"]
    tmp = os.path.join(tempfile.mkdtemp(prefix="oox_emb_"), "e.xlsx")
    try:
        _zip_write(tmp, [("[Content_Types].xml", ct), ("_rels/.rels", rels), ("xl/workbook.xml", wb),
                         ("docProps/core.xml", _core(K["emb"], K["emb"]))])
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)


def _fx_docx(dt=EPOCH, ole=False, app="Microsoft Office Word", all_leaky_custom=False, jpeg=None):
    """A Word-like .docx with every leak from the requirements list. ole=True adds an embedded OLE .bin
    and an embedded .xlsx workbook."""
    W = _NS["w"]
    jpeg = jpeg or _tiny_jpeg()
    doc = (_D + '<w:document xmlns:w="%s" xmlns:r="%s" xmlns:o="urn:schemas-microsoft-com:office:office" '
           'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:mc="%s" '
           'xmlns:w16du="http://schemas.microsoft.com/office/word/2023/wordml/word16du" mc:Ignorable="w16du">'
           '<w:body><w:p w:rsidR="00B54A06" w:rsidRDefault="00B54A06"><w:bookmarkStart w:id="0" w:name="_GoBack"/>'
           '<w:bookmarkEnd w:id="0"/>'
           '<w:ins w:id="1" w:author="%s" w:date="2024-05-01T10:00:00Z" w16du:dateUtc="2024-05-01T07:00:00Z">'
           '<w:r><w:t>вставленный текст</w:t></w:r></w:ins>'
           '<w:del w:id="2" w:author="%s" w:date="2024-05-01T10:01:00Z"><w:r w:rsidDel="006D6EA0">'
           '<w:delText>удалённый текст</w:delText></w:r></w:del>'
           '<w:permStart w:id="3" w:ed="%s" w:edGrp="editors"/><w:r><w:t>защищённая область</w:t></w:r>'
           '<w:permEnd w:id="3"/>'
           '<w:hyperlink r:id="rId20" w:history="1"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr>'
           '<w:t>написать в отдел кадров</w:t></w:r></w:hyperlink>'
           '<w:hyperlink r:id="rId25" w:history="1"><w:r><w:t>сайт</w:t></w:r></w:hyperlink>'
           '</w:p><w:sectPr w:rsidR="00B54A06" w:rsidSect="003E602A"><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>'
           '</w:body></w:document>' % (W, _NS["r"], _NS["mc"], K["rev"], K["rev"], K["ed"]))
    if ole:
        doc = doc.replace("</w:p><w:sectPr", (
            '<w:r><w:object w:dxaOrig="1440" w:dyaOrig="1440"><v:shape id="_x0000_i1025" type="#_x0000_t75">'
            '<v:imagedata r:id="rId21" o:title=""/></v:shape><o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" '
            'ShapeID="_x0000_i1025" DrawAspect="Content" ObjectID="_1" r:id="rId22"/></w:object></w:r>'
            '<w:r><w:object><o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" r:id="rId26"/></w:object></w:r>'
            '</w:p><w:sectPr'))
    rels = [("rId1", "styles", "styles.xml", False), ("rId2", "settings", "settings.xml", False),
            ("rId3", "theme", "theme/theme1.xml", False), ("rId4", "fontTable", "fontTable.xml", False),
            ("rId5", "webSettings", "webSettings.xml", False),
            ("rId6", "http://schemas.microsoft.com/office/2011/relationships/people", "people.xml", False),
            ("rId7", "comments", "comments.xml", False), ("rId8", "header", "header1.xml", False),
            ("rId20", "hyperlink", K["mailto"], True), ("rId21", "image", "media/image1.jpeg", False),
            ("rId23", "printerSettings", "printerSettings/printerSettings1.bin", False),
            ("rId24", "hyperlink", K["filelink"], True), ("rId25", "hyperlink", "https://example.org/", True)]
    if ole:
        rels += [("rId22", "oleObject", "embeddings/oleObject1.bin", False),
                 ("rId26", "package", "embeddings/Microsoft_Excel_Worksheet.xlsx", False)]
    settings = (_D + '<w:settings xmlns:w="%s" xmlns:r="%s" xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
                '<w:zoom w:percent="100"/><w:trackRevisions/>'
                '<w:documentProtection w:edit="readOnly" w:enforcement="1" w:cryptProviderType="rsaAES" '
                'w:cryptAlgorithmClass="hash" w:hash="%s" w:salt="%s" w:cryptSpinCount="100000"/>'
                '<w:proofState w:spelling="clean" w:grammar="clean"/>'
                '<w:attachedTemplate r:id="rId1"/><w:rsids><w:rsidRoot w:val="00B54A06"/><w:rsid w:val="00B54A06"/>'
                '<w:rsid w:val="006D6EA0"/></w:rsids>'
                '<w:docVars><w:docVar w:name="База" w:val="%s"/><w:docVar w:name="Стадия" w:val="черновик"/></w:docVars>'
                '<w:mailMerge><w:mainDocumentType w:val="formLetters"/><w:dataType w:val="native"/>'
                '<w:connectString w:val="%s"/><w:dataSource r:id="rId2"/></w:mailMerge>'
                '<w15:docId w15:val="{6D2B3E1C-1111-4222-8333-AAAABBBBCCCC}"/></w:settings>'
                % (W, _NS["r"], K["hash"], K["salt"], K["docvar"], K["conn"].replace("\\", "\\")))
    comments = (_D + '<w:comments xmlns:w="%s"><w:comment w:id="0" w:author="%s" w:date="2024-05-02T09:00:00Z" '
                'w:initials="КА"><w:p><w:r><w:t>комментарий</w:t></w:r></w:p></w:comment></w:comments>' % (W, K["rev"]))
    header = (_D + '<w:hdr xmlns:w="%s"><w:p w:rsidR="00C11111"><w:ins w:id="9" w:author="%s" '
              'w:date="2024-05-03T09:00:00Z"><w:r><w:t>колонтитул</w:t></w:r></w:ins></w:p></w:hdr>' % (W, K["hdr"]))
    people = (_D + '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
              '<w15:person w15:author="%s"><w15:presenceInfo w15:providerId="AD" w15:userId="%s"/></w15:person>'
              '</w15:people>' % (K["rev"], K["pres"]))
    cext = (_D + '<w16cex:commentsExtensible xmlns:w16cex="http://schemas.microsoft.com/office/word/2018/wordml/cex">'
            '<w16cex:commentExtensible w16cex:durableId="1A2B3C4D" w16cex:dateUtc="2024-05-02T06:00:00Z"/>'
            '</w16cex:commentsExtensible>')
    styles = (_D + '<w:styles xmlns:w="%s"><w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:rsid w:val="00B54A06"/></w:style></w:styles>' % W)
    settings_rels = _rels_xml([("rId1", "attachedTemplate", K["tpl"], True),
                               ("rId2", "mailMergeSource", K["mm"], True)])
    props = [("MSIP_Label_9f1_SetBy", K["msip"]), ("MSIP_Label_9f1_SiteId", K["site"]),
             ("ContentTypeId", K["ctid"])]
    if not all_leaky_custom:
        props += [("ProjectCode", "ALPHA-1")]
    label = (_D + '<clbl:labelList xmlns:clbl="http://schemas.microsoft.com/office/2020/mipLabelMetadata">'
             '<clbl:label id="{9f1}" siteId="{%s}" setBy="%s" enabled="1"/></clbl:labelList>' % (K["site"], K["msip"]))
    defaults = [("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml"),
                ("jpeg", "image/jpeg")]
    overrides = [("/word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
                 ("/word/styles.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"),
                 ("/word/settings.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"),
                 ("/word/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
                 ("/word/fontTable.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"),
                 ("/word/webSettings.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.webSettings+xml"),
                 ("/word/people.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.people+xml"),
                 ("/word/comments.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"),
                 ("/word/commentsExtensible.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtensible+xml"),
                 ("/word/header1.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"),
                 ("/word/printerSettings/printerSettings1.bin", "application/vnd.openxmlformats-officedocument.wordprocessingml.printerSettings"),
                 ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
                 ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml"),
                 ("/docProps/custom.xml", "application/vnd.openxmlformats-officedocument.custom-properties+xml"),
                 ("/docMetadata/LabelInfo.xml", "application/vnd.ms-office.classificationlabels+xml")]
    if ole:
        defaults += [("bin", "application/vnd.openxmlformats-officedocument.oleObject"),
                     ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")]
    pkg_rels = _rels_xml([("rId1", "officeDocument", "word/document.xml", False),
                          ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                           "docProps/core.xml", False),
                          ("rId3", _RT + "extended-properties", "docProps/app.xml", False),
                          ("rId4", _RT + "custom-properties", "docProps/custom.xml", False),
                          ("rId5", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail",
                           "docProps/thumbnail.jpeg", False),
                          ("rId6", "http://schemas.microsoft.com/office/2020/02/relationships/classificationlabels",
                           "docMetadata/LabelInfo.xml", False)])
    parts = [("[Content_Types].xml", _ct_xml(defaults, overrides)), ("_rels/.rels", pkg_rels),
             ("word/_rels/document.xml.rels", _rels_xml(rels)), ("word/document.xml", doc),
             ("word/theme/theme1.xml", _D + "<a:theme xmlns:a=\"%s\" name=\"Office\"/>" % _NS["a"]),
             ("word/settings.xml", settings), ("word/_rels/settings.xml.rels", settings_rels),
             ("word/fontTable.xml", _D + '<w:fonts xmlns:w="%s"/>' % W),
             ("word/webSettings.xml", _D + '<w:webSettings xmlns:w="%s"/>' % W),
             ("docProps/core.xml", _core(K["creator"], K["lmb"], title="Отчёт %s" % K["creator"])),
             ("word/styles.xml", styles), ("word/people.xml", people), ("word/comments.xml", comments),
             ("word/commentsExtensible.xml", cext), ("word/header1.xml", header),
             ("word/printerSettings/printerSettings1.bin", _devmode(K["printer"])),
             ("word/media/image1.jpeg", jpeg), ("docProps/thumbnail.jpeg", jpeg),
             ("docProps/custom.xml", _custom(props)), ("docMetadata/LabelInfo.xml", label),
             ("docProps/app.xml", _app(app, "16.0000", K["company"], K["manager"]))]
    if ole:
        try:
            from tests.fixtures import make_doc
            d = tempfile.mkdtemp(prefix="oox_ole_")
            try:
                with open(make_doc(d)[0], "rb") as fh:
                    ole_bytes = fh.read()
            finally:
                shutil.rmtree(d, ignore_errors=True)
        except Exception:  # noqa: BLE001
            ole_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
        parts += [("word/embeddings/oleObject1.bin", ole_bytes),
                  ("word/embeddings/Microsoft_Excel_Worksheet.xlsx", _fx_embedded_xlsx())]
    return parts


def _fx_xlsx(dt=EPOCH):
    X = _NS["x"]
    sheet1 = (_D + '<worksheet xmlns="%s" xmlns:r="%s" xmlns:mc="%s" mc:Ignorable="x14ac" '
              'xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"><sheetData>'
              '<row r="1" x14ac:dyDescent="0.25"><c r="A1" t="s"><v>0</v></c></row></sheetData>'
              '<hyperlinks><hyperlink ref="A1" r:id="rId1"/><hyperlink ref="A2" r:id="rId2"/></hyperlinks>'
              '<pageSetup orientation="portrait" r:id="rId3"/><drawing r:id="rId4"/></worksheet>'
              % (X, _NS["r"], _NS["mc"]))
    sheet2 = (_D + '<worksheet xmlns="%s" xmlns:r="%s"><sheetData/><sheetProtection sheet="1" password="CC1A" '
              'algorithmName="SHA-512" hashValue="%s" saltValue="%s" spinCount="100000" objects="1"/>'
              '<pageSetup orientation="landscape" r:id="rId1"/></worksheet>' % (X, _NS["r"], K["hash"], K["salt"]))
    wb = (_D + '<workbook xmlns="%s" xmlns:r="%s" xmlns:mc="%s" mc:Ignorable="x15" '
          'xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main">'
          '<fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="10531"/>'
          '<fileSharing userName="Иванов Иван Иванович" reservationPassword="CC1A"/><workbookPr codeName="ThisWorkbook"/>'
          '<mc:AlternateContent xmlns:mc="%s"><mc:Choice Requires="x15">'
          '<x15ac:absPath url="/Users/ivanov/Sites/scorecard/public/files/" '
          'xmlns:x15ac="http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"/></mc:Choice></mc:AlternateContent>'
          '<workbookProtection workbookAlgorithmName="SHA-512" workbookHashValue="%s" workbookSaltValue="%s" '
          'workbookSpinCount="100000" lockStructure="1"/>'
          '<sheets><sheet name="Данные" sheetId="1" r:id="rId1"/><sheet name="Скрытый_Лист" sheetId="2" state="hidden" r:id="rId2"/>'
          '</sheets><definedNames><definedName name="Внешняя">\'C:\\Users\\ivanov\\Documents\\[budget.xlsx]Sheet1\'!$A$1</definedName>'
          '<definedName name="Таблица">Table1[Сумма]</definedName></definedNames>'
          '<externalReferences><externalReference r:id="rId5"/></externalReferences></workbook>'
          % (X, _NS["r"], _NS["mc"], _NS["mc"], K["hash"], K["salt"]))
    wb_rels = _rels_xml([("rId1", "worksheet", "worksheets/sheet1.xml", False),
                         ("rId2", "worksheet", "worksheets/sheet2.xml", False),
                         ("rId3", "styles", "styles.xml", False), ("rId4", "theme", "theme/theme1.xml", False),
                         ("rId5", "externalLink", "externalLinks/externalLink1.xml", False),
                         ("rId6", "pivotCacheDefinition", "pivotCache/pivotCacheDefinition1.xml", False),
                         ("rId7", "connections", "connections.xml", False),
                         ("rId8", "http://schemas.microsoft.com/office/2017/10/relationships/person", "persons/person.xml", False)])
    s1_rels = _rels_xml([("rId1", "hyperlink", K["mailto"], True), ("rId2", "hyperlink", "https://example.org/", True),
                         ("rId3", "printerSettings", "../printerSettings/printerSettings1.bin", False),
                         ("rId4", "drawing", "../drawings/drawing1.xml", False)])
    s2_rels = _rels_xml([("rId1", "printerSettings", "../printerSettings/printerSettings2.bin", False)])
    ext = (_D + '<externalLink xmlns="%s" xmlns:r="%s"><externalBook r:id="rId1"><sheetNames><sheetName val="Sheet1"/>'
           '</sheetNames></externalBook></externalLink>' % (X, _NS["r"]))
    ext_rels = _rels_xml([("rId1", "externalLinkPath", "file:///C:\\Users\\ivanov\\Documents\\budget.xlsx", True)])
    pivot = (_D + '<pivotCacheDefinition xmlns="%s" xmlns:r="%s" r:id="rId1" refreshedBy="%s" refreshedDate="45000.5" '
             'createdVersion="6"/>' % (X, _NS["r"], K["rev"]))
    conns = (_D + '<connections xmlns="%s"><connection id="1" name="ERP" description="подключение к ERP" type="1">'
             '<dbPr connection="DRIVER=SQL Server;SERVER=sql01.corp.local;DATABASE=erp;UID=ivanov;Trusted_Connection=yes" '
             'command="SELECT * FROM dbo.Orders"/></connection>'
             '<connection id="2" name="Query - Таблица1" description="Подключение к запросу" type="5">'
             '<dbPr connection="Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location=Таблица1" '
             'command="SELECT * FROM [Таблица1]"/></connection></connections>' % X)
    persons = (_D + '<personList xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments">'
               '<person displayName="%s" id="{11111111-2222-3333-4444-555555555555}" userId="%s" providerId="AD"/>'
               '</personList>' % (K["rev"], K["pres"]))
    thr = (_D + '<ThreadedComments xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments">'
           '<threadedComment ref="A1" dT="2024-05-02T09:00:00.00" personId="{11111111-2222-3333-4444-555555555555}" '
           'id="{99999999-2222-3333-4444-555555555555}"><text>вопрос</text></threadedComment></ThreadedComments>')
    xlc = (_D + '<comments xmlns="%s"><authors><author>%s</author></authors><commentList>'
           '<comment ref="A1" authorId="0"><text><r><t>заметка</t></r></text></comment></commentList></comments>'
           % (X, K["rev"]))
    sp_props = (_D[:-2] + '<p:properties xmlns:p="http://schemas.microsoft.com/office/2006/metadata/properties" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><documentManagement><Reviewer>%s</Reviewer>'
                '</documentManagement></p:properties>' % K["rev"])
    sp_ct = ('<?xml version="1.0" encoding="utf-8"?><ct:contentTypeSchema ct:_="" ma:_="" '
             'ma:contentTypeName="Документ" ma:contentTypeID="0x0101%s" '
             'xmlns:ct="http://schemas.microsoft.com/office/2006/metadata/contentType" '
             'xmlns:ma="http://schemas.microsoft.com/office/2006/metadata/properties/metaAttributes">'
             '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></ct:contentTypeSchema>' % K["site"])
    sp_forms = ('<?mso-contentType?><FormTemplates xmlns="http://schemas.microsoft.com/sharepoint/v3/contenttype/forms">'
                '<Display>DocumentLibraryForm</Display><Edit>DocumentLibraryForm</Edit></FormTemplates>')
    mashup = ('<?xml version="1.0" encoding="utf-8"?><DataMashup xmlns="http://schemas.microsoft.com/DataMashup">'
              'UEsDBBQAAAAIAAAAAAAAAAAAAAAAAAAAAAAJAAAAQ29uZmlnLw==</DataMashup>')

    def props(i, uris):
        return (_D + '<ds:datastoreItem ds:itemID="{%08d-AAAA-BBBB-CCCC-DDDDEEEEFFFF}" '
                'xmlns:ds="http://schemas.openxmlformats.org/officeDocument/2006/customXml"><ds:schemaRefs>%s'
                '</ds:schemaRefs></ds:datastoreItem>'
                % (i, "".join('<ds:schemaRef ds:uri="%s"/>' % u for u in uris)))

    def item_rels(i):
        return _rels_xml([("rId1", "customXmlProps", "itemProps%d.xml" % i, False)])

    app = _app("Microsoft Excel", "16.0300", K["company"], "")
    defaults = [("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml"),
                ("png", "image/png"), ("bin", "application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings")]
    ov = lambda p, t: (p, "application/vnd.openxmlformats-officedocument.%s+xml" % t)  # noqa: E731
    overrides = [ov("/xl/workbook.xml", "spreadsheetml.sheet.main"), ov("/xl/worksheets/sheet1.xml", "spreadsheetml.worksheet"),
                 ov("/xl/worksheets/sheet2.xml", "spreadsheetml.worksheet"),
                 ov("/xl/externalLinks/externalLink1.xml", "spreadsheetml.externalLink"),
                 ov("/xl/pivotCache/pivotCacheDefinition1.xml", "spreadsheetml.pivotCacheDefinition"),
                 ov("/xl/connections.xml", "spreadsheetml.connections"),
                 ov("/xl/persons/person.xml", "spreadsheetml.person"),
                 ("/xl/threadedComments/threadedComment1.xml", "application/vnd.ms-excel.threadedcomments+xml"),
                 ov("/xl/comments1.xml", "spreadsheetml.comments"),
                 ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
                 ov("/docProps/app.xml", "extended-properties"), ov("/docProps/custom.xml", "custom-properties")]
    overrides += [("/customXml/itemProps%d.xml" % i, "application/vnd.openxmlformats-officedocument.customXmlProperties+xml")
                  for i in (1, 2, 3, 4)]
    pkg_rels = _rels_xml([("rId1", "officeDocument", "xl/workbook.xml", False),
                          ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                           "docProps/core.xml", False),
                          ("rId3", _RT + "extended-properties", "docProps/app.xml", False),
                          ("rId4", _RT + "custom-properties", "docProps/custom.xml", False)])
    wb_rels2 = wb_rels[:-len("</Relationships>")] + "".join(
        '<Relationship Id="rId%d" Type="%scustomXml" Target="../customXml/item%d.xml"/>' % (20 + i, _RT, i)
        for i in (1, 2, 3, 4)) + "</Relationships>"
    from_png = b"\x89PNG\r\n\x1a\n"
    try:
        from tests.fixtures import make_png
        d = tempfile.mkdtemp(prefix="oox_png_")
        try:
            with open(make_png(d)[0], "rb") as fh:
                from_png = fh.read()
        finally:
            shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
    parts = [("[Content_Types].xml", _ct_xml(defaults, overrides)), ("_rels/.rels", pkg_rels),
             ("xl/_rels/workbook.xml.rels", wb_rels2), ("xl/workbook.xml", wb),
             ("xl/worksheets/sheet1.xml", sheet1), ("xl/worksheets/sheet2.xml", sheet2),
             ("xl/worksheets/_rels/sheet1.xml.rels", s1_rels), ("xl/worksheets/_rels/sheet2.xml.rels", s2_rels),
             ("xl/theme/theme1.xml", _D + '<a:theme xmlns:a="%s" name="Office"/>' % _NS["a"]),
             ("xl/styles.xml", _D + '<styleSheet xmlns="%s"/>' % X),
             ("xl/externalLinks/externalLink1.xml", ext), ("xl/externalLinks/_rels/externalLink1.xml.rels", ext_rels),
             ("xl/pivotCache/pivotCacheDefinition1.xml", pivot), ("xl/connections.xml", conns),
             ("xl/persons/person.xml", persons), ("xl/threadedComments/threadedComment1.xml", thr),
             ("xl/comments1.xml", xlc), ("xl/media/image1.png", from_png),
             ("xl/drawings/drawing1.xml", _D + '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"/>'),
             ("xl/printerSettings/printerSettings1.bin", _devmode(K["printer"])),
             ("xl/printerSettings/printerSettings2.bin", _devmode("Canon iR-ADV C5535 buh-02")),
             ("customXml/item1.xml", sp_props), ("customXml/itemProps1.xml", props(1, [
                 "http://schemas.microsoft.com/office/2006/metadata/properties", "http://www.w3.org/2001/XMLSchema"])),
             ("customXml/_rels/item1.xml.rels", item_rels(1)),
             ("customXml/item2.xml", sp_ct), ("customXml/itemProps2.xml", props(2, [
                 "http://schemas.microsoft.com/office/2006/metadata/contentType", "http://www.w3.org/2001/XMLSchema"])),
             ("customXml/_rels/item2.xml.rels", item_rels(2)),
             ("customXml/item3.xml", sp_forms), ("customXml/itemProps3.xml", props(3, [
                 "http://schemas.microsoft.com/sharepoint/v3/contenttype/forms"])),
             ("customXml/_rels/item3.xml.rels", item_rels(3)),
             ("customXml/item4.xml", mashup), ("customXml/itemProps4.xml", props(4, [
                 "http://schemas.microsoft.com/DataMashup"])),
             ("customXml/_rels/item4.xml.rels", item_rels(4)),
             ("docProps/core.xml", _core(K["creator"], K["lmb"])), ("docProps/app.xml", app),
             ("docProps/custom.xml", _custom([("ContentTypeId", K["ctid"]), ("_AuthorEmail", K["msip"])]))]
    return parts


def _fx_pptx(dt=EPOCH):
    P, A, R = _NS["p"], _NS["a"], _NS["r"]
    slide = (_D + '<p:sld xmlns:p="%s" xmlns:a="%s" xmlns:r="%s" xmlns:mc="%s"><p:cSld><p:spTree>'
             '<p:sp><p:txBody><a:p><a:r><a:rPr><a:hlinkClick r:id="rId2"/></a:rPr><a:t>написать</a:t></a:r></a:p></p:txBody></p:sp>'
             '<mc:AlternateContent><mc:Choice Requires="v"><p:graphicFrame><a:graphic><a:graphicData '
             'uri="http://schemas.openxmlformats.org/presentationml/2006/ole"><p:oleObj name="Object 2" r:id="rId3" '
             'progId="Excel.Sheet.12"><p:embed/></p:oleObj></a:graphicData></a:graphic></p:graphicFrame></mc:Choice>'
             '<mc:Fallback><p:graphicFrame><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/presentationml/2006/ole">'
             '<p:oleObj name="Object 2" r:id="rId3" progId="Excel.Sheet.12"><p:embed/></p:oleObj></a:graphicData></a:graphic>'
             '</p:graphicFrame></mc:Fallback></mc:AlternateContent></p:spTree></p:cSld></p:sld>'
             % (P, A, R, _NS["mc"]))
    authors = (_D + '<p:cmAuthorLst xmlns:p="%s"><p:cmAuthor id="0" name="%s" initials="КА" lastIdx="1" clrIdx="0"/></p:cmAuthorLst>'
               % (P, K["rev"]))
    comment = (_D + '<p:cmLst xmlns:p="%s"><p:cm authorId="0" dt="2024-05-02T09:00:00.000" idx="1"><p:pos x="10" y="10"/>'
               '<p:text>вопрос</p:text></p:cm></p:cmLst>' % P)
    pres = (_D + '<p:presentation xmlns:p="%s" xmlns:r="%s"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
            '<p:modifyVerifier cryptProviderType="rsaAES" cryptAlgorithmClass="hash" hashValue="%s" saltValue="%s" '
            'spinCount="100000"/></p:presentation>' % (P, R, K["hash"], K["salt"]))
    pres_rels = _rels_xml([("rId1", "slide", "slides/slide1.xml", False),
                           ("rId2", "commentAuthors", "commentAuthors.xml", False),
                           ("rId3", "printerSettings", "printerSettings/printerSettings1.bin", False)])
    slide_rels = _rels_xml([("rId1", "comments", "../comments/comment1.xml", False),
                            ("rId2", "hyperlink", K["mailto"], True),
                            ("rId3", "oleObject", "../embeddings/oleObject1.bin", False)])
    defaults = [("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml"),
                ("jpeg", "image/jpeg"), ("bin", "application/vnd.openxmlformats-officedocument.oleObject")]
    overrides = [("/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"),
                 ("/ppt/slides/slide1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"),
                 ("/ppt/commentAuthors.xml", "application/vnd.openxmlformats-officedocument.presentationml.commentAuthors+xml"),
                 ("/ppt/comments/comment1.xml", "application/vnd.openxmlformats-officedocument.presentationml.comments+xml"),
                 ("/ppt/printerSettings/printerSettings1.bin", "application/vnd.openxmlformats-officedocument.presentationml.printerSettings"),
                 ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
                 ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml")]
    pkg_rels = _rels_xml([("rId1", "officeDocument", "ppt/presentation.xml", False),
                          ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                           "docProps/core.xml", False),
                          ("rId3", _RT + "extended-properties", "docProps/app.xml", False),
                          ("rId4", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail",
                           "docProps/thumbnail.jpeg", False)])
    return [("[Content_Types].xml", _ct_xml(defaults, overrides)), ("_rels/.rels", pkg_rels),
            ("ppt/_rels/presentation.xml.rels", pres_rels), ("ppt/presentation.xml", pres),
            ("ppt/slides/slide1.xml", slide), ("ppt/slides/_rels/slide1.xml.rels", slide_rels),
            ("ppt/commentAuthors.xml", authors), ("ppt/comments/comment1.xml", comment),
            ("ppt/printerSettings/printerSettings1.bin", _devmode(K["printer"])),
            ("ppt/embeddings/oleObject1.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504),
            ("docProps/thumbnail.jpeg", _tiny_jpeg()),
            ("docProps/core.xml", _core(K["creator"], K["lmb"])),
            ("docProps/app.xml", _app("Microsoft Office PowerPoint", "16.0000", K["company"], K["manager"]))]


def _selftest(verbose=False):
    """Synthetic data: the XML edit engine, three formats in two profiles, the oracle, structure,
    invisibility, idempotency, pathologies, fuzzing. Prints only failures and the total."""
    import io
    import random

    from .clean import survived_values
    from .inspect import inspect_file

    stat = [0, 0]

    def chk(name, cond, detail=""):
        stat[0] += 1
        if not cond:
            stat[1] += 1
            print("FAIL: %s %s" % (name, str(detail)[:400]))
        elif verbose:
            print("  ok: %s" % name)

    td = tempfile.mkdtemp(prefix="oox_self_")
    P = lambda n: os.path.join(td, n)  # noqa: E731
    ST, PA = Profile.STEALTH, Profile.PARANOID

    def read(path, name):
        with zipfile.ZipFile(path) as z:
            return z.read(name)

    def txt(path, name):
        return read(path, name).decode("utf-8")

    def names(path):
        with zipfile.ZipFile(path) as z:
            return [i.filename for i in z.infolist()]

    def build(name, parts, dt=EPOCH, **kw):
        return _zip_write(P(name), parts, dt, **kw)

    def leftovers():
        return [f for f in os.listdir(td) if f.endswith((".ooxml", ".part")) or f.startswith(".ooxml_")]

    def run(src, prof, tag, **kw):
        dst = P("out_%s%s" % (tag, os.path.splitext(src)[1]))
        h0 = _sha(src)
        try:
            res = clean_ooxml(src, dst, prof, **kw)
        except Exception as exc:  # noqa: BLE001
            chk("%s: doesn't raise" % tag, False, repr(exc))
            return None, dst
        chk("%s: input unchanged" % tag, _sha(src) == h0)
        chk("%s: no temporary files left" % tag, not leftovers(), leftovers())
        return res, dst

    def oracle(src, dst):
        b, a = inspect_file(src), inspect_file(dst)
        chk("%s: the inspector got no new parse errors (otherwise the dispatcher says "
            "\"cleanliness not proven\")"
            % os.path.basename(dst), len(a.errors) <= len(b.errors), a.errors)
        return b, a, survived_values(b, a)

    def new_signals(b, a):
        return {s.kind for s in a.signals} - {s.kind for s in b.signals}

    def bad(name, src, prof=ST, **kw):
        """Pathology: ok=False, errors present, input intact, dst not created, no temporary files."""
        dst = P("never.bin")
        if os.path.exists(dst):
            os.remove(dst)
        h0 = _sha(src) if os.path.isfile(src) else None
        try:
            res = clean_ooxml(src, dst, prof, **kw)
        except Exception as exc:  # noqa: BLE001
            chk("%s: doesn't raise" % name, False, repr(exc))
            return None
        chk("%s: ok=False and errors present" % name, (not res.ok) and bool(res.errors), (res.ok, res.errors))
        chk("%s: input intact" % name, h0 is None or _sha(src) == h0)
        chk("%s: dst not created" % name, not os.path.exists(dst))
        chk("%s: no temporary files" % name, not leftovers(), leftovers())
        chk("%s: no paths in errors" % name, td not in " ".join(res.errors))
        return res

    try:
        # ------------------------------------------------------------------ XML edit engine
        raw = ('\ufeff<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n<r:root xmlns:r="urn:r" xmlns:w="urn:w" '
               'w:z="1" a=\'q"x\'><w:ins w:id="1" w:author="Иван &amp; Ко" w:date="2020-01-01T00:00:00Z" k="&gt;">'
               '<w:r><w:t>text</w:t></w:r></w:ins><Company>ООО Ромашка</Company><Empty/><Pair></Pair>'
               '<x w:author = "A" /><w:hyperlink r:id="rId5"><w:r>link</w:r></w:hyperlink><!-- c <z> -->'
               '<![CDATA[ <z> ]]></r:root>').encode("utf-8")
        E = _Patch(raw, text=True)
        ins = E.by("ins")[0]
        chk("engine: attributes in document order, decoded",
            [a[1] for a in ins.attrs] == ["id", "author", "date", "k"] and ins.get("author") == "Иван & Ко" and ins.get("k") == ">")
        chk("engine: empty tags detected", E.by("Empty")[0].empty and E.by("x")[0].empty and not E.by("Pair")[0].empty)
        chk("engine: without edits the output equals the input byte for byte", _Patch(raw).finish() == raw)
        E.set_attr(ins, "author", "Author")
        E.del_attr(ins, "date")
        E.set_text(E.by("Company")[0], "")
        E.set_text(E.by("Empty")[0], "a<b")
        E.set_attr(E.by("x")[0], "author", "Au'th")
        E.unwrap(E.by("hyperlink")[0])
        out = E.finish().decode("utf-8")
        chk("engine: edits are pinpoint, the rest is source bytes",
            out == raw.decode("utf-8").replace('w:author="Иван &amp; Ко" w:date="2020-01-01T00:00:00Z"', 'w:author="Author"')
            .replace("<Company>ООО Ромашка</Company>", "<Company></Company>").replace("<Empty/>", "<Empty>a&lt;b</Empty>")
            .replace('w:author = "A"', 'w:author = "Au\'th"')
            .replace('<w:hyperlink r:id="rId5"><w:r>link</w:r></w:hyperlink>', "<w:r>link</w:r>"), out)
        E = _Patch(raw)
        E.remove(E.by("ins")[0])
        E.set_attr(E.by("ins")[0], "author", "Z")
        chk("engine: an edit inside a removed element is ignored", b"w:ins" not in E.finish())
        E = _Patch(raw)
        E.set_attr(E.by("x")[0], "author", "B")
        E.set_attr(E.by("x")[0], "author", "C")
        try:
            E.finish()
            chk("engine: two edits of the same spot -- one wins, the XML is intact", True)
        except _Bad:
            chk("engine: two edits of the same spot -- refusal, not corruption", True)
        for tag, bad_raw in (("nesting too deep", b"<a>" * 4000 + b"</a>" * 4000),
                             ("DOCTYPE", b'<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>'),
                             ("UTF-16", "<?xml version='1.0' encoding='utf-16'?><a/>".encode("utf-16")),
                             ("not XML", b"<a><b></a>"), ("empty", b"")):
            try:
                _parse(bad_raw)
                chk("engine: %s rejected" % tag, False)
            except _Bad:
                chk("engine: %s rejected" % tag, True)
        # foreign prefixes, single quotes, spaces around '=': editing doesn't depend on the document's style
        alt = ("<?xml version='1.0' encoding='UTF-8'?>\n<q:document xmlns:q='%s' xmlns:r='%s'><q:body>"
               "<q:ins q:id = '1'  q:author = 'Иванов' q:date='2024-01-01T00:00:00Z'><q:r><q:t>x</q:t></q:r></q:ins>"
               "</q:body></q:document>" % (_NS["w"], _NS["r"])).encode("utf-8")
        E = _Patch(alt)
        n = E.by("ins")[0]
        E.set_attr(n, "author", AUTHOR)
        E.del_attr(n, "date")
        chk("engine: foreign prefix and single quotes", E.finish().decode("utf-8").count("Author") == 1
            and "q:date" not in E.finish().decode("utf-8"))
        # engine fuzzing: corrupting valid XML never produces anything but _Bad
        rnd = random.Random(11)
        for i in range(300):
            b = bytearray(raw)
            for _ in range(rnd.randint(1, 4)):
                at = rnd.randrange(len(b))
                if rnd.random() < .5:
                    b[at] = rnd.getrandbits(8)
                else:
                    del b[at:at + rnd.randint(1, 8)]
            try:
                Pf = _Patch(bytes(b), text=True)
                for n in Pf.nodes[:3]:
                    Pf.set_attr(n, "author", "x") if n.attrs else Pf.remove(n)
                Pf.finish()
            except _Bad:
                pass
            except Exception as exc:  # noqa: BLE001
                chk("engine: fuzz %d raises only _Bad" % i, False, repr(exc))
                break
        else:
            chk("engine: 300 corrupted XMLs -- only _Bad", True)

        # ------------------------------------------------------------------ docx (Word-like)
        src = build("w.docx", _fx_docx())
        b0 = inspect_file(src)
        chk("the docx fixture really leaks", b0.critical >= 15, b0.critical)
        sig0 = {s.kind for s in b0.signals}
        res, dst = run(src, ST, "wS")
        chk("docx STEALTH: ok", res.ok, res.errors)
        b, a, sv = oracle(src, dst)
        chk("docx STEALTH: oracle -- no original value survived", not sv,
            [(s.label, s.found_at) for s in sv][:6])
        v = verify_ooxml(src, dst)
        chk("docx STEALTH: verify_ooxml", v["ok"], v["errors"])
        chk("docx STEALTH: no new ai signal", "ai" not in new_signals(b, a), new_signals(b, a))
        chk("docx STEALTH: scrubbed signal -- a known consequence of the profile (the inspector "
            "counts empty creator+lastModifiedBy as cleaning)", "scrubbed" in new_signals(b, a))
        with zipfile.ZipFile(dst) as z:
            blob = b"".join(z.read(n) for n in z.namelist() if not n.endswith((".jpeg", ".png")))
        for k in ("creator", "lmb", "company", "manager", "msip", "site", "ctid", "tpl", "mm", "conn", "hash", "salt",
                  "docvar", "rev", "ed", "pres", "hdr", "printer", "mailto", "filelink"):
            chk("docx STEALTH: \"%s\" is not in the output bytes" % k,
                K[k].encode("utf-8") not in blob and K[k].encode("utf-16le") not in blob)
        nm = names(dst)
        for gone in ("word/people.xml", "docProps/thumbnail.jpeg", "word/printerSettings/printerSettings1.bin",
                     "docMetadata/LabelInfo.xml", "word/_rels/settings.xml.rels"):
            chk("docx STEALTH: %s removed" % gone, gone not in nm)
        for kept in ("word/theme/theme1.xml", "word/fontTable.xml", "word/webSettings.xml", "word/styles.xml",
                     "word/settings.xml", "docProps/core.xml", "docProps/app.xml", "docProps/custom.xml"):
            chk("docx STEALTH: %s in place" % kept, kept in nm)
        sn = names(src)
        chk("docx STEALTH: entry order preserved",
            nm == [n for n in sn if n in set(nm)] and nm[0] == "[Content_Types].xml")
        with zipfile.ZipFile(dst) as z:
            chk("docx STEALTH: 1980-01-01 timestamps preserved", all(tuple(i.date_time) == EPOCH for i in z.infolist()))
            chk("docx STEALTH: testzip", z.testzip() is None)
        app, core = txt(dst, "docProps/app.xml"), txt(dst, "docProps/core.xml")
        chk("docx STEALTH: Application and AppVersion as they were",
            "<Application>Microsoft Office Word</Application>" in app and "<AppVersion>16.0000</AppVersion>" in app)
        chk("docx STEALTH: Company and Manager blanked, elements in place",
            "<Company></Company>" in app and "<Manager></Manager>" in app)
        chk("docx STEALTH: TotalTime, Words, Template untouched",
            "<TotalTime>187</TotalTime>" in app and "<Words>1250</Words>" in app and "<Template>Normal.dotm</Template>" in app)
        chk("docx STEALTH: creator and lastModifiedBy are empty elements, not removed",
            "<dc:creator></dc:creator>" in core and "<cp:lastModifiedBy></cp:lastModifiedBy>" in core)
        chk("docx STEALTH: created and modified untouched (neither substituted nor blanked)",
            "2024-03-01T10:00:00Z" in core and "2024-03-05T14:30:00Z" in core)
        chk("docx STEALTH: revision and keywords kept", "<cp:revision>7</cp:revision>" in core and "отчёт; квартал" in core)
        chk("docx STEALTH: the title with the author's full name blanked", "<dc:title></dc:title>" in core)
        doc, sett = txt(dst, "word/document.xml"), txt(dst, "word/settings.xml")
        chk("docx STEALTH: revision authors -> Author, revisions stayed",
            doc.count('w:author="Author"') == 2 and "вставленный текст" in doc and "удалённый текст" in doc)
        chk("docx STEALTH: revision dates removed (w:date and w16du:dateUtc)", "w:date=" not in doc and "dateUtc" not in doc)
        chk("docx STEALTH: rsids stayed", "w:rsidR=" in doc and "w:rsidSect=" in doc and "<w:rsid " in sett)
        chk("docx STEALTH: _GoBack stayed", "_GoBack" in doc)
        chk("docx STEALTH: permStart without the editor name, the group stayed",
            '<w:permStart w:id="3" w:edGrp="editors"/>' in doc)
        chk("docx STEALTH: mailto link removed, its text stayed; http link intact",
            'r:id="rId20"' not in doc and "написать в отдел кадров" in doc and 'r:id="rId25"' in doc)
        chk("docx STEALTH: template, mail merge, password hash removed",
            "attachedTemplate" not in sett and "mailMerge" not in sett and "w:hash=" not in sett and "w:salt=" not in sett)
        chk("docx STEALTH: protection stayed on", 'w:enforcement="1"' in sett and 'w:edit="readOnly"' in sett)
        chk("docx STEALTH: docVar with a path removed, harmless one stayed",
            "База" not in sett and 'w:name="Стадия"' in sett)
        chk("docx STEALTH: proofState and docId stayed", "proofState" in sett and "docId" in sett)
        com = txt(dst, "word/comments.xml")
        chk("docx STEALTH: comment: author Author, no initials and no date",
            'w:author="Author"' in com and "w:initials" not in com and "w:date" not in com and "комментарий" in com)
        chk("docx STEALTH: commentsExtensible without dateUtc", "dateUtc" not in txt(dst, "word/commentsExtensible.xml"))
        chk("docx STEALTH: header: revision author and date", 'w:author="Author"' in txt(dst, "word/header1.xml")
            and "w:date" not in txt(dst, "word/header1.xml") and "w:rsidR" in txt(dst, "word/header1.xml"))
        rl = txt(dst, "word/_rels/document.xml.rels")
        chk("docx STEALTH: .rels: people, printer, mailto removed",
            "people" not in rl and "printerSettings" not in rl and "mailto" not in rl)
        chk("docx STEALTH: .rels: file path -> file name, http untouched",
            'Target="spec.pdf"' in rl and "https://example.org/" in rl and 'TargetMode="External"' in rl)
        ct = txt(dst, "[Content_Types].xml")
        chk("docx STEALTH: Content_Types: Overrides of removed parts gone, Default jpeg stayed",
            "people" not in ct and "printerSettings" not in ct and "LabelInfo" not in ct and 'Extension="jpeg"' in ct)
        cu = txt(dst, "docProps/custom.xml")
        chk("docx STEALTH: custom.xml: harmless property stayed, pids consecutive from 2",
            "ProjectCode" in cu and 'pid="2"' in cu and "MSIP_Label" not in cu and "ContentTypeId" not in cu)
        chk("docx STEALTH: image rebuilt without EXIF", read(dst, "word/media/image1.jpeg") != read(src, "word/media/image1.jpeg")
            and b"Exif" not in read(dst, "word/media/image1.jpeg"))
        # byte accuracy: edited parts keep the declaration, CRLF and root; the others are raw bytes
        sd, dd = read(src, "word/document.xml"), read(dst, "word/document.xml")
        cut = sd.index(b"<w:body>")
        chk("docx STEALTH: document.xml: declaration, CRLF and root tag are source bytes",
            dd[:cut] == sd[:cut] and dd.endswith(b"</w:body></w:document>"))
        chk("docx STEALTH: core.xml: declaration and CRLF kept", read(dst, "docProps/core.xml").startswith(_D.encode()))
        es = {e.name: e for e in zipfix.entries(src)}
        ed = {e.name: e for e in zipfix.entries(dst)}
        same = [n for n in ed if n not in v["edited"]]
        chk("docx STEALTH: untouched entries have the same compressed bytes (size and CRC)",
            all(es[n].compress_size == ed[n].compress_size and es[n].crc == ed[n].crc for n in same) and len(same) >= 4,
            len(same))
        chk("docx STEALTH: the action list makes sense",
            res.count(Act.KEPT) >= 8 and res.count(Act.REMOVED) >= 8 and res.count(Act.FAILED) == 0 and res.changed >= 15,
            {a.value: res.count(a) for a in Act})
        # idempotency: re-cleaning our own result changes nothing
        res2, dst2 = run(dst, ST, "wS2")
        chk("docx STEALTH: re-cleaning the result -- ok", res2.ok, res2.errors)
        chk("docx STEALTH: re-cleaning doesn't change a single byte", res2.ok and _sha(dst2) == _sha(dst))

        res, dst = run(src, PA, "wP")
        chk("docx PARANOID: ok", res.ok, res.errors)
        b, a, sv = oracle(src, dst)
        chk("docx PARANOID: oracle -- nothing survived", not sv, [(s.label, s.found_at) for s in sv][:6])
        v = verify_ooxml(src, dst)
        chk("docx PARANOID: verify_ooxml", v["ok"], v["errors"])
        app, core = txt(dst, "docProps/app.xml"), txt(dst, "docProps/core.xml")
        chk("docx PARANOID: Application, AppVersion, TotalTime, Template, statistics removed",
            not any(t in app for t in ("Application", "AppVersion", "TotalTime", "Template", "<Pages>", "<Words>", "Company", "Manager")))
        chk("docx PARANOID: app.xml flags stayed (otherwise an empty app.xml is a parse error)", "DocSecurity" in app)
        chk("docx PARANOID: dates, revision, title, keywords removed; creator empty",
            not any(t in core for t in ("created", "modified", "revision", "<dc:title", "keywords")) and "<dc:creator></dc:creator>" in core)
        allx = b"".join(read(dst, n) for n in ("word/document.xml", "word/settings.xml", "word/styles.xml", "word/header1.xml"))
        chk("docx PARANOID: rsid and _GoBack removed", b"rsid" not in allx and b"_GoBack" not in allx)
        chk("docx PARANOID: custom.xml removed entirely", "docProps/custom.xml" not in names(dst))
        chk("docx PARANOID: revisions and text in place", "вставленный текст" in txt(dst, "word/document.xml"))
        chk("docx PARANOID: honestly visible as processed (ai signal: no rsid at all)", "ai" in new_signals(b, a))

        # the language (STRUCTURAL) survives in every profile
        lang = _core(K["creator"], K["lmb"], extra="<dc:language>ru-RU</dc:language>")
        p2 = [(n, lang if n == "docProps/core.xml" else bd) for n, bd in _fx_docx()]
        for prof, tag in ((ST, "S"), (PA, "P")):
            r_, d_ = run(build("lang.docx", p2), prof, "lang" + tag)
            chk("dc:language kept (%s)" % prof.value, r_.ok and "ru-RU" in txt(d_, "docProps/core.xml"), r_.errors)

        # app.xml and core.xml left with no properties after PARANOID are removed entirely
        # (otherwise the inspector gets a "parse error" and the dispatcher can't prove cleanliness)
        thin = [(n, (_D + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                     '<Application>Microsoft Office Word</Application><AppVersion>16.0000</AppVersion></Properties>'
                     if n == "docProps/app.xml" else (
                         _D + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                         'xmlns:dcterms="http://purl.org/dc/terms/"><dcterms:created>2024-03-01T10:00:00Z</dcterms:created>'
                         '</cp:coreProperties>' if n == "docProps/core.xml" else bd))) for n, bd in _fx_docx()]
        r_, d_ = run(build("thin.docx", thin), PA, "thin")
        b, a, sv = oracle(P("thin.docx"), d_)
        chk("PARANOID: app.xml and core.xml with nothing left are removed entirely, inspector "
            "without parse errors",
            r_.ok and "docProps/app.xml" not in names(d_) and "docProps/core.xml" not in names(d_) and not a.errors
            and verify_ooxml(P("thin.docx"), d_)["ok"], (r_.errors, a.errors))
        r_, d_ = run(P("thin.docx"), ST, "thinS")
        chk("STEALTH: the same parts stay in place", r_.ok and "docProps/app.xml" in names(d_), r_.errors)

        # the user's whitelist
        r_, d_ = run(src, ST, "keep", keep={"dc:title", "Company", "thumbnail"})
        chk("keep: fields and thumbnail kept, everything else cleaned",
            r_.ok and "ООО Ромашка-Тест" in txt(d_, "docProps/app.xml") and "docProps/thumbnail.jpeg" in names(d_)
            and K["creator"].encode() not in read(d_, "docProps/core.xml").replace(("Отчёт " + K["creator"]).encode("utf-8"), b"")
            and any(a_.act is Act.KEPT and "whitelist" in a_.note for a_ in r_.actions), r_.errors)

        # ------------------------------------------------------------------ docx with embedded items
        src_o = build("o.docx", _fx_docx(ole=True))
        res, dst = run(src_o, ST, "oS")
        chk("docx+OLE STEALTH: ok", res.ok, res.errors)
        b, a, sv = oracle(src_o, dst)
        emb = zipfile.ZipFile(io.BytesIO(read(dst, "word/embeddings/Microsoft_Excel_Worksheet.xlsx")))
        chk("docx+OLE STEALTH: the embedded workbook cleaned recursively (its own author removed)",
            K["emb"].encode() not in emb.read("docProps/core.xml") and b"<dc:creator></dc:creator>" in emb.read("docProps/core.xml")
            and emb.testzip() is None)
        chk("docx+OLE STEALTH: OLE stayed and is honestly marked UNREMOVABLE",
            "word/embeddings/oleObject1.bin" in names(dst) and any(
                a_.act is Act.UNREMOVABLE and "oleObject1.bin" in a_.location for a_ in res.actions))
        chk("docx+OLE STEALTH: survivors only inside OLE (no lying, and the inspector sees it)",
            all("oleObject1.bin" in s.found_at for s in sv), [(s.label, s.found_at) for s in sv][:5])
        res, dst = run(src_o, PA, "oP")
        chk("docx+OLE PARANOID: ok", res.ok, res.errors)
        b, a, sv = oracle(src_o, dst)
        chk("docx+OLE PARANOID: oracle -- nothing survived", not sv, [(s.label, s.found_at) for s in sv][:5])
        v = verify_ooxml(src_o, dst)
        chk("docx+OLE PARANOID: verify_ooxml (no broken references to the removed object)", v["ok"], v["errors"])
        d_ = txt(dst, "word/document.xml")
        chk("docx+OLE PARANOID: OLE removed with the object in the text, the reference and "
            "Default bin",
            "word/embeddings/oleObject1.bin" not in names(dst) and 'r:id="rId22"' not in d_ and "rId22" not in txt(dst, "word/_rels/document.xml.rels")
            and 'Extension="bin"' not in txt(dst, "[Content_Types].xml") and "Microsoft_Excel_Worksheet.xlsx" in " ".join(names(dst)))
        # nesting that's too deep doesn't loop the cleaning
        deep = _fx_embedded_xlsx()
        for _ in range(MAX_DEPTH + 2):
            tmp_in = P("deep.bin")
            _zip_write(tmp_in, [("[Content_Types].xml", _ct_xml([("rels", "x"), ("xml", "y")], [])), ("word/document.xml", "<a/>"),
                                ("word/embeddings/e.docx", deep)])
            with open(tmp_in, "rb") as fh:
                deep = fh.read()
        open(P("deep.docx"), "wb").write(deep)
        r_, d_ = run(P("deep.docx"), ST, "deep")
        chk("nesting deeper than the limit: no loop, honestly UNREMOVABLE",
            r_.ok and any(a_.act is Act.UNREMOVABLE for a_ in r_.actions), r_.errors)

        # ------------------------------------------------------------------ xlsx
        src_x = build("x.xlsx", _fx_xlsx())
        b0 = inspect_file(src_x)
        chk("the xlsx fixture really leaks", b0.critical >= 12, b0.critical)
        res, dst = run(src_x, ST, "xS")
        chk("xlsx STEALTH: ok", res.ok, res.errors)
        b, a, sv = oracle(src_x, dst)
        chk("xlsx STEALTH: oracle -- only what was kept on purpose survived (DataMashup, the "
            "Power Query connection, a table's structured reference: the inspector takes them "
            "for leaks)", all(_expected_survivor(s) for s in sv),
            [(s.label, s.found_at) for s in sv if not _expected_survivor(s)][:5])
        chk("xlsx STEALTH: the legacy comment author is reported by the inspector and gone from the output",
            any(f.location == "xl/comments1.xml" and f.value == K["rev"] and f.risk.value == "identity"
                for f in b.findings) and not any(K["rev"] in f.value for f in a.findings if f.location == "xl/comments1.xml"))
        chk("xlsx STEALTH: DataMashup marked UNREMOVABLE",
            any(a_.act is Act.UNREMOVABLE and "item4.xml" in a_.location for a_ in res.actions))
        v = verify_ooxml(src_x, dst)
        chk("xlsx STEALTH: verify_ooxml", v["ok"], v["errors"])
        nm = names(dst)
        chk("xlsx STEALTH: printers removed, the .rels of a sheet without a printer removed, "
            "sheet1's stayed",
            not any("printerSettings" in n for n in nm) and "xl/worksheets/_rels/sheet2.xml.rels" not in nm
            and "xl/worksheets/_rels/sheet1.xml.rels" in nm)
        s1 = txt(dst, "xl/worksheets/sheet1.xml")
        chk("xlsx STEALTH: pageSetup without r:id, the rest intact",
            '<pageSetup orientation="portrait"/>' in s1 and 'r:id="rId4"' in s1 and "x14ac:dyDescent" in s1)
        chk("xlsx STEALTH: mailto removed, http link stayed",
            "rId1\"" not in s1.split("<hyperlinks>")[1].split("</hyperlinks>")[0] and 'r:id="rId2"' in s1
            and s1.count("<hyperlink ") == 1)
        wbx = txt(dst, "xl/workbook.xml")
        chk("xlsx STEALTH: absPath and its wrapper removed", "absPath" not in wbx and "AlternateContent" not in wbx)
        chk("xlsx STEALTH: fileVersion and the hidden sheet untouched", "rupBuild=\"10531\"" in wbx and 'state="hidden"' in wbx)
        chk("xlsx STEALTH: workbook password hash removed, structure protection stayed",
            "workbookHashValue" not in wbx and 'lockStructure="1"' in wbx)
        chk("xlsx STEALTH: fileSharing without the name and password", "userName" not in wbx and "reservationPassword" not in wbx
            and "<fileSharing" in wbx)
        s2 = txt(dst, "xl/worksheets/sheet2.xml")
        chk("xlsx STEALTH: sheetProtection without the hash, sheet protection stayed",
            "hashValue" not in s2 and "saltValue" not in s2 and 'password=' not in s2 and 'sheet="1"' in s2
            and 'objects="1"' in s2)
        chk("xlsx STEALTH: external path in a defined name -> workbook name, structured "
            "reference intact",
            "[budget.xlsx]Sheet1" in wbx and "C:\\Users" not in wbx and "Table1[Сумма]" in wbx)
        chk("xlsx STEALTH: externalLink: path -> file name",
            'Target="budget.xlsx"' in txt(dst, "xl/externalLinks/_rels/externalLink1.xml.rels"))
        pv = txt(dst, "xl/pivotCache/pivotCacheDefinition1.xml")
        chk("xlsx STEALTH: refreshedBy empty, refreshedDate removed", 'refreshedBy=""' in pv and "refreshedDate" not in pv)
        cn = txt(dst, "xl/connections.xml")
        chk("xlsx STEALTH: connection: server, database, login, query, description removed; "
            "driver stayed",
            "sql01" not in cn and "UID" not in cn and "dbo.Orders" not in cn and "подключение к ERP" not in cn
            and "DRIVER=SQL Server" in cn)
        chk("xlsx STEALTH: the Power Query connection untouched", "Microsoft.Mashup.OleDb.1" in cn and "$Workbook$" in cn)
        chk("xlsx STEALTH: person: names and emails neutral",
            K["rev"] not in txt(dst, "xl/persons/person.xml") and K["pres"] not in txt(dst, "xl/persons/person.xml")
            and 'providerId="None"' in txt(dst, "xl/persons/person.xml"))
        chk("xlsx STEALTH: threadedComment: dT (required) kept", "dT=" in txt(dst, "xl/threadedComments/threadedComment1.xml"))
        chk("xlsx STEALTH: legacy comments: author Author, text in place",
            "<author>Author</author>" in txt(dst, "xl/comments1.xml") and "заметка" in txt(dst, "xl/comments1.xml"))
        chk("xlsx STEALTH: SharePoint customXml blanked to the root, parts in place",
            "Reviewer" not in txt(dst, "customXml/item1.xml") and "ma:contentTypeID" not in txt(dst, "customXml/item2.xml")
            and "Display" not in txt(dst, "customXml/item3.xml") and "customXml/item1.xml" in nm)
        chk("xlsx STEALTH: schemas in itemProps removed, itemID stayed",
            "schemaRef " not in txt(dst, "customXml/itemProps1.xml") and "itemID" in txt(dst, "customXml/itemProps1.xml"))
        chk("xlsx STEALTH: DataMashup untouched", read(dst, "customXml/item4.xml") == read(src_x, "customXml/item4.xml"))
        cu = txt(dst, "docProps/custom.xml") if "docProps/custom.xml" in nm else ""
        chk("xlsx STEALTH: custom.xml: no properties at all -- the part removed entirely", not cu and "custom" not in txt(dst, "_rels/.rels"))
        chk("xlsx STEALTH: PNG: Default png and bin: bin removed (no printers left), png stayed",
            'Extension="bin"' not in txt(dst, "[Content_Types].xml") and 'Extension="png"' in txt(dst, "[Content_Types].xml"))
        chk("xlsx STEALTH: Application kept", "<Application>Microsoft Excel</Application>" in txt(dst, "docProps/app.xml"))
        res, dst = run(src_x, PA, "xP")
        chk("xlsx PARANOID: ok", res.ok, res.errors)
        b, a, sv = oracle(src_x, dst)
        chk("xlsx PARANOID: oracle -- only what was kept on purpose survived",
            all(_expected_survivor(s) for s in sv), [(s.label, s.found_at) for s in sv if not _expected_survivor(s)][:5])
        chk("xlsx PARANOID: fileVersion removed, the hidden sheet not",
            "fileVersion" not in txt(dst, "xl/workbook.xml") and 'state="hidden"' in txt(dst, "xl/workbook.xml"))
        v = verify_ooxml(src_x, dst)
        chk("xlsx PARANOID: verify_ooxml", v["ok"], v["errors"])

        # huge sheets (beyond the edit limit) don't break cleaning: what can't be removed is honestly UNREMOVABLE
        mod = sys.modules[__name__]
        saved_max = mod.MAX_XML
        pad = [(n, (bd.replace("<sheetData", "<!--" + "x" * 8000 + "--><sheetData", 1)
                    if n in ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml") else bd)) for n, bd in _fx_xlsx()]
        mod.MAX_XML = 6000
        try:
            r_, d_ = run(build("big.xlsx", pad), ST, "bigsheet")
        finally:
            mod.MAX_XML = saved_max
        chk("sheet beyond the edit limit: file handed over, everything else cleaned", r_.ok and "<dc:creator></dc:creator>" in txt(d_, "docProps/core.xml"),
            r_.errors)
        chk("sheet beyond the limit: printers, mailto and the sheet password hash stayed and are "
            "honestly named UNREMOVABLE",
            r_.ok and "xl/printerSettings/printerSettings1.bin" in names(d_) and "xl/printerSettings/printerSettings2.bin" in names(d_)
            and "mailto" in txt(d_, "xl/worksheets/_rels/sheet1.xml.rels")
            and sum(1 for a_ in r_.actions if a_.act is Act.UNREMOVABLE and "printerSettings" in a_.location) == 2
            and any(a_.act is Act.UNREMOVABLE and "sheetProtection" in a_.label for a_ in r_.actions)
            and any(a_.act is Act.UNREMOVABLE and "mailto" in a_.label for a_ in r_.actions)
            and verify_ooxml(P("big.xlsx"), d_)["ok"], [(a_.act.value, a_.label) for a_ in r_.actions if a_.act is Act.UNREMOVABLE])

        # ------------------------------------------------------------------ pptx
        src_p = build("p.pptx", _fx_pptx())
        res, dst = run(src_p, ST, "pS")
        chk("pptx STEALTH: ok", res.ok, res.errors)
        b, a, sv = oracle(src_p, dst)
        chk("pptx STEALTH: only what's inside OLE survived (UNREMOVABLE)", all("oleObject1.bin" in s.found_at for s in sv),
            [(s.label, s.found_at) for s in sv][:5])
        sl = txt(dst, "ppt/slides/slide1.xml")
        chk("pptx STEALTH: hlinkClick with mailto removed", "hlinkClick" not in sl and "написать" in sl)
        chk("pptx STEALTH: write password (modifyVerifier) removed entirely, the rest intact",
            "modifyVerifier" not in txt(dst, "ppt/presentation.xml") and "sldIdLst" in txt(dst, "ppt/presentation.xml"))
        chk("pptx STEALTH: commentAuthors: name Author, initials empty",
            'name="Author"' in txt(dst, "ppt/commentAuthors.xml") and 'initials=""' in txt(dst, "ppt/commentAuthors.xml"))
        chk("pptx STEALTH: comment: dt removed, text in place",
            "dt=" not in txt(dst, "ppt/comments/comment1.xml") and "вопрос" in txt(dst, "ppt/comments/comment1.xml"))
        chk("pptx STEALTH: thumbnail and printer removed, OLE .bin stayed",
            "docProps/thumbnail.jpeg" not in names(dst) and "ppt/printerSettings/printerSettings1.bin" not in names(dst)
            and "ppt/embeddings/oleObject1.bin" in names(dst))
        chk("pptx STEALTH: verify_ooxml", verify_ooxml(src_p, dst)["ok"], verify_ooxml(src_p, dst)["errors"])
        res, dst = run(src_p, PA, "pP")
        chk("pptx PARANOID: ok", res.ok, res.errors)
        b, a, sv = oracle(src_p, dst)
        chk("pptx PARANOID: oracle -- nothing survived", not sv, [(s.label, s.found_at) for s in sv][:5])
        sl = txt(dst, "ppt/slides/slide1.xml")
        chk("pptx PARANOID: OLE removed together with AlternateContent, the reference and the "
            "part too",
            "oleObj" not in sl and "AlternateContent" not in sl and "ppt/embeddings/oleObject1.bin" not in names(dst)
            and "oleObject" not in txt(dst, "ppt/slides/_rels/slide1.xml.rels"))
        v = verify_ooxml(src_p, dst)
        chk("pptx PARANOID: verify_ooxml", v["ok"], v["errors"])

        # ------------------------------------------------------------------ project fixtures
        from tests.fixtures import make_docx, make_docx_wordlike, make_xlsx
        fx = tempfile.mkdtemp(prefix="oox_fx_")
        for label, mk in (("make_docx", make_docx), ("make_docx_wordlike", make_docx_wordlike), ("make_xlsx", make_xlsx)):
            path = mk(fx)[0]
            for prof in (ST, PA):
                r_, d_ = run(path, prof, "%s_%s" % (label, prof.value[0]))
                chk("%s %s: ok" % (label, prof.value), r_.ok, r_.errors)
                if not r_.ok:
                    continue
                b, a, sv = oracle(path, d_)
                chk("%s %s: oracle -- nothing survived" % (label, prof.value), not sv,
                    [(s.label, s.found_at) for s in sv][:5])
                chk("%s %s: verify_ooxml" % (label, prof.value), verify_ooxml(path, d_)["ok"], verify_ooxml(path, d_)["errors"])
                if label != "make_docx":
                    chk("%s %s: no new ai" % (label, prof.value), "ai" not in new_signals(b, a) or prof is PA,
                        new_signals(b, a))
        r_, d_ = run(make_docx(fx)[0], ST, "mkd_t")
        with zipfile.ZipFile(d_) as z:
            chk("make_docx (real timestamps, Application=Microsoft): normalized to 1980-01-01",
                all(tuple(i.date_time) == EPOCH for i in z.infolist()))
        chk("make_docx: real ZIP time in the action list", any(
            a_.act is Act.NORMALIZED and "timestamps" in a_.label for a_ in r_.actions))
        # a different application with real times: profile consistency matters more, so they stay and we say so
        lo = [(n, (_app("LibreOffice/7.6", "7.6", "", "") if n == "docProps/app.xml" else bd)) for n, bd in _fx_docx()]
        real_dt = (2025, 6, 7, 8, 9, 10)
        r_, d_ = run(build("lo.docx", lo, dt=real_dt), ST, "lo")
        with zipfile.ZipFile(d_) as z:
            chk("different application: timestamps untouched and recorded as KEPT",
                r_.ok and all(tuple(i.date_time) == real_dt for i in z.infolist())
                and any(a_.act is Act.KEPT and "timestamps" in a_.label for a_ in r_.actions), r_.errors)
        # hidden bytes outside entries are removed
        raw = bytearray(open(src, "rb").read())
        e2 = raw.rfind(b"PK\x05\x06")
        struct.pack_into("<H", raw, e2 + 20, 12)
        open(P("slack.docx"), "wb").write(bytes(raw) + b"hidden-comm!")
        r_, d_ = run(P("slack.docx"), ST, "slack")
        chk("the archive comment (a hiding place) is removed",
            r_.ok and zipfile.ZipFile(d_).comment == b"" and any("Hidden bytes" in a_.label for a_ in r_.actions), r_.errors)

        # a document with non-standard XML layout: foreign prefix, single quotes, spaces around '='
        fancy = []
        for n, bd in _fx_docx():
            if n == "word/document.xml":
                s = bd.replace("w:", "q:").replace("xmlns:w=", "xmlns:q=").replace('w16du:', 'z:').replace("xmlns:w16du", "xmlns:z")
                s = re.sub(r'(\S+)="([^"]*)"', lambda m: "%s = '%s'" % (m.group(1), m.group(2)) if m.group(1).startswith("q:") else m.group(0), s)
                bd = s
            fancy.append((n, bd))
        r_, d_ = run(build("fancy.docx", fancy), ST, "fancy")
        dx = txt(d_, "word/document.xml") if r_.ok else ""
        chk("non-standard XML layout: authors and dates are cleaned regardless of prefix and "
            "quotes",
            r_.ok and dx.count("q:author = 'Author'") == 2 and "q:date" not in dx and "dateUtc" not in dx
            and K["rev"] not in dx, r_.errors)

        # signed package
        sigs = _fx_docx() + [("_xmlsignatures/origin.sigs", b""), ("_xmlsignatures/sig1.xml", "<Signature/>"),
                             ("_xmlsignatures/_rels/origin.sigs.rels", _rels_xml([("rId1", "http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/signature", "sig1.xml", False)]))]
        sigs = [(n, (bd.replace("</Relationships>", '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/origin" Target="_xmlsignatures/origin.sigs"/></Relationships>')
                     if n == "_rels/.rels" else bd)) for n, bd in sigs]
        sigs = [(n, (bd.replace("</Types>", '<Default Extension="sigs" ContentType="application/vnd.openxmlformats-package.digital-signature-origin"/><Override PartName="/_xmlsignatures/sig1.xml" ContentType="application/vnd.openxmlformats-package.digital-signature-xmlsignature+xml"/></Types>')
                     if n == "[Content_Types].xml" else bd)) for n, bd in sigs]
        src_s = build("sig.docx", sigs)
        r_ = bad("signed package without force_signed", src_s)
        chk("signed package: the reason is named", r_ is not None and "signed" in " ".join(r_.errors).lower())
        rs = inspect_file(src_s)
        chk("signed package: the inspector warns before the cleaner refuses, same test of what is signed",
            any(s_.kind == "hazard" and "digitally signed" in s_.detail for s_ in rs.signals)
            and any(f.label == "Package digital signature" and f.risk.value == "identity" and not f.removable
                    for f in rs.findings))
        rn = inspect_file(P("w.docx"))
        chk("an unsigned package: no signature warning",
            not any("digitally signed" in s_.detail for s_ in rn.signals))
        r_, d_ = run(src_s, ST, "sig", force_signed=True)
        chk("signed package with force_signed: signature removed entirely, package intact",
            r_.ok and not any(n.startswith("_xmlsignatures/") for n in names(d_)) and "sigs" not in txt(d_, "[Content_Types].xml")
            and "signature" not in txt(d_, "_rels/.rels") and verify_ooxml(src_s, d_)["ok"], r_.errors)

        # a customXml part with no namespace and some text: reported as UNREMOVABLE, the cleaning goes on
        nons = _fx_docx() + [("customXml/item9.xml", '<?xml version="1.0"?><root>Some secret text</root>')]
        r_, d_ = run(build("nons.docx", nons), ST, "nons")
        chk("customXml root without a namespace and with text: UNREMOVABLE, not a failed section",
            r_.ok and any(a_.act is Act.UNREMOVABLE and a_.location == "customXml/item9.xml"
                          and '"root"' in a_.note for a_ in r_.actions), r_.errors)

        # properties and document variables named after a person, in English: removed, while a date or
        # a status with "review"/"approval" in its name holds no person and stays
        who = {"Reviewer": "Petr Ivanovich Smirnov", "Approved By": "Olga Sergeevna Belova",
               "Executor": "Anna Pavlovna Orlova"}
        harmless = {"ReviewDate": "2024-03-01", "ApprovalStatus": "Final"}
        en = []
        for n, bd in _fx_docx():
            if n == "docProps/custom.xml":
                bd = _custom([("ContentTypeId", K["ctid"])] + list(who.items()) + list(harmless.items()))
            elif n == "word/settings.xml":
                bd = bd.replace("</w:docVars>", '<w:docVar w:name="Reviewer" w:val="Maria Ivanovna Koval"/></w:docVars>')
            en.append((n, bd))
        src_en = build("en_names.docx", en)
        r_, d_ = run(src_en, ST, "en_names")
        b, a, sv = oracle(src_en, d_)
        out = txt(d_, "docProps/custom.xml") + txt(d_, "word/settings.xml")
        chk("English person-named properties and docVar: removed, oracle sees nothing survive",
            r_.ok and not sv and not any(v in out for v in list(who.values()) + ["Maria Ivanovna Koval"]),
            [(s_.label, s_.found_at) for s_ in sv][:4] or r_.errors)
        # legacy Excel comments in openpyxl's layout; Excel's tc={GUID} placeholder is left
        # as is; and cleaning the output once more finds nothing: the neutral "Author" it
        # wrote is not taken for a surviving name
        X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        guid = "tc={1A2B3C4D-0000-1111-2222-333344445555}"
        cm = ('<?xml version="1.0"?><comments xmlns="%s"><authors><author>Ivan Petrovich Sidorov</author>'
              '<author>%s</author></authors><commentList/></comments>' % (X, guid))
        xl = [(n, bd) for n, bd in _fx_xlsx()] + [("xl/comments/comment1.xml", cm)]
        src_cm = build("openpyxl_comments.xlsx", xl)
        r_, d_ = run(src_cm, ST, "openpyxl_comments")
        cm_out = txt(d_, "xl/comments/comment1.xml")
        chk("openpyxl comments: the author is gone, the tc={GUID} placeholder is untouched and not reported",
            r_.ok and "Ivan Petrovich Sidorov" not in cm_out and guid in cm_out
            and not any(a_.before == guid for a_ in r_.actions), r_.errors)
        r2, d2 = run(d_, ST, "openpyxl_comments_again")
        b2, a2, sv2 = oracle(d_, d2)
        # (a path the first pass turned into a bare file name is still there; that is by design)
        authors = [(s_.label, s_.found_at) for s_ in sv2 if "comment" in s_.found_at.lower()
                   or "author" in s_.label.lower()]
        chk("cleaning the cleaned file again: the neutral \"Author\" is not taken for a surviving name",
            r2.ok and not authors, authors[:4])

        chk("ReviewDate and ApprovalStatus are not taken for a person and stay",
            r_.ok and all(v in out for v in harmless.values()) and all(
                f.risk.value == "provenance" for f in b.findings
                if f.location == "docProps/custom.xml" and f.label in harmless))

        # a reference from XML to a relationship of an unknown kind: the part isn't removed, honestly UNREMOVABLE, package intact
        weird = [(n, (bd.replace('<w:p w:rsidR="00B54A06"', '<w:p w:rsidR="00B54A06"><w:foo r:id="rId23"/></w:p><w:p w:rsidR="00B54A06"', 1)
                      if n == "word/document.xml" else bd)) for n, bd in _fx_docx()]
        r_, d_ = run(build("weird.docx", weird), ST, "weird")
        chk("reference to a printer from a foreign element: part kept, UNREMOVABLE, package "
            "intact",
            r_.ok and "word/printerSettings/printerSettings1.bin" in names(d_) and any(
                a_.act is Act.UNREMOVABLE and "printerSettings1.bin" in a_.location for a_ in r_.actions)
            and verify_ooxml(P("weird.docx"), d_)["ok"], r_.errors)

        # many mailto links: all removed, the link texts stay
        many = []
        for n, bd in _fx_docx():
            if n == "word/_rels/document.xml.rels":
                bd = bd.replace("</Relationships>", "".join(
                    '<Relationship Id="rM%d" Type="%shyperlink" Target="mailto:u%d@romashka-test.ru" TargetMode="External"/>'
                    % (i, _RT, i) for i in range(40)) + "</Relationships>")
            if n == "word/document.xml":
                bd = bd.replace("</w:p><w:sectPr", "".join(
                    '<w:hyperlink r:id="rM%d"><w:r><w:t>почта%d</w:t></w:r></w:hyperlink>' % (i, i) for i in range(40)) + "</w:p><w:sectPr", 1)
            many.append((n, bd))
        r_, d_ = run(build("many.docx", many), ST, "many")
        dm = txt(d_, "word/document.xml") if r_.ok else ""
        chk("40 mailto links: all removed, texts in place, package intact", r_.ok and "rM" not in dm and "почта39" in dm
            and "romashka-test.ru" not in txt(d_, "word/_rels/document.xml.rels") and verify_ooxml(P("many.docx"), d_)["ok"], r_.errors)

        # the exact time in ZIP extra fields survives normalizing the DOS time: honestly UNREMOVABLE
        ut = P("ut.docx")
        with zipfile.ZipFile(ut, "w", zipfile.ZIP_DEFLATED) as zf:
            for n, bd in _fx_docx():
                zi = zipfile.ZipInfo(n, EPOCH)
                zi.extra = struct.pack("<HHBL", 0x5455, 5, 1, 1700000000)
                zf.writestr(zi, bd.encode("utf-8") if isinstance(bd, str) else bd)
        r_, d_ = run(ut, ST, "ut")
        chk("time in ZIP extra fields: UNREMOVABLE, not silence", r_.ok and any(
            a_.act is Act.UNREMOVABLE and a_.location == "ZIP" for a_ in r_.actions), r_.errors)

        # an embedded PDF is cleaned recursively
        try:
            from tests.fixtures import make_pdf
            pd = tempfile.mkdtemp(prefix="oox_pdf_")
            pdf_path, pdf_exp = make_pdf(pd)
            pdf_bytes = open(pdf_path, "rb").read()
            shutil.rmtree(pd, ignore_errors=True)
            r_, d_ = run(build("pdf.docx", _fx_docx() + [("word/embeddings/embedded.pdf", pdf_bytes)]), ST, "pdf")
            b, a, sv = oracle(P("pdf.docx"), d_)
            chk("embedded PDF cleaned recursively: no value survived", r_.ok and not sv and read(d_, "word/embeddings/embedded.pdf") != pdf_bytes,
                [(s.label, s.found_at) for s in sv][:4] or r_.errors)
        except ImportError:
            pass

        # the actions make sense: each has a location and a label, values are bounded
        chk("actions: each has a location and a label, values no longer than the limit",
            all(a_.location and a_.label and len(a_.before) <= 400 and len(a_.after) <= 400 for a_ in res.actions) and res.actions)

        # ------------------------------------------------------------------ pathologies
        open(P("empty.docx"), "wb").close()
        bad("empty file", P("empty.docx"))
        open(P("pk.docx"), "wb").write(b"PK\x03\x04" + b"junk" * 60)
        bad("non-ZIP with a PK signature", P("pk.docx"))
        open(P("junk.docx"), "wb").write(b"x" * 500)
        bad("junk", P("junk.docx"))
        whole = open(src, "rb").read()
        for cut in (len(whole) // 2, len(whole) - 30, len(whole) - 1):
            open(P("cut.docx"), "wb").write(whole[:cut])
            bad("truncated docx (%d)" % cut, P("cut.docx"))
        bad("no file", P("нет_такого.docx"))
        bad("directory instead of a file", td)
        r_ = clean_ooxml(src, src)
        chk("input == output: refused, input intact", (not r_.ok) and _sha(src) == _sha(src) and r_.errors, r_.errors)
        open(P("keepme.docx"), "wb").write(b"old content")
        r_ = clean_ooxml(P("junk.docx"), P("keepme.docx"))
        chk("a refusal doesn't touch an existing dst", (not r_.ok) and open(P("keepme.docx"), "rb").read() == b"old content")
        r_ = clean_ooxml(src, P("keepme.docx"))
        chk("success atomically replaces an existing dst", r_.ok and open(P("keepme.docx"), "rb").read() != b"old content"
            and zipfile.ZipFile(P("keepme.docx")).testzip() is None and not leftovers(), r_.errors)
        r_ = clean_ooxml(src, P("x.docx"), "garbage")
        chk("unknown profile: ok=False, no exception", (not r_.ok) and r_.errors)
        r_ = clean_ooxml(src, P("str.docx"), "paranoid")
        chk("profile as a string is accepted", r_.ok, r_.errors)
        r_ = clean_ooxml(src, os.path.join(td, "новый каталог", "глубже", "o.docx"))
        chk("the output directory is created", r_.ok and os.path.isfile(os.path.join(td, "новый каталог", "глубже", "o.docx")), r_.errors)
        txtfile = build("t.txt", [("a.txt", "x")])
        bad("ZIP without Content_Types (not OOXML)", txtfile)
        # a broken part that needs editing
        garbage = [(n, ("this is not XML at all" if n == "docProps/core.xml" else bd)) for n, bd in _fx_docx()]
        bad("docx with a broken docProps/core.xml", build("g1.docx", garbage))
        dtd = [(n, ('<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><cp:coreProperties xmlns:cp="urn:x"/>'
                    if n == "docProps/core.xml" else bd)) for n, bd in _fx_docx()]
        bad("docx with a DOCTYPE in core.xml", build("g2.docx", dtd))
        u16 = [(n, ("<?xml version='1.0' encoding='utf-16'?><a/>".encode("utf-16") if n == "docProps/app.xml" else bd))
               for n, bd in _fx_docx()]
        bad("docx with UTF-16 in app.xml", build("g3.docx", u16))
        brokenrels = [(n, ("<Relationships" if n == "word/_rels/document.xml.rels" else bd)) for n, bd in _fx_docx()]
        bad("docx with a broken .rels", build("g4.docx", brokenrels))
        # a broken part at the ZIP level: corrupt the compressed bytes of styles.xml (CRC)
        e = {x.name: x for x in zipfix.entries(src)}["word/styles.xml"]
        mut = bytearray(whole)
        mut[e.header_offset + 30 + len(e.name) + 3] ^= 0xFF
        open(P("crc.docx"), "wb").write(bytes(mut))
        bad("docx with a broken entry (CRC)", P("crc.docx"))
        with warnings_off():
            dup = build("dup.docx", _fx_docx() + [("word/document.xml", "<a/>")])
        bad("duplicate entry names", dup)
        trav = build("trav.docx", _fx_docx() + [("../evil.xml", "<a/>")])
        bad("path traversal in an entry name", trav)

        # ------------------------------------------------------------------ fuzzing: corrupting a valid package
        rnd = random.Random(5)
        pool = [open(src, "rb").read(), open(src_x, "rb").read(), open(src_p, "rb").read()]
        for it in range(150):
            b = bytearray(rnd.choice(pool))
            for _ in range(rnd.randint(1, 5)):
                at = rnd.randrange(len(b))
                m = rnd.random()
                if m < .5:
                    b[at] = rnd.getrandbits(8)
                elif m < .8:
                    del b[at:at + rnd.randint(1, 30)]
                else:
                    b[at:at] = bytes(rnd.getrandbits(8) for _ in range(rnd.randint(1, 20)))
            fz = P("fz.docx")
            open(fz, "wb").write(bytes(b))
            h0 = _sha(fz)
            fo = P("fz_out.docx")
            if os.path.exists(fo):
                os.remove(fo)
            try:
                r_ = clean_ooxml(fz, fo, ST if it % 2 else PA)
            except Exception as exc:  # noqa: BLE001
                chk("fuzz %d: doesn't raise" % it, False, repr(exc))
                continue
            chk("fuzz %d: input intact" % it, _sha(fz) == h0)
            chk("fuzz %d: no temporary files" % it, not leftovers(), leftovers())
            if r_.ok:
                try:
                    with zipfile.ZipFile(fo) as z:
                        good = z.testzip() is None
                except Exception:  # noqa: BLE001
                    good = False
                chk("fuzz %d: ok => valid output" % it, good and verify_ooxml(fz, fo)["ok"])
            else:
                chk("fuzz %d: refused with a reason, dst not created" % it, bool(r_.errors) and not os.path.exists(fo))

        # ------------------------------------------------------------------ structure-aware fuzzing:
        # corrupting the CONTENT of XML parts inside a whole, valid ZIP -- this is exactly how "malformed"
        # files reach us, and exactly where the edit engine could crash
        rnd = random.Random(23)
        kits = [_fx_docx(ole=True), _fx_xlsx(), _fx_pptx()]
        ext = [".docx", ".xlsx", ".pptx"]
        survived_ok = refused = 0
        for it in range(240):
            k = rnd.randrange(3)
            parts = []
            for n, bd in kits[k]:
                raw_b = bd.encode("utf-8") if isinstance(bd, str) else bd
                if n.endswith((".xml", ".rels")) and rnd.random() < 0.05:
                    b = bytearray(raw_b)
                    for _ in range(rnd.randint(1, 3)):
                        if not b:
                            break
                        at = rnd.randrange(len(b))
                        m = rnd.random()
                        if m < .3:
                            b[at] = rnd.choice(b'<>"\'=/& \r\n')
                        elif m < .6:
                            del b[at:at + rnd.randint(1, 25)]
                        elif m < .8:                       # a duplicated chunk: tags break and multiply
                            seg = bytes(b[at:at + rnd.randint(5, 60)])
                            b[at:at] = seg
                        else:
                            b[at:at] = bytes(rnd.getrandbits(8) for _ in range(rnd.randint(1, 6)))
                    raw_b = bytes(b)
                parts.append((n, raw_b))
            fz = P("sf%s" % ext[k])
            _zip_write(fz, parts)
            h0 = _sha(fz)
            fo = P("sf_out%s" % ext[k])
            if os.path.exists(fo):
                os.remove(fo)
            try:
                r_ = clean_ooxml(fz, fo, ST if it % 2 else PA)
            except Exception as exc:  # noqa: BLE001
                chk("structural fuzz %d: doesn't raise" % it, False, repr(exc))
                continue
            chk("structural fuzz %d: input intact, no temporary files" % it, _sha(fz) == h0 and not leftovers(), leftovers())
            if r_.ok:
                survived_ok += 1
                vv = verify_ooxml(fz, fo)
                chk("structural fuzz %d: ok => verify_ooxml" % it, vv["ok"], vv["errors"])
            else:
                refused += 1
                chk("structural fuzz %d: refused with a reason, dst not created" % it, bool(r_.errors) and not os.path.exists(fo),
                    r_.errors)
        chk("structural fuzzing: files both get cleaned and get refused (both paths alive)", survived_ok > 20 and refused > 20, (survived_ok, refused))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("TOTAL: %d checks, %d failed" % tuple(stat))
    return stat[1] == 0


def _expected_survivor(s):
    """Survivors the cleaner keeps on purpose and the inspector takes for a leak: Power Query code
    (DataMashup, UNREMOVABLE), the internal Power Query connection (points to no external server),
    a structured reference Table1[Column] (a formula, not a path)."""
    return ("item4.xml" in s.found_at or "xl/connections.xml" in s.found_at or "definedName Таблица" in s.found_at)


class warnings_off:
    """zipfile writes a UserWarning on duplicate names: the test needs them, not the noise."""

    def __enter__(self):
        import warnings
        self._w = warnings.catch_warnings()
        self._w.__enter__()
        warnings.simplefilter("ignore")

    def __exit__(self, *a):
        return self._w.__exit__(*a)


def _check_real(path, say):
    """Cleaning metrics for one real file in both profiles. Only numbers, part names and field
    names are printed: file values and personal data never are."""
    from .clean import survived_values
    from .inspect import inspect_file

    good = True
    before = inspect_file(path)
    kinds0 = {s.kind for s in before.signals}
    ents0 = zipfix.entries(path)
    say("  source: %s, entries %d, critical findings %d, all sensitive %d, parse errors %d"
        % (sniff.pretty(sniff.sniff(path)), len(ents0), before.critical, before.sensitive, len(before.errors)))
    for prof in (Profile.STEALTH, Profile.PARANOID):
        td = tempfile.mkdtemp(prefix="oox_real_")
        try:
            dst = os.path.join(td, "out" + os.path.splitext(path)[1])
            h0 = _sha(path)
            res = clean_ooxml(path, dst, prof)
            h1 = _sha(path)
            say("  --- profile %s: ok=%s, errors %d%s" % (prof.value, res.ok, len(res.errors),
                                                          ("; " + "; ".join(res.errors)[:300]) if res.errors else ""))
            say("      input sha256 before/after: %s / %s -- %s" % (h0[:16], h1[:16], "MATCH" if h0 == h1 else "MISMATCH"))
            good &= res.ok and h0 == h1
            if not res.ok:
                continue
            after = inspect_file(dst)
            sv = survived_values(before, after)
            v = verify_ooxml(path, dst)
            kinds1 = {s.kind for s in after.signals}
            new = sorted(kinds1 - kinds0)
            say("      critical findings: %d -> %d; all sensitive: %d -> %d; original values "
                "survived: %d"
                % (before.critical, after.critical, before.sensitive, after.sensitive, len(sv)))
            for s in sv:
                say("        survived: \"%s\" in %s -> found: %s" % (s.label, s.location, s.found_at))
            say("      opens: zipfile=%s, testzip=%s, [Content_Types].xml first=%s, verify_ooxml "
                "ok=%s"
                % (v["opens"], v["testzip"], v["content_types_first"], v["ok"]))
            say("      entries %d -> %d, removed: %s" % (v["entries_src"], v["entries_dst"], ", ".join(v["dropped"]) or "nothing"))
            ents1 = zipfix.entries(dst)
            say("      entry order matches: %s; timestamps match the source: %s (source: %s; "
                "output: %s%s)"
                % (v["order_preserved"], v["times_same"],
                   ", ".join(sorted({"%04d-%02d-%02d" % tuple(e.date_time[:3]) for e in ents0})),
                   ", ".join(sorted({"%04d-%02d-%02d" % tuple(e.date_time[:3]) for e in ents1})),
                   "" if v["times_same"] else "; real ZIP time deliberately normalized to Word's "
                                              "form"))
            say("      parts changed: %d of %d, the rest copied as raw compressed bytes"
                % (len(v["edited"]), v["entries_dst"]))
            a0, a1 = _app_name(path), _app_name(dst)
            say("      Application: %s" % ("kept as it was" if a0 == a1 and a0 else
                                           "removed (the profile removes all provenance)" if a0 and not a1 else
                                           "not declared in the source" if not a0 else "CHANGED"))
            say("      signals before: %s; after: %s; new: %s%s"
                % (sorted(kinds0), sorted(kinds1), new or "none",
                   "".join("\n        new %s: %s" % (s.kind, clip(s.detail, 200)) for s in after.signals
                           if s.kind in ("ai", "scrubbed") and s.kind in new)))
            say("      actions: %s" % ", ".join("%s %d" % (a.value, res.count(a)) for a in Act if res.count(a)))
            good &= (not sv) and v["ok"] and (prof is Profile.PARANOID or "ai" not in new)
        finally:
            shutil.rmtree(td, ignore_errors=True)
    return good


def _smoke_pathologies(say):
    """Pathological inputs: each gives ok=False, a reason in errors, no exception, input intact."""
    td = tempfile.mkdtemp(prefix="oox_path_")
    good = True
    try:
        cases = []
        base = _zip_write(os.path.join(td, "base.docx"), _fx_docx())
        whole = open(base, "rb").read()
        cases.append(("empty file (0 bytes)", b""))
        cases.append(("non-ZIP with a PK signature", b"PK\x03\x04" + b"junk" * 60))
        cases.append(("truncated docx (half)", whole[:len(whole) // 2]))
        cases.append(("truncated docx (without the directory tail)", whole[:-30]))
        e = {x.name: x for x in zipfix.entries(base)}["word/styles.xml"]
        mut = bytearray(whole)
        mut[e.header_offset + 30 + len(e.name) + 3] ^= 0xFF
        cases.append(("docx with a broken entry (CRC of styles.xml)", bytes(mut)))
        for label, name in (("docx with a broken docProps/core.xml (not XML)", "docProps/core.xml"),):
            cases.append((label, open(_zip_write(os.path.join(td, "g.docx"), [
                (n, ("this is not XML at all" if n == name else b)) for n, b in _fx_docx()]), "rb").read()))
        cases.append(("docx with a DOCTYPE in core.xml", open(_zip_write(os.path.join(td, "g2.docx"), [
            (n, ('<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a/>' if n == "docProps/core.xml" else b))
            for n, b in _fx_docx()]), "rb").read()))
        with warnings_off():
            cases.append(("duplicate entry names", open(_zip_write(os.path.join(td, "d.docx"), _fx_docx() + [
                ("word/document.xml", "<a/>")]), "rb").read()))
        cases.append(("path traversal in an entry name", open(_zip_write(os.path.join(td, "t.docx"), _fx_docx() + [
            ("../evil.xml", "<a/>")]), "rb").read()))
        for label, data in cases:
            src, dst = os.path.join(td, "in.docx"), os.path.join(td, "never.docx")
            open(src, "wb").write(data)
            h0 = _sha(src)
            try:
                res = clean_ooxml(src, dst)
                exc = ""
            except Exception as ex:  # noqa: BLE001
                res, exc = None, type(ex).__name__
            ok = res is not None and (not res.ok) and bool(res.errors) and _sha(src) == h0 and not os.path.exists(dst)
            good &= ok
            say("  %-44s -> %s%s%s" % (label, "ok=False, errors=%d, input intact, no dst" % len(res.errors) if ok else "FAIL",
                                      (" [exception %s]" % exc) if exc else "",
                                      ("; reason: " + clip(res.errors[0], 110)) if res is not None and res.errors else ""))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return good


def _app_name(path):
    try:
        with zipfile.ZipFile(path) as z:
            for n in _parse(z.read("docProps/app.xml"), None, True):
                if n.name == "Application":
                    return n.text().strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if args and args[0] == "--smoke":
        # Metrics on the project fixtures (+ pathologies): what the acceptance report shows.
        from tests.fixtures import make_docx, make_docx_wordlike, make_xlsx
        fx = tempfile.mkdtemp(prefix="oox_fx_")
        allgood = True
        try:
            for label, mk in (("make_docx", make_docx), ("make_docx_wordlike", make_docx_wordlike), ("make_xlsx", make_xlsx)):
                print("fixture %s:" % label)
                allgood &= _check_real(mk(fx)[0], print)
        finally:
            shutil.rmtree(fx, ignore_errors=True)
        print("pathologies:")
        allgood &= _smoke_pathologies(print)
        sys.exit(0 if allgood else 1)
    if args and args[0] == "--real-word":
        # Acceptance on a file really saved by Word. Synthetic data checks that the
        # code does what was intended; only a real file checks that what was intended
        # is right. Office is optional: without it this is an honest skip, not a failure.
        from tests.real_office import Unavailable, make_word_files
        try:
            made = make_word_files()
        except Unavailable as exc:
            print("acceptance on a real file skipped: %s" % exc)
            sys.exit(0)
        allgood = True
        for name in sorted(made):
            if not name.endswith((".docx", ".xlsx", ".pptx")):
                continue        # the rest is cleaned by other modules (phase 2: ODF, RTF)
            print("real file from Word (%s):" % name)
            allgood &= _check_real(made[name], print)
        sys.exit(0 if allgood else 1)
    if args and args[0] == "--real":
        allgood = True
        for n, p in enumerate(args[1:], 1):
            print("file %d:" % n)
            try:
                allgood &= _check_real(p, print)
            except Exception as exc:  # noqa: BLE001
                allgood = False
                print("  check FAILED: %s" % type(exc).__name__)
        sys.exit(0 if allgood else 1)
    sys.exit(0 if _selftest("-v" in args) else 1)
