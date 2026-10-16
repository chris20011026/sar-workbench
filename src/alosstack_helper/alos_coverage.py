"""Read-only ALOS CEOS coverage evidence and a separate human review record.

``read_product(LED_path)`` runs in the selected ISCE2 science worker. It uses
the installed upstream CEOS schemas/decoder, with documented PALSAR-3 offsets
where its layout differs. Only metadata records and nine image line prefixes
are read; image pixels, source files and the environment are never modified.

Footprints are GeoJSON longitude/latitude metadata outlines, not valid-data
masks or a declaration of interferometric compatibility. Bounds consistently
use [west, south, east, north]; ``bounds_parts`` preserves antimeridian pieces.
``inspect_coverage`` needs Shapely/PyProj, but review functions and module import
use only the standard library. A review can never unlock mixed-sensor InSAR.

Field definitions: JAXA PALSAR-2 CEOS Rev G tables 3.3-5/6/14 and PALSAR-3
CEOS Rev A tables 4.6-5/6/15. In these specific layouts ASCEND/DESCEND in the
along-line field are explicitly defined as orbital ascending/descending; a
generic time-order flag, file suffix, or the reserved bytes 101-116 is not.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET


SCHEMA_VERSION = 1
SOURCES = {
    "PALSAR-2": "https://www.eorc.jaxa.jp/ALOS/en/alos-2/pdf/product_format_description/PALSAR-2_xx_Format_CEOS_E_g.pdf",
    "PALSAR-3": "https://www.eorc.jaxa.jp/ALOS/en/alos-4/pdf/FTR-240031A_ALOS-4_PALSAR-3_StandardProduct_Format_CEOS_En.pdf",
}
LIMITATION = "僅為地理中繼資料的概略覆蓋；重疊不代表可干涉、有效相干範圍或跨衛星對位已通過。"
MAX_PRODUCTS = 256
MAX_SELECTION = 64
_BINARY_SIZE = {3: 1, 4: 4, 6: 2, 8: 4, 9: 8}


def _json(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _stamp(path):
    s = Path(path).lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError("來源必須是一般檔案，不能是符號連結：" + Path(path).name)
    return dict(size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns,
                device=s.st_dev, inode=s.st_ino)


class _Source:
    """Bounded reads plus a fingerprint of exactly the metadata that was read."""
    def __init__(self, path):
        self.path = Path(path)
        self.before = _stamp(self.path)
        self.hash = hashlib.sha256()
        self.bytes_read = 0
        self.stream = None

    def __enter__(self):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags)
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or (s.st_dev, s.st_ino) != (self.before["device"], self.before["inode"]):
            os.close(fd)
            raise ValueError("來源在開啟前改變，請重新掃描")
        self.stream = os.fdopen(fd, "rb")
        return self

    def read(self, offset, length):
        if offset < 0 or length < 0 or length > 1024 * 1024 or offset + length > self.before["size"]:
            raise ValueError("CEOS 描述或影像前綴不完整／長度異常")
        self.stream.seek(offset)
        raw = self.stream.read(length)
        if len(raw) != length:
            raise ValueError("CEOS 來源讀取不完整")
        self.hash.update(offset.to_bytes(8, "big") + length.to_bytes(8, "big") + raw)
        self.bytes_read += length
        return raw

    def record(self, offset, max_length=65536):
        head = self.read(offset, 12)
        size = int.from_bytes(head[8:12], "big")
        if not 12 <= size <= max_length:
            raise ValueError("CEOS 描述記錄長度異常")
        return head + self.read(offset + 12, size - 12)

    def evidence(self):
        return dict(path=str(self.path), name=self.path.name, **self.before,
                    metadata_sha256=self.hash.hexdigest(), bytes_read=self.bytes_read,
                    hash_scope="sampled_metadata_only")

    def verify(self):
        if _stamp(self.path) != self.before:
            raise ValueError("來源在讀取過程中改變，請重新掃描")

    def __exit__(self, *exc):
        self.stream.close()
        self.verify()


@lru_cache(maxsize=8)
def _fields(schema):
    import isce  # noqa: F401; initializes the upstream isceobj namespace.
    from isceobj.Sensor import xmlPrefix
    fields, offset = {}, 0
    # Schemas come only from the installed reader, never from selected data.
    for node in ET.parse(Path(xmlPrefix) / "alos2_slc" / schema).getroot():
        if node.tag != "rec":
            break  # No repeated/unbounded structures are needed here.
        kind, count = int(node.get("type")), int(node.get("num"))
        length = _BINARY_SIZE.get(kind, count)
        if kind:
            fields[node.get("name")] = (offset, length, kind)
        offset += length
    return fields


def _field(raw, spec, name):
    """Use upstream scalar decoding after rejecting its invalid-to-zero cases."""
    from isceobj.Sensor import CEOS
    offset, length, kind = spec
    data = raw[offset:offset + length]
    if len(data) != length:
        raise ValueError("欄位不完整：" + name)
    if kind in (1, 2, 5):
        try:
            value = data.decode("ascii").strip().rstrip("\0")
        except UnicodeDecodeError as exc:
            raise ValueError("欄位編碼不合法：" + name) from exc
        if not value:
            return None
        if kind == 2 and not re.fullmatch(r"[+-]?\d+", value):
            raise ValueError("整數欄位不合法：" + name)
        if kind == 5:
            if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?", value):
                raise ValueError("數值欄位不合法：" + name)
            if not math.isfinite(float(value.replace("D", "E").replace("d", "e"))):
                raise ValueError("數值欄位不是有限數：" + name)
        if kind == 1 and any(ord(c) < 32 for c in value):
            raise ValueError("文字欄位含控制字元：" + name)
    stream = io.BytesIO(raw)
    stream.seek(offset)
    parser = CEOS.CEOSDB(dataFile=stream)
    # CEOS.readData recognizes uppercase D exponents; normalize lowercase d.
    if kind == 5 and b"d" in data:
        stream = io.BytesIO(data.replace(b"d", b"D"))
        parser = CEOS.CEOSDB(dataFile=stream)
    value = parser.readData(name, length, kind)
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError("無法解碼欄位：" + name)
    return value


def _decode(raw, schema, names):
    fields = _fields(schema)
    return {name: _field(raw, fields[name], name) for name in names}


def _signed_microdegrees(value):
    if not isinstance(value, int) or not 0 <= value < 2**32:
        raise ValueError("影像座標編碼不合法")
    return (value - 2**32 if value >= 2**31 else value) / 1e6


def _point(value, reject_zero=False):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("座標需為經緯度一組")
    lon, lat = value
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in value):
        raise ValueError("座標缺漏或不是有限數")
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("經緯度超出合法範圍")
    if reject_zero and (lon == 0 or lat == 0):
        raise ValueError("座標為零，無法排除缺值；未以零值猜測範圍")
    return [float(lon), float(lat)]


def _polygons(geometry):
    if geometry.geom_type == "Polygon":
        return [geometry] if not geometry.is_empty else []
    if geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [p for child in geometry.geoms for p in _polygons(child)]
    return []


def _geometry_json(geometry):
    from shapely.geometry import mapping
    return _json(mapping(geometry))


def _geometry_info(geometry):
    from pyproj import Geod
    from shapely.geometry.polygon import orient
    parts = _polygons(geometry)
    area = sum(abs(Geod(ellps="WGS84").geometry_area_perimeter(orient(p, sign=1.0))[0]) for p in parts) / 1e6
    return dict(footprint=_geometry_json(geometry), bounds=list(geometry.bounds),
                bounds_parts=[list(p.bounds) for p in parts], area_km2=area,
                crosses_dateline=any(p.bounds[0] <= -180 for p in parts) and any(p.bounds[2] >= 180 for p in parts))


def outline_from_points(points):
    """Validate a perimeter, splitting dateline crossings without world wrap.

    Conservative zero handling is intentional: the CEOS formats also use zero
    as missing/fixed data. A genuine equator/Greenwich corner needs independent
    geolocation evidence; this reader will not guess which meaning was meant.
    """
    from shapely.affinity import translate
    from shapely.geometry import MultiPolygon, Polygon, box
    if not isinstance(points, (list, tuple)) or not 4 <= len(points) <= 4096:
        raise ValueError("影格邊界點數不足或過多")
    points = [_point(p, reject_zero=True) for p in points]
    if points[-1] == points[0]:
        points.pop()
    if len(set(map(tuple, points))) < 4:
        raise ValueError("影格缺少四個不同的邊界點")
    unwrapped = [points[0]]
    for lon, lat in points[1:]:
        previous = unwrapped[-1][0]
        lon += 360 * round((previous - lon) / 360)
        if abs(lon - previous) >= 180:
            raise ValueError("經度跳躍無法唯一判讀")
        unwrapped.append([lon, lat])
    xs = [p[0] for p in unwrapped]
    if max(xs) - min(xs) >= 180 or abs(xs[-1] - xs[0]) >= 180:
        raise ValueError("邊界跨度不合理，未猜測跨換日線範圍")
    polygon = Polygon(unwrapped)
    if not polygon.is_valid or polygon.area <= 1e-12:
        raise ValueError("邊界退化、自我交叉或無效，未自動修補座標")
    pieces = []
    first, last = math.floor((min(xs) + 180) / 360), math.floor((max(xs) + 180) / 360)
    for band in range(first, last + 1):
        clipped = polygon.intersection(box(-180 + 360 * band, -90, 180 + 360 * band, 90))
        pieces.extend(translate(p, xoff=-360 * band) for p in _polygons(clipped) if p.area > 1e-12)
    result = pieces[0] if len(pieces) == 1 else MultiPolygon(pieces)
    if result.is_empty or not result.is_valid:
        raise ValueError("無法建立有效的地理邊界")
    return _geometry_info(result)


def _scene(raw, expected_sensor, warnings):
    fields = _fields("scene_record.xml")
    values = {}
    names = ["Sensor platform mission identifier", "Sensor ID and mode of operation for this channel",
             "Orbit number", "Scene centre time", "Product level code", "Sensor clock angle",
             "Range sampling rate in MHz", "Ellipsoid designator", "scene centre geodetic latitude",
             "scene centre geodetic longitude"]
    for name in names:
        try:
            values[name] = _field(raw, fields[name], name)
        except ValueError as exc:
            warnings.append(str(exc))
            values[name] = None
    # Both official formats use 32 scene-ID bytes; the upstream P2 schema
    # splits them between two historically named fields.
    scene_id = _field(raw, (20, 32, 1), "Scene identifier")
    mission = str(values["Sensor platform mission identifier"] or "").strip()
    observed = {"ALOS2": "ALOS-2", "ALOS4": "ALOS-4"}.get(mission)
    if observed != expected_sensor or not str(scene_id or "").startswith(mission):
        raise ValueError("LED 任務／場景編號與檔名不一致，不能推定資料格式")
    layout = "PALSAR-3" if observed == "ALOS-4" else "PALSAR-2"
    if len(raw) != (4680 if layout == "PALSAR-3" else 4096):
        raise ValueError("資料集描述長度與已核對的 CEOS 版本不同")
    direction_start = 1822 if layout == "PALSAR-3" else 1534
    indicator = _field(raw, (direction_start, 8, 1), "Along-line direction indicator")
    direction = {"ASCEND": "ascending", "DESCEND": "descending"}.get(indicator)
    clock = values["Sensor clock angle"]
    look = {90.0: "right", -90.0: "left"}.get(clock)
    if not direction:
        warnings.append("未取得 JAXA 明確定義的 ASCEND／DESCEND；未由時間順序或檔名推測升降軌。")
    if not look:
        warnings.append("觀測左右向未確認，不能由升降軌代替。")
    timestamp = str(values["Scene centre time"] or "")
    date, acquisition = None, None
    try:
        if not re.fullmatch(r"\d{14}(?:\d{1,9})?", timestamp):
            raise ValueError()
        parsed = dt.datetime.strptime(timestamp[:14], "%Y%m%d%H%M%S")
        date, acquisition = parsed.strftime("%Y%m%d"), parsed.isoformat() + "Z"
    except ValueError:
        warnings.append("場景觀測日期不合法，未以資料夾或檔名代替。")
    center = None
    try:
        center = _point([values["scene centre geodetic longitude"], values["scene centre geodetic latitude"]], reject_zero=True)
    except ValueError:
        pass
    frame = None
    match = re.fullmatch(r"ALOS2\d+(\d{4})-\d{6}", str(scene_id or ""))
    if match:
        frame = match.group(1)
    rate = values["Range sampling rate in MHz"]
    if rate is not None and rate <= 0:
        warnings.append("Range sampling rate 不是正值，視為未知。")
        rate = None
    return dict(scene_id=scene_id, acquisition=acquisition, date=date, layout=layout,
                sensor=observed, source_sensor=observed, level=values["Product level code"],
                sensor_mode=values["Sensor ID and mode of operation for this channel"],
                orbit=values["Orbit number"], frame=frame, frame_provenance="scene_id_label_only" if frame else "unknown",
                orbit_direction=direction, direction=direction, look_direction=look, sensor_clock_angle_degrees=clock,
                along_line_indicator=indicator, range_sampling_mhz=rate, center=center,
                ellipsoid=values["Ellipsoid designator"], converted=False,
                field_sources=dict(orbit_direction=dict(record="dataset_summary", bytes=[direction_start + 1, direction_start + 8],
                                                       definition="JAXA ASCEND/DESCEND mapping", source=SOURCES[layout]),
                                   look_direction=dict(record="dataset_summary", bytes=[477, 484], source=SOURCES[layout]),
                                   range_sampling_mhz=dict(record="dataset_summary", bytes=[711, 726], source=SOURCES[layout])))


def _map_outline(raw):
    names = [f"{corner} geodetic {axis}"
             for corner in ("1st line 1st pixel", "1st line last valid pixel", "Last line last valid pixel", "Last line 1st pixel")
             for axis in ("latitude", "longitude")]
    values = _decode(raw, "map_proj_record.xml", names)
    return outline_from_points([[values[names[i + 1]], values[names[i]]] for i in range(0, len(names), 2)])


def _image_paths(leader):
    suffix = re.escape(leader.name[4:])
    expression = re.compile(r"^IMG-(HH|HV|VH|VV)(?:-[FR])?-" + suffix + r"(?:-[FB][1-9]\d?)?$")
    images = []
    for number, item in enumerate(leader.parent.iterdir()):
        if number >= 20000:
            raise ValueError("產品資料夾檔案過多，請選較小的資料夾")
        if expression.fullmatch(item.name):
            images.append(item)
            if len(images) > 32:
                raise ValueError("同產品 IMG 數量超過可核對的範圍")
    return sorted(images)


def _image(source, metadata):
    header = source.record(0)
    desc = _decode(header, "image_file.xml", ["Number of SAR DATA records", "SAR DATA record length",
                                            "Number of pixels per line per SAR channel"])
    count, stride, width = (desc[n] for n in ["Number of SAR DATA records", "SAR DATA record length", "Number of pixels per line per SAR channel"])
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (count, stride, width)) or not 2 <= count <= 10000000 or width <= 0 or stride < 216:
        raise ValueError("影像尺寸或記錄長度不合法")
    if len(header) + count * stride != source.before["size"]:
        raise ValueError("影像大小與 CEOS 記錄不符，可能未複製完整")
    # The geographic fields of processed L1.5/L2.1 records have a different
    # layout. They must use the leader map-projection record, never this schema.
    if metadata["level"] != "1.1":
        return dict(name=source.path.name, width=width, lines=count, footprint=None,
                    polarization=None, dates=[], warning="此處只解讀 Level 1.1 訊號記錄座標；其他級別需可靠 map projection corners。")
    names = ["Record Length", "Sensor acquisition year", "Sensor acquisition day of year",
             "Transmitted polarization", "Received polarization"]
    names += [axis + " of " + side + " pixel" for axis in ("Latitude", "Longitude") for side in ("first", "last")]
    left, right, pols, dates = [], [], set(), set()
    indices = sorted({round(i * (count - 1) / 8) for i in range(9)})
    for index in indices:
        raw = source.read(len(header) + index * stride, 216)
        line = _decode(raw, "image_record.xml", names)
        if line["Record Length"] != stride:
            raise ValueError("影像邊界記錄長度與描述檔不同")
        tx, rx = line["Transmitted polarization"], line["Received polarization"]
        if tx not in (0, 1) or rx not in (0, 1):
            raise ValueError("影像極化欄位不合法")
        pols.add("HV"[tx] + "HV"[rx])
        year, day = line["Sensor acquisition year"], line["Sensor acquisition day of year"]
        if not 2000 <= year <= 2100 or not 1 <= day <= 366:
            raise ValueError("影像觀測日期不合法")
        date = dt.date(year, 1, 1) + dt.timedelta(days=day - 1)
        if date.year != year:
            raise ValueError("影像年內日期不合法")
        dates.add(date.strftime("%Y%m%d"))
        for target, side in ((left, "first"), (right, "last")):
            target.append([_signed_microdegrees(line["Longitude of " + side + " pixel"]),
                           _signed_microdegrees(line["Latitude of " + side + " pixel"])])
    expected_pol = source.path.name[4:6]
    if pols != {expected_pol}:
        raise ValueError("影像極化與檔名或各行記錄不同")
    if metadata["date"] and not min(dates) <= metadata["date"] <= max(dates):
        raise ValueError("影像觀測日期與 LED 不同")
    result = dict(name=source.path.name, width=width, lines=count, polarization=expected_pol,
                  dates=sorted(dates), sampled_lines=indices, footprint=None)
    try:
        result.update(outline_from_points(left + right[::-1]))
    except ValueError as exc:
        result["warning"] = str(exc)
    return result


def read_product(path):
    """Return a JSON-safe product dossier; partial metadata stays explicitly unknown.

    ``source_fingerprint`` covers source identity/stat and the sampled metadata,
    not all image pixels. The caller should retain its full manifest for any
    conversion or processing action, and rescan before storing a review.
    """
    leader = Path(path).expanduser().absolute()
    if leader.is_symlink() or not leader.name.startswith(("LED-ALOS2", "LED-ALOS4")):
        raise ValueError("請選 ALOS-2／ALOS-4 的一般 LED 檔案")
    leader = leader.resolve(strict=True)
    _stamp(leader)
    expected = "ALOS-4" if leader.name.startswith("LED-ALOS4") else "ALOS-2"
    result = dict(schema_version=SCHEMA_VERSION, id="alos-" + hashlib.sha256(str(leader).encode()).hexdigest()[:24],
                  sensor=expected, type="CEOS", path=str(leader), date=None, polarizations=[], metadata={},
                  images=[], footprint=None, bounds=None, bounds_parts=[], footprint_status="unknown",
                  footprint_source=None, warnings=[], errors=[], source_files=[], source_fingerprint=None,
                  processing_unlocked=False, limitation=LIMITATION)
    tracked = []
    try:
        with _Source(leader) as source:
            tracked.append(source)
            fdr = source.record(0)
            if len(fdr) != 720:
                raise ValueError("LED descriptor 長度不是已核對的 720 bytes")
            descriptor = _decode(fdr, "leader_file.xml", ["Number of data set summary records", "Data set summary record length",
                                                        "Number of map projection data records", "Map projection record length"])
            if descriptor["Number of data set summary records"] != 1:
                raise ValueError("需要且僅能解讀一筆資料集描述記錄")
            raw = source.record(len(fdr))
            if len(raw) != descriptor["Data set summary record length"]:
                raise ValueError("資料集描述長度與 LED descriptor 不同")
            metadata = _scene(raw, expected, result["warnings"])
            if not leader.name[4:].startswith(str(metadata["scene_id"]) + "-"):
                raise ValueError("LED 場景編號與檔名不同，請核對產品來源")
            result["metadata"], result["date"] = metadata, metadata["date"]
            # An adjacent manifest describes provenance, never changes the
            # binary layout chosen from the actual leader's mission field.
            provenance = leader.parent / "conversion.json"
            if provenance.exists() or provenance.is_symlink():
                try:
                    with _Source(provenance) as converted:
                        tracked.append(converted)
                        if converted.before["size"] > 1024 * 1024:
                            raise ValueError("轉換紀錄過大")
                        document = json.loads(converted.read(0, converted.before["size"]))
                    if not isinstance(document, dict):
                        raise ValueError("轉換紀錄格式不合法")
                    if document.get("source_sensor") == "ALOS-4" and document.get("output_format") == "PALSAR-2 CEOS compatibility":
                        result["sensor"] = "ALOS-4"
                        metadata.update(source_sensor="ALOS-4", converted=True, frame_provenance="synthetic_compatibility_id")
                        result["warnings"].append("ALOS-4 相容格式產品；ALOS2 影格編號僅為相容標籤，不代表跨衛星地理對應。")
                except (ValueError, OSError, UnicodeError) as exc:
                    result["warnings"].append("轉換來源紀錄未確認：" + str(exc))
                    metadata["provenance_status"] = "unknown"
            maps = descriptor["Number of map projection data records"]
            if maps not in (0, 1):
                raise ValueError("map projection 記錄數不合法")
            map_geometry = None
            if maps:
                raw_map = source.record(len(fdr) + len(raw))
                if len(raw_map) != descriptor["Map projection record length"]:
                    raise ValueError("map projection 記錄長度不同")
                try:
                    map_geometry = _map_outline(raw_map)
                except ValueError as exc:
                    result["warnings"].append("Map projection corners 無法使用：" + str(exc))
        paths = _image_paths(leader)
        for image in paths:
            try:
                with _Source(image) as source:
                    tracked.append(source)
                    detail = _image(source, metadata)
                result["images"].append(detail)
                if detail.get("polarization"):
                    result["polarizations"].append(detail["polarization"])
                if detail.get("warning"):
                    result["warnings"].append(image.name + "：" + detail["warning"])
            except (ValueError, OSError) as exc:
                result["errors"].append(image.name + "：" + str(exc))
        if not paths:
            result["warnings"].append("缺少同場景 IMG 檔；不能由 LED 中心點推測影格大小。")
        if map_geometry is not None:
            result.update(map_geometry)
            result["footprint_source"] = dict(record="map_projection", bytes=[1073, 1200], source=SOURCES[metadata["layout"]])
        elif paths and len(result["images"]) == len(paths) and all(i["footprint"] for i in result["images"]):
            from shapely.geometry import shape
            from shapely.ops import unary_union
            geometry = unary_union([shape(i["footprint"]) for i in result["images"]])
            result.update(_geometry_info(geometry))
            result["footprint_source"] = dict(record="signal_data_prefix", bytes=[193, 216], sampled_lines_per_image=9,
                                               source=SOURCES[metadata["layout"]], scope="all_matched_images")
        if result["footprint"] is not None:
            result["footprint_status"] = "metadata_outline"
        else:
            result["warnings"].append("沒有完整可靠的影格邊界；中心點及相同 frame 編號不能代替覆蓋範圍。")
        if paths != _image_paths(leader):
            raise ValueError("同場景 IMG 清單在讀取時改變，請重新掃描")
    except (ValueError, OSError, ImportError) as exc:
        result["errors"].append("LED／地理中繼資料未確認：" + str(exc))
        result.update(footprint=None, bounds=None, bounds_parts=[], footprint_status="unknown", footprint_source=None)
    for source in tracked:
        source.verify()  # Never return an apparently stable dossier after a race.
    result["source_files"] = [source.evidence() for source in tracked]
    result["source_fingerprint"] = _digest(result["source_files"])
    result["polarizations"] = sorted(set(result["polarizations"]))
    result["warnings"] = sorted(set(result["warnings"]))
    result["errors"] = sorted(set(result["errors"]))
    return _json(result)


def _known_geometry(product):
    from shapely.geometry import shape
    if product.get("footprint_status") != "metadata_outline" or product.get("errors") or not product.get("footprint"):
        return None
    geo = product["footprint"]
    if not isinstance(geo, dict) or geo.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    polygons = [geo.get("coordinates")] if geo["type"] == "Polygon" else geo.get("coordinates")
    try:
        if not isinstance(polygons, (list, tuple)) or not 1 <= len(polygons) <= 64:
            return None
        count = 0
        for polygon in polygons:
            if not isinstance(polygon, (list, tuple)) or not 1 <= len(polygon) <= 64:
                return None
            for ring in polygon:
                if not isinstance(ring, (list, tuple)) or len(ring) < 4 or ring[0] != ring[-1]:
                    return None
                count += len(ring)
                if count > 4096:
                    return None
                for point in ring:
                    _point(point)
                if any(abs(a[0] - b[0]) >= 180 for a, b in zip(ring, ring[1:])):
                    return None
        geometry = shape(geo)
        return geometry if geometry.is_valid and not geometry.is_empty and geometry.area > 1e-12 else None
    except (ValueError, TypeError, KeyError):
        return None


def _ids(value, available=None):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SELECTION or any(not isinstance(x, str) or not x or len(x) > 160 for x in value):
        raise ValueError("請選取 1 至 64 個產品")
    if len(set(value)) != len(value):
        raise ValueError("選取產品不能重複")
    if available is not None and not set(value) <= set(available):
        raise ValueError("選取含未掃描的產品，請重新掃描")
    return sorted(value)


def _group(ids, products, geometries):
    from shapely.ops import unary_union
    known = [i for i in ids if geometries[i] is not None]
    unknown = [i for i in ids if geometries[i] is None]
    union = unary_union([geometries[i] for i in known]) if known else None
    info = _geometry_info(union) if union is not None else dict(footprint=None, bounds=None, bounds_parts=[], area_km2=None, crosses_dateline=False)
    return dict(selected_ids=ids, known_ids=known, unknown_ids=unknown, complete=not unknown,
                coverage=info.pop("footprint"), coverage_scope="complete" if not unknown else "partial_preview", **info), union


def _intersection(left, right, complete=True):
    from shapely.geometry import MultiPolygon
    result = dict(status="unknown", footprint=None, bounds=None, bounds_parts=[], area_km2=None,
                  fraction_left=None, fraction_right=None, touches=None)
    if not complete or left is None or right is None:
        return result
    parts = _polygons(left.intersection(right))
    if not parts or sum(p.area for p in parts) <= 1e-12:
        result.update(status="no_overlap", area_km2=0.0, fraction_left=0.0, fraction_right=0.0, touches=left.touches(right))
        return result
    overlap = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    info = _geometry_info(overlap)
    left_area, right_area = _geometry_info(left)["area_km2"], _geometry_info(right)["area_km2"]
    result.update(info, status="overlap", touches=False,
                  fraction_left=min(1.0, info["area_km2"] / left_area) if left_area else None,
                  fraction_right=min(1.0, info["area_km2"] / right_area) if right_area else None)
    return result


def inspect_coverage(products, left_ids, right_ids):
    """Compare selected geographic outlines; no frame-ID correspondence rule."""
    if not isinstance(products, list) or not 1 <= len(products) <= MAX_PRODUCTS:
        raise ValueError("產品清單數量不合法")
    if any(not isinstance(p, dict) or not isinstance(p.get("id"), str) for p in products):
        raise ValueError("產品清單格式不合法")
    indexed = {p["id"]: p for p in products}
    if len(indexed) != len(products):
        raise ValueError("產品 ID 重複，請重新掃描")
    left_ids, right_ids = _ids(left_ids, indexed), _ids(right_ids, indexed)
    if set(left_ids) & set(right_ids):
        raise ValueError("兩組不得選取同一個產品")
    selected = sorted(set(left_ids + right_ids))
    if len(selected) > MAX_SELECTION:
        raise ValueError("兩組合計最多選取 64 個產品，請分批檢查")
    geometries = {i: _known_geometry(indexed[i]) for i in selected}
    left, a = _group(left_ids, indexed, geometries)
    right, b = _group(right_ids, indexed, geometries)
    intersection = _intersection(a, b, left["complete"] and right["complete"])
    pairs = [dict(left_id=i, right_id=j, **_intersection(geometries[i], geometries[j])) for i in left_ids for j in right_ids]
    checks, warnings = [], [LIMITATION, "不同衛星的 frame 編號與相容格式合成編號不作為對應依據。"]
    for field, label in (("orbit_direction", "升降軌"), ("look_direction", "觀測左右向"), ("level", "產品級別"),
                         ("sensor_mode", "感測器模式"), ("range_sampling_mhz", "距離取樣率")):
        values = {i: indexed[i].get("metadata", {}).get(field) for i in selected}
        known_values = {json.dumps(v, sort_keys=True) for v in values.values() if v is not None}
        status = "unknown" if any(v is None for v in values.values()) else "same" if len(known_values) == 1 else "different"
        checks.append(dict(key=field, label=label, status=status, values=values, compatibility_decision=False))
    pols = {i: sorted(set(indexed[i].get("polarizations") or [])) for i in selected}
    common_pols = sorted(set.intersection(*(set(v) for v in pols.values())))
    checks.append(dict(key="polarizations", label="共同極化", status="unknown" if any(not v for v in pols.values()) else "shared" if common_pols else "different",
                       values=pols, common=common_pols, compatibility_decision=False))
    if not left["complete"] or not right["complete"]:
        warnings.append("選定產品有未知邊界，僅顯示已知部分；整組共同範圍保持未知。")
    revision_data = [dict(id=i, source_fingerprint=indexed[i].get("source_fingerprint"), sensor=indexed[i].get("sensor"),
                          date=indexed[i].get("date"), polarizations=indexed[i].get("polarizations"), metadata=indexed[i].get("metadata"), footprint=indexed[i].get("footprint"),
                          footprint_status=indexed[i].get("footprint_status"), errors=indexed[i].get("errors")) for i in selected]
    revision = _digest(dict(schema_version=SCHEMA_VERSION, left_ids=left_ids, right_ids=right_ids, products=revision_data))
    return _json(dict(schema_version=SCHEMA_VERSION, revision=revision, selected_product_ids=selected,
                      left=left, right=right, intersection=intersection, pairwise=pairs, checks=checks, warnings=warnings,
                      processing_unlocked=False, scientific_validation="not_performed"))


def validate_review(payload, inspection):
    """Validate a human's coverage review against the current source revision.

    ``confirmed`` means the person reviewed this selection, not that an InSAR
    method was validated. Unknown/no-overlap geometry cannot be confirmed as
    shared coverage; the person can keep it pending or mark not_compatible.
    """
    if not isinstance(payload, dict) or not isinstance(inspection, dict):
        raise ValueError("人工確認資料格式不合法")
    outcome = payload.get("outcome", "pending")
    if outcome not in ("pending", "confirmed", "not_compatible"):
        raise ValueError("人工確認結果不合法")
    left = _ids(payload.get("left_ids"))
    right = _ids(payload.get("right_ids"))
    ids = sorted(set(left + right))
    if left != inspection.get("left", {}).get("selected_ids") or right != inspection.get("right", {}).get("selected_ids"):
        raise ValueError("選取產品已改變，請重新檢查覆蓋範圍")
    if payload.get("selected_product_ids") is not None and _ids(payload["selected_product_ids"]) != ids:
        raise ValueError("人工確認產品清單與兩組選取不一致")
    revision = payload.get("revision")
    if not isinstance(revision, str) or not revision or revision != inspection.get("revision"):
        raise ValueError("來源或覆蓋資訊已改變，舊確認不能沿用")
    notes, reviewer = payload.get("notes", ""), payload.get("reviewer", "")
    if not isinstance(notes, str) or len(notes) > 8000 or not isinstance(reviewer, str) or len(reviewer) > 160:
        raise ValueError("確認說明或姓名格式不合法")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in notes + reviewer):
        raise ValueError("人工確認文字含控制字元")
    if outcome != "pending" and (not notes.strip() or not reviewer.strip()):
        raise ValueError("請填寫確認者及判斷依據")
    if outcome == "confirmed" and inspection.get("intersection", {}).get("status") != "overlap":
        raise ValueError("共同覆蓋未知或沒有重疊，請保留待確認或標記不相容")
    return dict(schema_version=SCHEMA_VERSION, selected_product_ids=ids, left_ids=left, right_ids=right,
                revision=revision, outcome=outcome, notes=notes.strip(), reviewer=reviewer.strip(),
                scope="human_metadata_coverage_review", processing_unlocked=False, scientific_validation="not_performed")


def review_status(review, inspection):
    """Return effective review status without mutating the stored record."""
    if not review:
        return dict(outcome="pending", stale=False, reason="尚未完成此次選取的人工確認", processing_unlocked=False)
    try:
        validated = validate_review(review, inspection)
    except (ValueError, TypeError):
        return dict(outcome="pending", stale=True, stored_outcome=review.get("outcome") if isinstance(review, dict) else None,
                    reason="來源、選取或確認格式已改變，請重新人工確認", processing_unlocked=False)
    return dict(outcome=validated["outcome"], stale=False, reason=None, processing_unlocked=False)
