import io
import zipfile

import pytest

from lexinform.word_formulas import MathNode, read_word_formulas

_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _document(body: str) -> bytes:
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{_WORD}" xmlns:m="{_MATH}" xmlns:mc="{_MC}">'
            f"<w:body>{body}</w:body></w:document>",
        )
    return package.getvalue()


def _formula(xml: str) -> bytes:
    return _document(f"<w:p><m:oMath>{xml}</m:oMath></w:p>")


def _run(token: str) -> str:
    return f"<m:r><m:t>{token}</m:t></m:r>"


def _token(node: MathNode) -> str | None:
    return node.children[0].text


def test_source_order_preserves_fraction_delimiter_and_scripts() -> None:
    source = _formula(
        "<m:d><m:dPr><m:begChr m:val='['/><m:endChr m:val=']'/></m:dPr><m:e>"
        f"<m:f><m:num>{_run('1')}{_run('+')}{_run('2')}</m:num><m:den>{_run('3')}</m:den></m:f>"
        f"<m:sSub><m:e>{_run('a')}</m:e><m:sub>{_run('i')}</m:sub></m:sSub>"
        f"<m:sSup><m:e>{_run('b')}</m:e><m:sup>{_run('2')}</m:sup></m:sSup>"
        f"<m:sSubSup><m:e>{_run('c')}</m:e><m:sub>{_run('j')}</m:sub>"
        f"<m:sup>{_run('3')}</m:sup></m:sSubSup>"
        "</m:e></m:d>"
    )
    scan = read_word_formulas(source)

    assert scan.coverage == "complete_body_walk"
    (formula,) = scan.formulas
    assert formula.path == (0, 0, 0)
    assert formula.paragraph_path == (0, 0)
    assert formula.structure is not None, "The ordered tags and operand slots are supported"
    (delimiter,) = formula.structure.children
    properties, operand = delimiter.children
    assert [node.attributes for node in properties.children] == [
        ((f"{{{_MATH}}}val", "["),),
        ((f"{{{_MATH}}}val", "]"),),
    ]
    fraction, subscript, superscript, combined = operand.children
    assert [node.kind for node in operand.children] == ["f", "sSub", "sSup", "sSubSup"]
    numerator, denominator = fraction.children
    assert [node.kind for node in fraction.children] == ["num", "den"]
    assert [_token(node) for node in numerator.children] == ["1", "+", "2"]
    assert [_token(node) for node in denominator.children] == ["3"]
    assert [node.kind for node in subscript.children] == ["e", "sub"]
    assert [node.kind for node in superscript.children] == ["e", "sup"]
    assert [node.kind for node in combined.children] == ["e", "sub", "sup"]


@pytest.mark.parametrize(
    "xml",
    [
        "<m:nary><m:e/></m:nary>",
        "<m:f><m:den/><m:num/></m:f>",
        "<m:oMath/>",
    ],
)
def test_unknown_shape_does_not_return_a_partial_formula(xml: str) -> None:
    (formula,) = read_word_formulas(_formula(_run("1") + xml)).formulas

    assert formula.unknown == "unsupported_structure"
    assert formula.structure is None
    assert formula.xml_sha256 is not None


def test_alternate_branches_are_not_resolved() -> None:
    source = _document(
        "<w:p><mc:AlternateContent><mc:Choice Requires='m'>"
        f"<m:oMath>{_run('1')}</m:oMath></mc:Choice><mc:Fallback>"
        f"<m:oMath>{_run('2')}</m:oMath></mc:Fallback></mc:AlternateContent></w:p>"
    )

    left, right = read_word_formulas(source).formulas
    assert left.unknown == right.unknown == "alternate_content"
    assert left.structure is right.structure is None
    assert left.path != right.path
    assert left.xml_sha256 != right.xml_sha256


def test_long_formula_walk_reports_prefix() -> None:
    source = _document("<w:p><m:oMath/></w:p>" * 65)

    scan = read_word_formulas(source)

    assert scan.coverage == "prefix"
    assert len(scan.formulas) == 64


def test_plain_word_paragraph_has_no_formulas() -> None:
    assert read_word_formulas(_document("<w:p><w:r><w:t>3+4</w:t></w:r></w:p>")).formulas == ()


def test_offline_nary_preserves_operator_and_bound_order() -> None:
    properties = "<m:naryPr><m:chr m:val='∑'/><m:limLoc m:val='undOvr'/></m:naryPr>"
    lower = f"<m:sub>{_run('i=1')}</m:sub>"
    upper = f"<m:sup>{_run('n')}</m:sup>"
    body = f"<m:e>{_run('x')}</m:e>"
    data = _formula(f"<m:nary>{properties}{lower}{upper}{body}</m:nary>")

    (legacy,) = read_word_formulas(data).formulas
    (observed,) = read_word_formulas(data, include_nary=True).formulas

    assert legacy.unknown == "unsupported_structure"
    assert observed.structure is not None, "The offline profile supports ordered n-ary nodes"
    (operator,) = observed.structure.children
    assert [child.kind for child in operator.children] == ["naryPr", "sub", "sup", "e"]
    assert operator.children[0].children[0].attributes == ((f"{{{_MATH}}}val", "∑"),)
    reordered = _formula(f"<m:nary>{properties}{upper}{lower}{body}</m:nary>")
    (unsupported,) = read_word_formulas(reordered, include_nary=True).formulas
    assert unsupported.unknown == "unsupported_structure"
