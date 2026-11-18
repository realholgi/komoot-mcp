"""Read source-format POI metadata for Komoot description preparation."""

from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from komoot_mcp.poi_types import get_poi_type

GPX = "http://www.topografix.com/GPX/1/1"
_COMMENT_SECTION = re.compile(r"(?m)^User comments \([^\r\n]*\):[ \t]*\r?\n")
_COMMENT_HEADER = re.compile(
    r"(?m)^(?P<created_at>\d{4}-\d{2}-\d{2}T[^\s|]+)[ \t]*\|[ \t]*"
    r"(?P<author>[^\r\n]*?)[ \t]*\|[ \t]*(?P<rating>[^\r\n]*)\r?$"
)


def comment_datetime(value: str) -> datetime:
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("Comment dates must include a timezone")
    return date.astimezone(timezone.utc)


def extract_poi_details(root) -> list[dict]:
    records = []
    for index, point in enumerate(root.findall(f"{{{GPX}}}wpt")):
        description = point.findtext(f"{{{GPX}}}desc", default="")
        marker = _COMMENT_SECTION.search(description)
        comments = []
        intro = description
        if marker:
            body = description[marker.end():]
            headers = list(_COMMENT_HEADER.finditer(body))
            if headers:
                intro = description[:marker.start()].rstrip("\r\n")
                for position, header in enumerate(headers):
                    end = headers[position + 1].start() if position + 1 < len(headers) else len(body)
                    comments.append({
                        "created_at": header["created_at"], "author": header["author"].rstrip(),
                        "rating": header["rating"].rstrip(),
                        "text": body[header.end():end].strip("\r\n"),
                    })
                comments.sort(key=lambda item: comment_datetime(item["created_at"]), reverse=True)
        source_type = point.findtext(f"{{{GPX}}}type", default="")
        kind = get_poi_type(source_type)
        records.append({
            "id": index, "name": point.findtext(f"{{{GPX}}}name", default=""),
            "latitude": point.get("lat"), "longitude": point.get("lon"),
            "source_type": source_type, "type_de": kind.de if kind else source_type,
            "type_en": kind.en if kind else source_type,
            "description": description, "intro": intro, "comments": comments,
            "links": [link.get("href", "") for link in point.findall(f"{{{GPX}}}link")],
        })
    return records


def tour_source_links(details: dict) -> str:
    lines = ["GPX: Quellen und Originaltypen", "",
             "Die POI-Kurztexte enthalten Originaltypen und datierte Nutzerberichte. "
             "Vollständige Angaben bleiben im Original-GPX und den zurückgegebenen Quelldaten. "
             "Wasser- und Unterkunftssymbole garantieren weder Trinkbarkeit noch Übernachtungserlaubnis.",
             "", "Weiterführende Quellen:"]
    for point in details["pois"]:
        for url in point["links"]:
            try:
                parsed = urlsplit(url)
                safe = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password
            except ValueError:
                safe = False
            if safe:
                lines.append(f'{point["name"]} ({point["type_de"]}): {url}')
    return '\n'.join(lines)
