"""Re-create a file through LibreOffice. Phase 4, regenerate profile.

An optional path: no soffice means an honest refusal, not a silent fallback
to in-place cleaning. The user chose regenerate for invisibility.

Conversion runs with a throwaway profile and an empty user name.
Otherwise LibreOffice would stamp the machine owner's name into the document.

Public API:
    find_soffice() -> str | None
    clean_regen(src, dst, profile, keep, force_signed=False) -> CleanResult
        the result's fmt is the OUTPUT format (doc becomes docx).

Never raises. The input is not modified.
"""

from __future__ import annotations

import glob
import html
import os
import re
import shutil
import subprocess
import tempfile

from core.cleanmodel import Act, CleanResult, Profile
from core.i18n import tr

__all__ = ["clean_regen", "find_soffice", "OUTPUT_FMT"]

# Legacy OLE2 can't be cleaned in place: the honest way out is modern OOXML.
OUTPUT_FMT = {
    "doc": "docx", "xls": "xlsx", "ppt": "pptx",
    "docx": "docx", "xlsx": "xlsx", "pptx": "pptx",
    "odt": "odt", "ods": "ods", "odp": "odp", "rtf": "rtf",
}


# Where LibreOffice lives when it's not on PATH. Windows and macOS don't add
# it to PATH; on Linux the packaged soffice is usually on PATH, but snap and
# manual installs into /opt are not.
_SOFFICE_PLACES = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    os.path.expanduser("~/Applications/LibreOffice.app/Contents/MacOS/soffice"),
    "/usr/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
    "/snap/bin/libreoffice",
)


def find_soffice():
    """Path to soffice if LibreOffice is installed, otherwise None.

    Windows: soffice.com, not soffice.exe; the exe detaches from the console and
    prints nothing. Standard install directories first, then PATH.
    """
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        if not base:
            continue
        path = os.path.join(base, "LibreOffice", "program", "soffice.com")
        if os.path.isfile(path):
            return path
    found = shutil.which("soffice.com") or shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    # /opt/libreofficeX.Y: that's how the official .deb/.rpm from the website installs
    extra = sorted(glob.glob("/opt/libreoffice*/program/soffice"), reverse=True)
    for path in _SOFFICE_PLACES + tuple(extra):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _file_url(path) -> str:
    return "file:///" + os.path.abspath(path).replace("\\", "/").lstrip("/")


