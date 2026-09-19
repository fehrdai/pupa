"""
scene_discovery.py - deriva le liste di scene di PUPA dalla convenzione di
denominazione OBS (desinenze), invece che da nomi letterali hardcoded.

Convenzione (vedi OBS_CONFIG.md):
    _A       scena_A (video/contenuto primario)
    _B       scena_B (pool condiviso tra tutte le scene_A)
    _kick    variante kick-reattiva
    _color   scena identita'/colore (es. red_color, blue_color) - "black_color"
             e' l'unica obbligatoria (fallback universale)
    _wave    scena spettro/waveform

Le slide non hanno una desinenza propria: sono scene_A/_B come le altre,
riconosciute dal CONTENUTO (una sorgente di kind 'slideshow', qualunque
versione - vedi is_slide_scene) invece che dal nome, per la scelta esplicita
dell'operatore (slide = video intercambiabile, non categoria a parte).

Ogni funzione qui e' pura (prende liste/dict gia' ottenuti da OBS, non parla
mai col WebSocket direttamente) - le chiamate API vivono in obs_controller.py,
la logica di discovery vive qui, cosi' e' testabile senza una connessione OBS.
"""
import re

_SUFFIX_A = re.compile(r"^(.+)_A$")
_SUFFIX_B = re.compile(r"^(.+)_B$")
_SUFFIX_KICK = re.compile(r"^(.+)_kick$")
_SUFFIX_COLOR = re.compile(r"^(.+)_color$")
_SUFFIX_WAVE = re.compile(r"^(.+)_wave$")

# Nomi di sorgenti color_source_v3 strutturali/condivise (create una tantum
# per l'intero progetto, non identita' di una singola scena_color) - escluse
# quando si cerca IL colore proprio di una scena in get_scene_color.
STRUCTURAL_COLOR_SOURCE_NAMES = {
    "color_overlay", "color_overlay 2", "black_overlay",
    "PUPA_CALM_0", "PUPA_CALM_1", "PUPA_CALM_2", "PUPA_CALM_3",
    "PUPA_LOOP_SCENE",
}

BLACK_COLOR_SCENE = "black_color"


def discover_a_scenes(scene_names):
    """Tutte le scene che finiscono per '_A' (match pieno, non substring -
    'backup_A_old' non deve corrispondere)."""
    return [s for s in scene_names if _SUFFIX_A.fullmatch(s)]


def discover_b_scenes(scene_names):
    """Tutte le scene che finiscono per '_B'."""
    return [s for s in scene_names if _SUFFIX_B.fullmatch(s)]


def discover_kick_scenes(scene_names):
    """Tutte le varianti kick (sostituisce WAVE_KICK_VARIANTS hardcoded)."""
    return [s for s in scene_names if _SUFFIX_KICK.fullmatch(s)]


def discover_color_scenes(scene_names):
    """Tutte le scene identita'/colore (sostituisce STROBE_COLOR_POOL/
    IDENTITY_SETS['color'] hardcoded)."""
    return [s for s in scene_names if _SUFFIX_COLOR.fullmatch(s)]


def discover_wave_scenes(scene_names):
    """Tutte le scene spettro/waveform (sostituisce IDENTITY_SETS['waveform']
    hardcoded)."""
    return [s for s in scene_names if _SUFFIX_WAVE.fullmatch(s)]


def discover_couples(scene_names):
    """scena_A -> pool CONDIVISO di tutte le scene_B scoperte - stessa
    filosofia di ALL_B_SCENES in brain.py (nessuna curatela di coppie
    specifiche qui, quella resta un'eventuale scelta esplicita in
    scenes_config.yaml)."""
    a_scenes = discover_a_scenes(scene_names)
    b_scenes = discover_b_scenes(scene_names)
    return {a: list(b_scenes) for a in a_scenes}


def has_black_color(color_scenes):
    """True se tra le scene _color scoperte c'e' 'black_color' - vedi
    BLACK_COLOR_SCENE, l'unica desinenza _color obbligatoria (fallback
    universale per flash/strobo quando nessun altro colore e' disponibile)."""
    return BLACK_COLOR_SCENE in color_scenes


def slideshow_input_names(all_inputs):
    """Nomi di tutte le sorgenti di kind 'slideshow' presenti in OBS
    (qualunque versione - usa unversionedInputKind, verificato empiricamente
    che l'installazione corrente espone 'slideshow_v2' con
    unversionedInputKind='slideshow', quindi non hardcodare la versione)."""
    return {
        i.get("inputName")
        for i in all_inputs
        if i.get("unversionedInputKind") == "slideshow" and i.get("inputName")
    }


IMAGE_INPUT_KINDS = ("slideshow", "image_source")


def image_input_names(all_inputs):
    """Nomi di tutte le sorgenti che mostrano IMMAGINI (presentazioni 'slideshow' e immagini
    singole 'image_source'), per KIND e non per nome (2026-09-20, regola "mai immagini con
    immagini" - vedi brain.IMAGE_SCENES)."""
    names = set()
    for i in all_inputs:
        kind = i.get("unversionedInputKind") or i.get("inputKind") or ""
        if i.get("inputName") and any(kind == k or kind.startswith(k) for k in IMAGE_INPUT_KINDS):
            names.add(i["inputName"])
    return names


def scenes_with_images(scene_names, get_item_names, image_names, all_scene_names, max_depth=6):
    """Insieme delle scene di `scene_names` che contengono IMMAGINI, direttamente o dentro una
    scena annidata (una scena che ne contiene un'altra eredita le sue immagini).
    `get_item_names(scena)` -> nomi degli scene item (OBS: get_scene_item_source_names).
    Riconoscimento per CONTENUTO, non per nome della scena."""
    all_scene_names = set(all_scene_names)
    cache = {}

    def has_images(scene, depth, seen):
        if scene in cache:
            return cache[scene]
        if depth > max_depth or scene in seen:
            return False
        seen = seen | {scene}
        found = False
        for name in get_item_names(scene) or []:
            if name in image_names:
                found = True
            elif name in all_scene_names and has_images(name, depth + 1, seen):
                found = True
            if found:
                break
        cache[scene] = found
        return found

    return {s for s in scene_names if has_images(s, 0, frozenset())}


def is_slide_scene(scene_item_names, slide_input_names):
    """True se la scena contiene almeno una sorgente slideshow tra i suoi
    scene item - riconoscimento per CONTENUTO, non per nome della scena."""
    return any(name in slide_input_names for name in scene_item_names)


def color_source_names(all_inputs):
    """Nomi di tutte le sorgenti color_source_v3 presenti in OBS."""
    return {
        i.get("inputName")
        for i in all_inputs
        if i.get("inputKind") == "color_source_v3" and i.get("inputName")
    }


def find_own_color_source(scene_item_names, color_names):
    """Tra gli scene item di una scena _color, trova IL nome della sorgente
    colore propria di questa scena (non condivisa/strutturale) - esclude
    STRUCTURAL_COLOR_SOURCE_NAMES (color_overlay/black_overlay/PUPA_CALM_*/
    PUPA_LOOP_SCENE, nidificate in ogni scena per altri meccanismi, non
    l'identita' colore della scena stessa). None se non trovata (o
    ambigua - piu' di una candidata)."""
    candidates = [
        name for name in scene_item_names
        if name in color_names and name not in STRUCTURAL_COLOR_SOURCE_NAMES
    ]
    return candidates[0] if len(candidates) == 1 else None
