"""Keep README chips local, accessible and visually consistent."""

import re
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
SVG = "{http://www.w3.org/2000/svg}"


def test_readme_chip_standard():
    readme = (ROOT / "README.md").read_text()
    chips = re.findall(r"\[!\[([^\]]+)\]\((docs/badges/[^)]+)\)\]\(([^)]+)\)", readme)
    assert [Path(path).name for _, path, _ in chips] == [
        "ci.svg",
        "compatibility.svg",
        "license.svg",
        "distribution.svg",
    ]
    assert len(re.findall(r"\[!\[", readme)) == 4
    colors = ["#64748b", "#0369a1", "#2563eb", None]
    for (alt, path, link), color in zip(chips, colors, strict=True):
        image = ElementTree.parse(ROOT / path).getroot()
        assert image.tag == SVG + "svg"
        assert image.attrib["height"] == "20"
        assert image.attrib["role"] == "img"
        assert image.attrib["aria-label"] == alt
        assert image.findtext(SVG + "title") == alt
        clip = image.find(SVG + "clipPath/" + SVG + "rect")
        assert clip is not None and clip.attrib["rx"] == "3"
        rects = image.findall(SVG + "g/" + SVG + "rect")
        assert rects[0].attrib["fill"] == "#555"
        if color:
            assert rects[1].attrib["fill"] == color
        else:
            assert rects[1].attrib["fill"] in {"#047857", "#b45309"}
        text = image.findall(SVG + "g/" + SVG + "text")
        assert len(text) == 2
        assert ": ".join(node.text or "" for node in text) == alt
        assert all(node.attrib["y"] == "14" for node in text)
        assert not any(
            node.tag.removeprefix(SVG) in {"script", "image", "filter", "linearGradient"}
            or any(key.startswith("on") or "href" in key for key in node.attrib)
            for node in image.iter()
        )
        if link.startswith("#"):
            headings = re.findall(r"^#+ (.+)$", readme, re.MULTILINE)
            assert link[1:] in [heading.lower().replace(" ", "-") for heading in headings]
        elif not link.startswith("https://"):
            assert (ROOT / link).is_file()
    assert chips[0][0] == "CI: workflow"
    assert "/actions/workflows/" in chips[0][2]
    assert chips[2][0].startswith("License: ")
    assert chips[3][0].startswith("Distribution: ")