def _patch_profile(profile_dir):
    """Add personal-data removal. LibreOffice overwrites a prepared xcu on first
    start, so the file it created has to be edited afterwards."""
    path = os.path.join(profile_dir, "user", "registrymodifications.xcu")
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if "RemovePersonalInfoOnSaving" in text:
        return True
    extra = (
        '<item oor:path="/org.openoffice.Office.Common/Security">'
        '<prop oor:name="RemovePersonalInfoOnSaving" oor:op="fuse"><value>true</value></prop>'
        '<prop oor:name="RemoveEditingTimeOnSaving" oor:op="fuse"><value>true</value></prop>'
        "</item>"
    )
    if "</oor:items>" not in text:
        return False
    text = text.replace("</oor:items>", extra + "</oor:items>", 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


def _run(soffice, profile_dir, extra, timeout):
    cmd = [
        soffice, "--headless", "--norestore", "--nolockcheck", "--nologo",
        "--nofirststartwizard",
        "-env:UserInstallation=%s" % _file_url(profile_dir),
    ] + extra
    return subprocess.run(cmd, timeout=timeout, capture_output=True, check=False)


def _empty_profile(root) -> str:
    user = os.path.join(root, "user")
    os.makedirs(user, exist_ok=True)
    return root


# Attributes LibreOffice loads something from during conversion:
# Target in .rels, xlink:href in ODF, href/src in VML.
_LINK_ATTR_RE = re.compile(
    rb'((?:Target|href|r:link|src)\s*=\s*)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)
# Where a load takes us off the machine: any scheme with "//" (http, ftp, smb,
# dav...), file: and UNC (\\server, //server), which on Windows is an SMB
# connection that hands a foreign server the bot owner's IP and NTLM hash,
# and an absolute path with a drive letter. Measured: LibreOffice opens the
# file behind a file:// image link.
# Internal package targets ("media/x.png", "/word/document.xml") don't match.
_REMOTE_RE = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:)?(?://|\\\\)|^file:|^[a-z]:[\\/]",
                        re.IGNORECASE)


def _remote(value: bytes) -> bool:
    """Does the attribute value point outside? The DECODED value is checked:
    LibreOffice's XML parser reads "&#104;ttp://" as http://, while such an
    address slipped past a check on raw bytes. URL parsers drop whitespace and
    control characters inside an address, and so do we."""
    text = html.unescape(value.decode("utf-8", "replace"))
    return bool(_REMOTE_RE.match(re.sub(r"[\x00-\x20]", "", text)))


def _defang_bytes(raw: bytes):
    """External links -> an empty string of the same shape. Returns (bytes, count)."""
    hits = 0

    def sub(m):
        nonlocal hits
        if not _remote(m.group(3)):
            return m.group(0)
        hits += 1
        return m.group(1) + m.group(2) + m.group(2)

    return _LINK_ATTR_RE.sub(sub, raw), hits


def _defang_external(src, work, res):
    """A copy of the file without external links; None (refusal) on any hiccup.

    Works on the raw bytes of the ZIP package's XML parts (OOXML and ODF) and on
    RTF text. No XML parsing is needed: the goal is that LibreOffice finds
    nothing to load, not a model document. A link is replaced with an empty
    string of the same shape, so the structure doesn't break.

    The output file doesn't suffer from this: external links are removed by the
    second pass as ENVIRONMENT anyway.
    """
    try:
        import zipfile

        from . import zipfix
        from .sniff import family, sniff

        fam = family(sniff(src))
        out = os.path.join(work, "defanged" + os.path.splitext(src)[1])

        if fam in ("ooxml", "odf"):
            replace, hits = {}, 0
            with zipfile.ZipFile(src) as z:
                for name in z.namelist():
                    if not name.lower().endswith((".xml", ".rels", ".vml")):
                        continue
                    raw = z.read(name)
                    new, n = _defang_bytes(raw)
                    if n:
                        replace[name] = new
                        hits += n
            if not hits:
                return src
            rep = zipfix.rebuild(src, out, replace=replace)
            if not getattr(rep, "ok", False) or not os.path.isfile(out):
                res.err(tr("External links could not be defused, regeneration cancelled: "
                           "LibreOffice would have followed them over the network."))
                return None
            res.act(Act.REMOVED, tr("external links"), tr("Network links before conversion"),
                    tr("%d pcs") % hits, "",
                    tr("During conversion LibreOffice loads external images by itself, revealing "
                       "your IP to the link's owner. Defused before launch."))
            return out

        if fam == "rtf":
            with open(src, "rb") as fh:
                raw = fh.read()
            new, n = _defang_bytes(raw)
            if not n:
                return src
            with open(out, "wb") as fh:
                fh.write(new)
            res.act(Act.REMOVED, tr("external links"), tr("Network links before conversion"),
                    tr("%d pcs") % n, "", tr("Defused before launching LibreOffice."))
            return out
    except Exception:  # noqa: BLE001
        res.err(tr("Checking external links failed, regeneration cancelled: LibreOffice could "
                   "have followed them over the network."))
        return None
    return src


def _scrub_after_convert(path, res) -> None:
    """Clean the properties of the already regenerated file with our own cleaner.

    STEALTH profile: after conversion the file REALLY was made by LibreOffice,
    so keeping Application=LibreOffice is the truth, and empty identity fields
    are LibreOffice's ordinary state with no user name set. For OOXML that pair
    usually gives cleaning away, but here the whole package was built by a stock
    application, so there's no contradiction.
    """
    try:
        from .sniff import family, sniff
        fam = family(sniff(path))
        if fam == "ooxml":
            import functools

            from .clean_ooxml import clean_ooxml
            # A fresh LibreOffice file has no custom properties: everything
            # there came from the source. STEALTH keeps some of them as harmless,
            # while the .doc inspector counts them as identity; measured on
            # "Department" from DocumentSummaryInformation.
            _inner = functools.partial(clean_ooxml, drop_custom=True)
        elif fam == "odf":
            from .clean_odf import clean_odf as _inner
        elif fam == "rtf":
            # RTF -> RTF: LibreOffice carries \info over just like OOXML properties.
            from .clean_rtf import clean_rtf as _inner
        else:
            res.err(tr("After conversion the format isn't supported by the second pass: document "
                       "properties may remain."))
            return
    except Exception:
        res.err(tr("The post-conversion pass is unavailable: document properties may remain."))
        return

    work = tempfile.mkdtemp(prefix="lo_scrub_")
    try:
        tmp_out = os.path.join(work, os.path.basename(path))
        inner = _inner(path, tmp_out, Profile.STEALTH, None)
        if inner.ok and os.path.isfile(tmp_out) and os.path.getsize(tmp_out) > 0:
            shutil.copyfile(tmp_out, path)
            for a in inner.actions:
                res.actions.append(a)
            for e in inner.errors:
                res.err(e)
        else:
            res.err(tr("The post-conversion pass failed: document properties may remain in the "
                       "file. %s") % ((inner.errors or [""])[0])[:160])
    except Exception:  # noqa: BLE001
        res.err(tr("The post-conversion pass crashed: document properties may remain."))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def clean_regen(src, dst, profile=Profile.REGENERATE, keep=None, force_signed=False) -> CleanResult:
    """Re-save src through LibreOffice into dst. Never raises.

    keep and force_signed are unused: there's one profile, and the stock
    application decides what to keep. The signature matches the other cleaners.
    """
    del keep, force_signed, profile
    res = CleanResult(src=str(src), dst=str(dst), fmt="unknown", profile="regenerate")
    ext = os.path.splitext(src)[1].lower().lstrip(".")
    # sniff hasn't been called yet: the source extension is a hint for LO, and
    # the dispatcher verifies the format by re-inspection.
    target = OUTPUT_FMT.get(ext)
    if target is None:
        res.err(tr("Regenerate doesn't apply to this extension."))
        return res
    res.fmt = target
    soffice = find_soffice()
    if not soffice:
        res.err(tr("LibreOffice not found. Regenerate is not replaced with in-place cleaning."))
        return res
    work = tempfile.mkdtemp(prefix="lo_regen_")
    try:
        profile_dir = _empty_profile(os.path.join(work, "profile"))
        outdir = os.path.join(work, "out")
        os.makedirs(outdir)

        # External links are defused BEFORE conversion. With --convert-to,
        # LibreOffice DOWNLOADS a document's external images by itself: measured,
        # a docx with a:blip r:link=http://... makes three HTTP requests from the
        # owner's machine. For a privacy tool that's a plain leak: the file's sender
        # learns the IP and may reach the internal network.
        # So we convert a copy without external links, not the original.
        src = _defang_external(src, work, res)
        if src is None:
            # Defusing failed: the file is NOT regenerated. Starting LibreOffice
            # "just in case" means hitting a stranger's address over the network:
            # refusing is safer than succeeding here.
            return res
        try:
            # The first run only creates the profile: LibreOffice discards an xcu put
            # there in advance. The setting is added before the second run.
            _run(soffice, profile_dir, ["--terminate_after_init"], 60)
            if not _patch_profile(profile_dir):
                res.err(tr("Could not enable personal-data removal in the LibreOffice profile."))
                return res
            proc = _run(soffice, profile_dir,
                        ["--convert-to", target, "--outdir", outdir, src], 120)
        except subprocess.TimeoutExpired:
            res.err(tr("LibreOffice didn't respond in time. File not created."))
            return res
        except OSError:
            res.err(tr("LibreOffice failed to start. File not created."))
            return res
        produced = os.path.join(
            outdir, os.path.splitext(os.path.basename(src))[0] + "." + target)
        if proc.returncode != 0 or not os.path.isfile(produced) or os.path.getsize(produced) == 0:
            res.err(tr("LibreOffice didn't save the file (code %s).") % proc.returncode)
            return res
        shutil.copyfile(produced, dst)
        res.act(Act.REGENERATED, tr("file"), tr("regeneration"), "", target,
                tr("The file was saved again by LibreOffice: the package was built by a stock "
                   "application, so its structure is consistent throughout. The claimed "
                   "application is now LibreOffice, not the original. The layout may have "
                   "changed."))

        # SECOND PASS; without it this mode leaks. With --convert-to LibreOffice
        # CARRIES OVER document properties: measured, author, last saved by,
        # company and a custom MSIP label with an email reach the output
        # untouched, despite RemovePersonalInfoOnSaving in the profile (that
        # option works when saving from the UI, not during conversion).
        # We can't rely on someone else's setting: regenerate solves
        # INVISIBILITY, and our own cleaner provides cleanliness.
        _scrub_after_convert(dst, res)
        res.ok = True
    except Exception:  # noqa: BLE001
        res.ok = False
        res.err(tr("Internal Regenerate error."))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return res
