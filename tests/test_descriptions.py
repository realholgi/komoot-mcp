"""Description policy at the GPX preparation interface, without live models."""

import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
from mcp.types import TextContent

from komoot_mcp.descriptions import prepare_gpx_descriptions
from komoot_mcp.gpx_details import GPX, comment_datetime, extract_poi_details, tour_source_links
from komoot_mcp.text_limits import utf16_units


def gpx(*descriptions):
    root = ET.Element(f"{{{GPX}}}gpx", version="1.1", creator="test")
    for index, text in enumerate(descriptions):
        point = ET.SubElement(root, f"{{{GPX}}}wpt", lat=str(49 + index / 100), lon="8.4")
        ET.SubElement(point, f"{{{GPX}}}name").text = f"POI {index}"
        ET.SubElement(point, f"{{{GPX}}}desc").text = text
    track = ET.SubElement(root, f"{{{GPX}}}trk")
    segment = ET.SubElement(track, f"{{{GPX}}}trkseg")
    point = ET.SubElement(segment, f"{{{GPX}}}trkpt", lat="49", lon="8.4")
    ET.SubElement(point, f"{{{GPX}}}ele").text = "120.5"
    return ET.tostring(root, encoding="unicode")


class Sampler:
    def __init__(self, responses, supported=True):
        self.responses = iter(responses)
        self.supported = supported
        self.calls = []

    def check_client_capability(self, capability):
        return self.supported

    async def create_message(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        text = response if isinstance(response, str) else json.dumps(response)
        return SimpleNamespace(content=TextContent(type="text", text=text))


def result(index, languages, language, text):
    return {"id": index, "source_languages": languages, "language": language, "text": text}


def descriptions(xml):
    root = ET.fromstring(xml)
    return [point.findtext(f"{{{GPX}}}desc", default="") for point in root.findall(f"{{{GPX}}}wpt")]


@pytest.mark.asyncio
async def test_summarizes_long_text_and_translates_french_without_changing_names_or_geometry():
    english = "A walker reported water available on 2026-11-10, but it was not tested. " * 8
    french = "Source fermée depuis le 12 novembre 2026. Ne pas boire."
    source = gpx(english, french)
    sampler = Sampler([[result(0, ["en"], "en", "Water reported 10 Nov 2026; potability untested."),
                        result(1, ["fr"], "de", "Quelle seit 12.11.2026 geschlossen. Nicht trinken.")]])
    prepared = await prepare_gpx_descriptions(source, sampler)
    assert descriptions(prepared["gpx_content"]) == [
        "Water reported 10 Nov 2026; potability untested.",
        "Quelle seit 12.11.2026 geschlossen. Nicht trinken.",
    ]
    root = ET.fromstring(prepared["gpx_content"])
    assert [point.findtext(f"{{{GPX}}}name") for point in root.findall(f"{{{GPX}}}wpt")] == ["POI 0", "POI 1"]
    assert [point.attrib for point in root.findall(f"{{{GPX}}}wpt")] == [
        {"lat": "49.0", "lon": "8.4"}, {"lat": "49.01", "lon": "8.4"},
    ]
    track = root.find(f".//{{{GPX}}}trkpt")
    assert track.attrib == {"lat": "49", "lon": "8.4"}
    assert track.findtext(f"{{{GPX}}}ele") == "120.5"
    assert descriptions(source) == [english, french]
    assert sampler.calls[0]["include_context"] == "none"
    assert sampler.calls[0].get("tools") is None


@pytest.mark.asyncio
async def test_short_german_and_english_remain_verbatim_even_if_model_paraphrases():
    source = gpx("  Wasser aktuell verfügbar.\n", "Water not tested.")
    sampler = Sampler([[result(0, ["de"], "de", "Geändert"), result(1, ["en"], "en", "Changed")]])
    prepared = await prepare_gpx_descriptions(source, sampler)
    assert prepared["gpx_content"] == source
    assert prepared["changed_descriptions"] == []


@pytest.mark.asyncio
async def test_short_other_language_is_translated_without_waiting_for_length_limit():
    source = gpx("Prohibido beber.")
    sampler = Sampler([[result(0, ["es"], "de", "Trinken verboten.")]])
    prepared = await prepare_gpx_descriptions(source, sampler)
    assert descriptions(prepared["gpx_content"]) == ["Trinken verboten."]
    assert prepared["changed_descriptions"][0]["language"] == "de"


@pytest.mark.asyncio
@pytest.mark.parametrize("languages,target", [(["fr"], "en"), (["en"], "de"), (["de"], "en")])
async def test_wrong_target_language_is_rejected(languages, target):
    sampler = Sampler([[result(0, languages, target, "Wrong target.")]])
    with pytest.raises(ValueError, match="wrong target language"):
        await prepare_gpx_descriptions(gpx("Long report. " * 40), sampler)


@pytest.mark.asyncio
async def test_unicode_storage_limit_is_enforced_without_cutting_model_output():
    sampler = Sampler([[result(0, ["en"], "en", "😀" * 126)]])
    with pytest.raises(ValueError, match="was not truncated"):
        await prepare_gpx_descriptions(gpx("Long report. " * 40), sampler)
    assert utf16_units("😀" * 125) == 250


@pytest.mark.asyncio
@pytest.mark.parametrize("records", [
    [result(0, ["fr"], "de", "Quelle."), result(0, ["fr"], "de", "Quelle.")],
    [], [result(9, ["fr"], "de", "Quelle.")],
])
async def test_missing_duplicate_or_foreign_record_ids_are_rejected(records):
    with pytest.raises(ValueError, match="POI ID"):
        await prepare_gpx_descriptions(gpx("Source fermée."), Sampler([records]))


@pytest.mark.asyncio
async def test_missing_sampling_is_an_explicit_error_not_truncation():
    sampler = Sampler([], supported=False)
    with pytest.raises(ValueError, match="sampling enabled"):
        await prepare_gpx_descriptions(gpx("Source fermée."), sampler)
    assert sampler.calls == []


@pytest.mark.asyncio
async def test_empty_descriptions_need_no_model():
    source = gpx("")
    prepared = await prepare_gpx_descriptions(source, None)
    assert prepared["gpx_content"] == source




def enriched_gpx(description):
    root = ET.fromstring(gpx(description))
    point = root.find(f"{{{GPX}}}wpt")
    ET.SubElement(point, f"{{{GPX}}}type").text = "Refuge non Gardé"
    ET.SubElement(point, f"{{{GPX}}}link", href="https://example.com/hut")
    return ET.tostring(root, encoding="unicode")


@pytest.mark.asyncio
async def test_enrichment_sorts_authentic_observations_and_preserves_raw_details():
    source_text = (
        "Abri avec table.\n\nUser comments (newest first; retrieved 2026-11-15):\n\n"
        "2025-03-01T10:00:00Z | Older author | 2/5\nNo water.\n\n"
        "2026-11-10T10:00:00Z | Newer author | 4/5\nWater back, not tested."
    )
    source = enriched_gpx(source_text)
    response = result(0, ["fr", "en"], "de", "Tisch vorhanden.")
    response["observations"] = [
        {"comment_id": 1, "text": "Kein Wasser."},
        {"comment_id": 0, "text": "Wasser wieder gemeldet, ungeprüft."},
    ]
    sampler = Sampler([[response]])
    prepared = await prepare_gpx_descriptions(source, sampler)
    text = descriptions(prepared["gpx_content"])[0]
    assert text.startswith("Unbewirtschaftete Hütte\n2026-11-10:")
    assert text.index("2026-11-10") < text.index("2025-03-01")
    assert utf16_units(text) <= 250
    point = prepared["details"]["pois"][0]
    assert point["description"] == source_text
    assert [c["author"] for c in point["comments"]] == ["Newer author", "Older author"]
    assert [c["text"] for c in point["comments"]] == ["Water back, not tested.", "No water."]
    assert point["links"] == ["https://example.com/hut"]
    payload = sampler.calls[0]["messages"][0].content.text
    assert "Older author" not in payload and "https://example.com/hut" not in payload
    assert prepared["tour_description"].endswith("https://example.com/hut")


@pytest.mark.asyncio
@pytest.mark.parametrize("observations", [
    [{"comment_id": 20, "text": "Invented."}],
    [{"comment_id": 0, "text": "A."}, {"comment_id": 0, "text": "B."}],
])
async def test_enrichment_rejects_unknown_or_duplicate_comment_sources(observations):
    source = enriched_gpx("User comments (newest first; retrieved today):\n"
                          "2026-11-10T10:00:00Z | Author | 4/5\nReported dry.")
    response = result(0, ["en"], "en", "")
    response["observations"] = observations
    with pytest.raises(ValueError, match="source comment ID"):
        await prepare_gpx_descriptions(source, Sampler([[response]]))


@pytest.mark.asyncio
async def test_type_and_dates_count_towards_the_hard_storage_limit():
    source = enriched_gpx("User comments (newest first; retrieved today):\n"
                          "2026-11-10T10:00:00Z | Author | 4/5\nReported dry.")
    response = result(0, ["en"], "en", "")
    response["observations"] = [{"comment_id": 0, "text": "x" * 235}]
    with pytest.raises(ValueError, match="exceeds 250"):
        await prepare_gpx_descriptions(source, Sampler([[response]]))



def test_full_details_sort_timezone_aware_dates_and_preserve_raw_content():
    source = enriched_gpx(
        "User comments (newest first; retrieved today):\n"
        "2026-11-15T00:30:00Z | Early | 1/5\n<img src=x onerror=alert(1)>\n\n"
        "2026-11-14T23:30:00-02:00 | Late | 5/5\n<script>alert(2)</script>"
    )
    root = ET.fromstring(source)
    ET.SubElement(root.find(f"{{{GPX}}}wpt"), f"{{{GPX}}}link", href="javascript:alert(3)")
    records = extract_poi_details(root)
    assert [comment["author"] for comment in records[0]["comments"]] == ["Late", "Early"]
    assert records[0]["comments"][0]["text"] == "<script>alert(2)</script>"
    assert records[0]["comments"][1]["text"] == "<img src=x onerror=alert(1)>"
    links = tour_source_links({"pois": records})
    assert "https://example.com/hut" in links
    assert "javascript:" not in links


def test_comment_dates_require_timezone():
    with pytest.raises(ValueError, match="timezone"):
        comment_datetime("2026-11-15T00:30:00")
