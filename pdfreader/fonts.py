"""Local font catalog. PDF operations never initiate a font download."""
from functools import lru_cache
import hashlib
from collections import OrderedDict
import io
import json
import os
from pathlib import Path
import re
import sys
import pymupdf as fitz
from .paths import data_root, resource_root


def font_key(name):
    name = re.sub(r"-\d+$", "", re.sub(r"^[A-Z]{6}\+", "", name))
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    return key.removesuffix("regular").removesuffix("psmt").removesuffix("mt")


@lru_cache(maxsize=1)
def catalog():
    data = json.loads((resource_root()/"assets/fonts/catalog.json").read_text(encoding="utf-8"))
    return {item["id"]:item for item in data["fonts"]}


def cache_dir():
    path = data_root()/"fonts"
    path.mkdir(exist_ok=True)
    return path


@lru_cache(maxsize=32)
def verified_file(filename, modified, length, expected):
    return hashlib.sha256(Path(filename).read_bytes()).hexdigest() == expected


def catalog_path(identity):
    item = catalog()[identity]
    paths = [resource_root()/"assets/fonts"/item["filename"], cache_dir()/item["filename"]]
    for path in paths:
        if path.is_file():
            stat=path.stat()
            if stat.st_size == item["bytes"] and verified_file(str(path),stat.st_mtime_ns,stat.st_size,item["sha256"]):
                return path
    return None


FONT_CACHE_BYTES=64*1024*1024
_face_cache=OrderedDict()


def face_from_file(filename):
    path=Path(filename); stat=path.stat(); key=(str(path),stat.st_mtime_ns,stat.st_size)
    if key in _face_cache:
        _face_cache.move_to_end(key); return _face_cache[key][0]
    font=fitz.Font(fontfile=str(path))
    if stat.st_size>FONT_CACHE_BYTES:
        return font  # Oversize system fonts are usable, but never retained.
    _face_cache[key]=(font,stat.st_size)
    while len(_face_cache)>12 or sum(x[1] for x in _face_cache.values())>FONT_CACHE_BYTES:
        _face_cache.popitem(last=False)
    return font


_subset_cache=OrderedDict()


def subset_font(font,text):
    """Subset before installing a font, avoiding MuPDF CFF subsetting failures."""
    raw=font.buffer
    characters="".join(sorted(set(text)))
    key=(hashlib.sha256(raw).digest(),characters)
    if key in _subset_cache:
        _subset_cache.move_to_end(key); return _subset_cache[key]
    if raw[:4] not in (b"OTTO",b"\x00\x01\x00\x00",b"true",b"ttcf"):
        return raw  # Small legacy Type1/CFF buffers are not OpenType containers.
    from fontTools.ttLib import TTFont
    from fontTools import subset
    face=TTFont(io.BytesIO(raw),recalcTimestamp=False,fontNumber=0)
    options=subset.Options()
    options.ignore_missing_unicodes=False
    options.retain_gids=True  # CID-keyed CFF fonts require their original glyph IDs.
    options.layout_features=[]
    subsetter=subset.Subsetter(options=options)
    subsetter.populate(text=characters)
    subsetter.subset(face)
    output=io.BytesIO(); face.save(output); face.close()
    data=output.getvalue()
    _subset_cache[key]=data
    while len(_subset_cache)>32 or sum(len(x) for x in _subset_cache.values())>8*1024*1024:
        _subset_cache.popitem(last=False)
    return data


_qt_fonts={}
_font_owners={}


def _register_qt(key,raw,owner):
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QFontDatabase
    if key not in _qt_fonts:
        handle=QFontDatabase.addApplicationFontFromData(QByteArray(raw))
        families=QFontDatabase.applicationFontFamilies(handle)
        _qt_fonts[key]=(handle,families[0] if families else "")
    if owner is not None: _font_owners.setdefault(id(owner),set()).add(key)
    return _qt_fonts[key][1]


def release_fonts(owner):
    from PySide6.QtGui import QFontDatabase
    _font_owners.pop(id(owner),None)
    retained=set().union(*_font_owners.values()) if _font_owners else set()
    for key,(handle,_) in list(_qt_fonts.items()):
        if key not in retained:
            if handle>=0: QFontDatabase.removeApplicationFont(handle)
            del _qt_fonts[key]


def library_face(identity):
    if identity.startswith("import:"):
        name=identity[7:]
        if Path(name).name != name:
            raise ValueError("无效的字体文件。")
        path=cache_dir()/"imported"/name
    else:
        path=catalog_path(identity)
    if path is None or not path.is_file():
        raise ValueError("此字体尚未安装，请先在字体库获取。")
    return face_from_file(str(path))


