"""Remove hidden identifying data from Word and PowerPoint packages.

Shared by the .docx and .pptx writers, which call these on the in-memory
python-docx / python-pptx package just before saving:

* :func:`scrub_core_properties` blanks the text fields of
  ``docProps/core.xml`` (author, last modified by, title, ...).
* :func:`scrub_app_properties` drops ``Company`` and ``Manager`` from
  ``docProps/app.xml``.
* :func:`remove_comments` drops every comment-related part (legacy and
  modern comments, comment authors, people) and the in-document markers
  that point at them, so the saved file has no comment text or author
  names and no dangling references.
* :func:`remove_thumbnail` drops ``docProps/thumbnail.*``, a picture of
  the *original* first page/slide that Office saves with the file.
* :func:`accept_tracked_changes` (Word only) accepts every tracked change:
  deleted and moved-away content is removed, inserted and moved-in content
  is kept as plain text, and formatting-change records are dropped. That
  removes deleted text and every revision author's name.

Parts are written by walking the relationship graph, so dropping the
relationship to a part is what keeps it out of the saved package.
"""

from __future__ import annotations

from typing import Any

from lxml import etree  # type: ignore[import-untyped]  # python-docx/pptx dependency

_TEXT_FIELDS = (
    "author",
    "last_modified_by",
    "title",
    "subject",
    "keywords",
    "comments",
    "category",
    "content_status",
    "identifier",
    "language",
    "version",
)

_COMMENT_RELTYPES = frozenset(
    {
        # Word
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
        "http://schemas.microsoft.com/office/2011/relationships/commentsExtended",
        "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds",
        "http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible",
        "http://schemas.microsoft.com/office/2011/relationships/people",
        # PowerPoint (legacy and modern threaded comments)
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/commentAuthors",
        "http://schemas.microsoft.com/office/2018/10/relationships/comments",
        "http://schemas.microsoft.com/office/2018/10/relationships/authors",
    }
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_P188 = "http://schemas.microsoft.com/office/powerpoint/2018/8/main"

# In-document markers that refer to a comment by id or relationship.
_W_ANCHORS = tuple(f"{{{_W}}}{tag}" for tag in ("commentRangeStart", "commentRangeEnd"))
_W_REFERENCE = f"{{{_W}}}commentReference"
_PPT_COMMENT_REL = f"{{{_P188}}}commentRel"

_THUMBNAIL_RELTYPE = (
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"
)

_APP_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.extended-properties+xml"
_APP_IDENTITY_TAGS = ("Company", "Manager")


def scrub_core_properties(core_props: Any) -> None:
    """Blank every free-text core property (author, last modified by, title, ...)."""
    for field in _TEXT_FIELDS:
        if hasattr(core_props, field):
            setattr(core_props, field, "")


def scrub_app_properties(package: Any) -> None:
    """Remove ``Company`` and ``Manager`` from ``docProps/app.xml``, if present.

    Neither python-docx nor python-pptx models this part, so it is a plain
    blob part: parse it, drop the elements, store the new bytes.
    """
    for part in list(package.iter_parts()):
        if part.content_type != _APP_CONTENT_TYPE or hasattr(part, "_element"):
            continue
        root = etree.fromstring(part.blob)
        removed = False
        for el in list(root):
            if etree.QName(el).localname in _APP_IDENTITY_TAGS:
                root.remove(el)
                removed = True
        if removed:
            part._blob = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )


def _remove_comment_markers(element: Any) -> None:
    for el in list(element.iter(*_W_ANCHORS, _PPT_COMMENT_REL)):
        if el.tag == _PPT_COMMENT_REL:
            # p:extLst/p:ext/p188:commentRel — drop the whole extension entry.
            ext = el.getparent()
            ext_lst = ext.getparent()
            ext_lst.remove(ext)
            if len(ext_lst) == 0:
                ext_lst.getparent().remove(ext_lst)
        else:
            el.getparent().remove(el)
    for ref in list(element.iter(_W_REFERENCE)):
        run = ref.getparent()
        run.remove(ref)
        # The reference lives in its own run (styled "CommentReference"); drop it if empty.
        if run.tag == f"{{{_W}}}r" and all(child.tag == f"{{{_W}}}rPr" for child in run):
            run.getparent().remove(run)


