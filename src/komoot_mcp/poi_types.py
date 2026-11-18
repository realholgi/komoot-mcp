"""Source POI types, their labels, and current Komoot categories."""

from typing import NamedTuple


class PoiType(NamedTuple):
    category: int
    de: str
    en: str


_TYPES = {
    "fontaine": PoiType(3, "Brunnen", "Fountain"),
    "source": PoiType(3, "Quelle", "Spring"),
    "cours d'eau": PoiType(3, "Wasserlauf", "Watercourse"),
    "restaurant": PoiType(1001, "Restaurant", "Restaurant"),
    "epicerie / artisan": PoiType(48, "Lebensmittel / Handwerk", "Groceries / artisan"),
    "supermarché": PoiType(48, "Supermarkt", "Supermarket"),
    "toilettes": PoiType(39, "Toilette", "Toilet"),
    "point de vue": PoiType(1006, "Aussichtspunkt", "Viewpoint"),
    "refuge non gardé": PoiType(1010, "Unbewirtschaftete Hütte", "Unstaffed hut"),
    "refuge gardé": PoiType(1010, "Bewirtschaftete Hütte", "Staffed hut"),
    "chambre d'hotes": PoiType(1010, "Gästezimmer", "Guest accommodation"),
    "information": PoiType(53, "Information", "Information"),
    "camping": PoiType(27, "Campingplatz", "Campsite"),
    "zone de bivouac": PoiType(27, "Biwakzone", "Bivouac area"),
    "monument": PoiType(1008, "Denkmal", "Monument"),
    "village de france": PoiType(1008, "Ortschaft", "Village"),
    "immanquable": PoiType(1008, "Besonderes Ziel", "Must-see"),
}


def get_poi_type(value: str | None) -> PoiType | None:
    return _TYPES.get((value or "").strip().casefold().replace("’", "'"))