def font_choices(available_only=False):
    result=[]
    for identity,item in catalog().items():
        available=catalog_path(identity) is not None
        if available or not available_only:
            result.append((item["label"] + ("" if available else "（下载）"),"library:"+identity))
    directory=cache_dir()/"imported"
    if directory.exists():
        for path in sorted(directory.glob("*")):
            if path.suffix.lower() not in (".ttf",".otf",".ttc"): continue
            try: result.append((face_from_file(str(path)).name,"library:import:"+path.name))
            except (ValueError,RuntimeError): continue
    return result


def import_font(filename):
    source=Path(filename)
    if source.suffix.lower() not in (".ttf",".otf",".ttc") or source.stat().st_size>64*1024*1024:
        raise ValueError("请选择小于 64 MiB 的 TTF、OTF 或 TTC 字体。")
    raw=source.read_bytes()
    face=fitz.Font(fontbuffer=raw)  # Validate before publishing to the library.
    if face.glyph_count<2: raise ValueError("该字体没有可用字形。")
    directory=cache_dir()/"imported"; directory.mkdir(exist_ok=True)
    name=hashlib.sha256(raw).hexdigest()+source.suffix.lower()
    path=directory/name
    if not path.exists():
        temp=path.with_suffix(".tmp")
        temp.write_bytes(raw)
        os.replace(temp,path)
    return "library:import:"+name


def catalog_match(name):
    key=font_key(name)
    for identity,item in catalog().items():
        names=(item["family"]+"-"+item["style"],Path(item["filename"]).stem)
        if key in (font_key(n) for n in names): return identity
    return None


@lru_cache(maxsize=1)
def system_files():
    if sys.platform=="win32":
        directories=[Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts",Path(os.environ.get("LOCALAPPDATA",Path.home()))/"Microsoft/Windows/Fonts"]
    elif sys.platform=="darwin":
        directories=[Path("/System/Library/Fonts"),Path("/Library/Fonts"),Path.home()/"Library/Fonts"]
    else:
        directories=[Path("/usr/share/fonts"),Path.home()/".local/share/fonts"]
    return tuple(p for directory in directories if directory.exists() for p in directory.rglob("*")
                 if p.suffix.lower() in (".ttf",".otf",".ttc"))


def exact_local_face(name,text):
    key=font_key(name)
    candidates=[]
    matched=catalog_match(name)
    if matched and catalog_path(matched): candidates.append(catalog_path(matched))
    for _,identity in font_choices(True):
        if identity.startswith("library:import:"):
            font=library_face(identity[8:])
            if font_key(font.name)==key and supports(font,text): return font
    # Filename filtering keeps a missing-font lookup from parsing every system font.
    aliases={"microsoftyahei":"msyh","microsoftyaheibold":"msyhbd","microsoftyaheilight":"msyhl",
             "arial":"arial","arialbold":"arialbd","arialitalic":"ariali","arialbolditalic":"arialbi",
             "timesnewroman":"times","timesnewromanpsbold":"timesbd","timesnewromanpsitalic":"timesi",
             "segoeui":"segoeui","segoeuibold":"segoeuib","couriernew":"cour"}
    stems={key,aliases.get(key,key)}
    candidates += [p for p in system_files() if font_key(p.stem) in stems]
    for path in candidates:
        try:
            font=face_from_file(str(path))
            if font_key(font.name)==key and supports(font,text): return font
        except (ValueError,RuntimeError,OSError): continue
    return None


def supports(font,text):
    return all(c.isspace() or font.has_glyph(ord(c),fallback=False) for c in set(text))


def qt_family(identity,owner=None):
    identity=identity.removeprefix("library:")
    key="library:"+identity
    if key in _qt_fonts:
        if owner is not None: _font_owners.setdefault(id(owner),set()).add(key)
        return _qt_fonts[key][1]
    font=library_face(identity)
    return _register_qt(key,font.buffer,owner) or font.name


def qt_original_family(page,name,text,owner=None):
    from .text_edit import resolve_font
    face=resolve_font(page,name,text).font
    raw=face.buffer
    key="native:"+hashlib.sha256(raw).hexdigest()
    return _register_qt(key,raw,owner) or re.sub(r"-\d+$","",name.split("+")[-1])


def character_font(identity,char):
    face=library_face(identity[8:])
    if char.isspace() or face.has_glyph(ord(char),fallback=False): return identity
    for fallback in ("sans-sc","sans","math","symbols"):
        if library_face(fallback).has_glyph(ord(char),fallback=False): return "library:"+fallback
    raise ValueError(f"字体库没有字符 U+{ord(char):04X}（{char}）。")