def remove_comment_parts(part: Any) -> None:
    """Drop comment-related relationships from ``part`` (document, presentation or slide)."""
    for rel_id, rel in list(part.rels.items()):
        if rel.reltype in _COMMENT_RELTYPES:
            part.drop_rel(rel_id)


def remove_comments(package: Any) -> None:
    """Remove every comment part and every in-document comment marker."""
    for part in list(package.iter_parts()):
        element = getattr(part, "_element", None)
        if element is not None:
            _remove_comment_markers(element)
        remove_comment_parts(part)


def remove_thumbnail(package: Any) -> None:
    """Drop the package-level thumbnail (an image of the unredacted first page)."""
    # python-docx exposes the package's relationships as ``rels``, python-pptx only as the
    # private ``_rels``. If a python-pptx upgrade renames it, this breaks loudly, and the
    # zip assertions in tests/unit/test_documents/test_ooxml_scrub.py (no
    # docProps/thumbnail* in the output) guard the behaviour.
    rels = package.rels if hasattr(package, "rels") else package._rels
    for rel_id, rel in list(rels.items()):
        if rel.reltype == _THUMBNAIL_RELTYPE:
            rels.pop(rel_id)


# Tracked-change elements, by what accepting the change does to them.
_REVISION_REMOVE = frozenset(
    f"{{{_W}}}{tag}"
    for tag in (
        # deleted / moved-away content (and the run-property markers of the same name)
        "del",
        "moveFrom",
        # range markers and cell-level revision markers, which carry the author
        "moveFromRangeStart",
        "moveFromRangeEnd",
        "moveToRangeStart",
        "moveToRangeEnd",
        "customXmlInsRangeStart",
        "customXmlInsRangeEnd",
        "customXmlDelRangeStart",
        "customXmlDelRangeEnd",
        "customXmlMoveFromRangeStart",
        "customXmlMoveFromRangeEnd",
        "customXmlMoveToRangeStart",
        "customXmlMoveToRangeEnd",
        "cellIns",
        "cellDel",
        "cellMerge",
        # previous-formatting records
        "rPrChange",
        "pPrChange",
        "sectPrChange",
        "tblPrChange",
        "tblPrExChange",
        "trPrChange",
        "tcPrChange",
        "tblGridChange",
        "numberingChange",
    )
)
# Inserted / moved-in content: keep the children, drop the wrapper (a bare
# marker inside w:rPr / w:trPr has no children and simply disappears).
_REVISION_UNWRAP = frozenset(f"{{{_W}}}{tag}" for tag in ("ins", "moveTo"))
_WORD_XML_PREFIX = "/word/"


def _accept_in_element(root: Any) -> bool:
    """Accept the tracked changes under ``root`` in place; True if anything changed."""
    hits = [el for el in root.iter(*_REVISION_REMOVE, *_REVISION_UNWRAP)]
    changed = False
    # Removals first: a w:del inside a w:ins goes with its content.
    for el in hits:
        if el.tag in _REVISION_REMOVE and el.getparent() is not None:
            el.getparent().remove(el)
            changed = True
    for el in reversed(hits):  # innermost first, so nested wrappers unwrap cleanly
        if el.tag not in _REVISION_UNWRAP:
            continue
        parent = el.getparent()
        if parent is None:
            continue
        index = parent.index(el)
        for child in reversed(list(el)):
            parent.insert(index, child)
        parent.remove(el)
        changed = True
    return changed


def accept_tracked_changes(package: Any) -> None:
    """Accept every tracked change in every Word XML part of ``package``.

    Parts python-docx models (document, headers, footers, ...) are edited
    through their element tree; other ``/word/*.xml`` parts (footnotes,
    endnotes) are plain blob parts, re-parsed and re-serialised.
    """
    for part in list(package.iter_parts()):
        element = getattr(part, "_element", None)
        if element is not None:
            _accept_in_element(element)
            continue
        name = str(part.partname)
        if not (name.startswith(_WORD_XML_PREFIX) and name.endswith(".xml")):
            continue
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.fromstring(part.blob, parser)
        if _accept_in_element(root):
            part._blob = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )


def scrub_package(package: Any, core_props: Any) -> None:
    """Everything above, in one call for a writer about to save."""
    scrub_core_properties(core_props)
    scrub_app_properties(package)
    remove_comments(package)
    remove_thumbnail(package)
