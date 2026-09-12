from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

EXT_MAP = {
    'À':0xA6,'Á':0xA7,'Â':0xA8,'Ã':0xA9,'Ç':0xAA,'È':0xAB,'É':0xAC,'Ê':0xAD,'Ì':0xAE,'Í':0xAF,
    'Î':0xB0,'Ï':0xB1,'Ñ':0xB2,'Ò':0xB3,'Ó':0xB4,'Ô':0xB5,'Õ':0xB6,'Ù':0xB7,'Ú':0xB8,'Û':0xB9,
    'ß':0xBB,'à':0xBC,'á':0xBD,'â':0xBE,'ã':0xBF,'ç':0xC0,'è':0xC1,'é':0xC2,'ê':0xC3,'ì':0xC4,
    'í':0xC5,'î':0xC6,'ï':0xC7,'ñ':0xC8,'ò':0xC9,'ó':0xCA,'ô':0xCB,'õ':0xCC,'ù':0xCD,'ú':0xCE,
    'û':0xCF,'Œ':0xD1,'œ':0xD2,'¿':0xD3,'¡':0xD4,'ª':0xD5,
}

CONTROL_RE = re.compile(r"\{[0-9A-Fa-f ]+\}|\[[0-9A-Fa-f ]+\]")
BOX_SPLIT_RE = re.compile(r"\{BOX\}|\{05(?:\s+0C)?\}", re.I)


@dataclass
class PreviewMetrics:
    pages: int
    max_explicit_line: int
    control_count: int
    overflow: bool


@dataclass
class PortraitContext:
    entity_id: str
    display_name: str
    character_id: Optional[int]
    face_id: Optional[int]
    expression: Optional[int]
    side: str
    image_path: Optional[Path]


class DialoguePreviewRenderer:
    SIZE = (256, 192)
    TEXT_ORIGIN = (8, 8)
    NAME_LEFT_X = 8
    NAME_RIGHT_X = 168
    BG_Y = 64
    NAMEBAR_Y = 176
    TAIL_LEFT = (100, 62)
    TAIL_RIGHT = (140, 62)
    ARROW_TOP_LEFT = (236, 45)

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or Path(__file__).with_name('data')
        self.preview_dir = self.data_dir / 'dialogue_preview'
        self.portrait_dir = self.data_dir / 'portrait_states'
        self.available = bool(Image is not None and (self.preview_dir / 'ui' / 'dialogue_box_256x64.png').exists())
        self._cache = {}

    @property
    def dependency_error(self) -> Optional[str]:
        if Image is None:
            return 'Pillow não está instalado. Execute: pip install Pillow'
        if not self.available:
            return 'Assets do preview não foram encontrados no pacote.'
        return None

    def _open(self, path: Path):
        key = str(path)
        im = self._cache.get(key)
        if im is None:
            im = Image.open(path).convert('RGBA')
            self._cache[key] = im
        return im

    def background_ids(self):
        return list(range(57))

    def background_path(self, bg_id: int) -> Path:
        return self.preview_dir / 'backgrounds' / f'bg_{int(bg_id):02d}.png'

    def portrait_path(self, face_id: int, expression: int, side: str) -> Optional[Path]:
        p = self.portrait_dir / f'f{int(face_id):02d}_e{int(expression)}_{side}.png'
        if p.exists():
            return p
        return None

    @staticmethod
    def _friendly_dynamic_text(text: str, ff2a_placeholder: str = "[DINÂMICO]") -> str:
        text = str(text or '')
        text = re.sub(r'\{FF\s+24\}', '[NOME]', text, flags=re.I)
        text = re.sub(r'\{FF\s+2A\}', ff2a_placeholder, text, flags=re.I)
        text = re.sub(r'\{81\s+99\}', '[ÍCONE 99]', text, flags=re.I)
        text = re.sub(r'\{81\s+F4\}', '♪', text, flags=re.I)
        text = re.sub(r'\{81\s+63\}', '…', text, flags=re.I)
        text = re.sub(r'\{81\s+CD\}', '♥', text, flags=re.I)
        return text

    @classmethod
    def _strip_controls(cls, text: str, ff2a_placeholder: str = "[DINÂMICO]") -> str:
        return CONTROL_RE.sub('', cls._friendly_dynamic_text(text, ff2a_placeholder))

    @staticmethod
    def wrap_lines(text: str, max_chars: int = 30):
        out = []
        for logical in str(text or '').replace('\r', '').split('\n'):
            if logical == '':
                out.append('')
                continue
            line = ''
            for word0 in logical.split(' '):
                word = word0
                cand = (line + ' ' + word) if line else word
                if len(cand) <= max_chars:
                    line = cand
                    continue
                if line:
                    out.append(line)
                while len(word) > max_chars:
                    out.append(word[:max_chars])
                    word = word[max_chars:]
                line = word
            if line:
                out.append(line)
        return out

    @classmethod
    def pages_from_raw(cls, raw: str, ff2a_placeholder: str = "[DINÂMICO]"):
        raw = cls._friendly_dynamic_text(raw, ff2a_placeholder)
        parts = BOX_SPLIT_RE.split(raw)
        pages = []
        for part in parts:
            part = cls._strip_controls(part, ff2a_placeholder)
            if not part.strip() and '\n' not in part:
                continue
            lines = cls.wrap_lines(part, 30)
            if not lines:
                pages.append([''])
                continue
            for i in range(0, len(lines), 3):
                pages.append(lines[i:i+3])
        return pages or [['']]

    @classmethod
    def metrics(cls, raw: str):
        visible = cls._strip_controls(raw)
        lines = visible.replace('\r', '').split('\n')
        max_line = max([len(x) for x in lines] or [0])
        controls = len(re.findall(r"\{[^}]+\}", str(raw or '')))
        pages = cls.pages_from_raw(raw)
        return PreviewMetrics(len(pages), max_line, controls, max_line > 30)

    @staticmethod
    def _glyph_index(ch: str) -> int:
        cp = ord(ch)
        if 0x20 <= cp <= 0x7E:
            b = cp
        else:
            b = EXT_MAP.get(ch, 0x3F)
        if 0x20 <= b <= 0x7E:
            return b - 0x20
        if 0xA1 <= b <= 0xD7:
            return 95 + (b - 0xA1)
        return 0x3F - 0x20

    @staticmethod
    def _hex_rgb(value: str) -> tuple[int, int, int]:
        try:
            v = str(value or "#777777").lstrip("#")
            if len(v) != 6:
                raise ValueError
            return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
        except Exception:
            return 119, 119, 119

    def _draw_text(self, dst, atlas, text: str, x0: int, y0: int, *,
                   shadow: bool = True, shadow_color: str = "#777777",
                   shadow_x: int = -1, shadow_y: int = 0):
        x, y = x0, y0
        shadow_x = max(-4, min(4, int(shadow_x)))
        shadow_y = max(-4, min(4, int(shadow_y)))
        sr, sg, sb = self._hex_rgb(shadow_color)
        for ch in text:
            if ch == '\n':
                x = x0
                y += 16
                continue
            idx = self._glyph_index(ch)
            sx = (idx % 16) * 8
            sy = (idx // 16) * 16
            glyph = atlas.crop((sx, sy, sx+8, sy+16))
            if shadow and (shadow_x or shadow_y):
                # Soft single-copy shadow. It keeps the ROM glyph alpha but uses a
                # lighter, semi-transparent gray by default so it never reads as a
                # second black text layer.
                alpha = glyph.getchannel('A')
                shadow_glyph = Image.new('RGBA', glyph.size, (sr, sg, sb, 0))
                shadow_glyph.putalpha(alpha.point(lambda a: int(a * 0.32)))
                dst.alpha_composite(shadow_glyph, (x + shadow_x, y + shadow_y))
            dst.alpha_composite(glyph, (x, y))
            x += 8

    def contexts_from_entity_rows(self, rows: Iterable[dict]):
        out = []
        seen = set()
        for r in rows:
            faces = r.get('face_ids') or []
            exprs = r.get('expressions') or []
            sides = r.get('sides') or []
            if not faces:
                continue
            face = int(faces[0])
            expr = int(exprs[0]) if exprs else 0
            side = sides[0] if sides else 'right'
            key = (r.get('entity_id'), face, expr, side)
            if key in seen:
                continue
            seen.add(key)
            out.append(PortraitContext(
                entity_id=str(r.get('entity_id', '')),
                display_name=str(r.get('display_name') or r.get('entity_id') or ''),
                character_id=(int(r['character_id']) if r.get('character_id') is not None else None),
                face_id=face,
                expression=expr,
                side=side,
                image_path=self.portrait_path(face, expr, side),
            ))
        return out

    def compose(self, raw_text: str, name: str = '', bg_id: int = 0, page: int = 0,
                contexts: Iterable[PortraitContext] = (), speaker_entity_id: str | None = None,
                text_shadow: bool = True, shadow_color: str = "#777777", shadow_x: int = -1, shadow_y: int = 0,
                ff2a_placeholder: str = "[DINÂMICO]"):
        if self.dependency_error:
            raise RuntimeError(self.dependency_error)
        contexts = list(contexts)
        canvas = Image.new('RGBA', self.SIZE, (0, 0, 0, 255))
        bg = self._open(self.background_path(bg_id))
        canvas.alpha_composite(bg, (0, self.BG_Y))
        for p in contexts:
            if not p.image_path:
                continue
            im = self._open(p.image_path)
            x = 256 - im.width if p.side == 'right' else 0
            y = 64 + max(0, 112 - im.height)
            canvas.alpha_composite(im, (x, y))
        box = self._open(self.preview_dir / 'ui' / 'dialogue_box_256x64.png')
        canvas.alpha_composite(box, (0, 0))
        speaker = None
        if speaker_entity_id:
            speaker = next((p for p in contexts if p.entity_id == speaker_entity_id), None)
        if speaker is None and len(contexts) == 1:
            speaker = contexts[0]
        if speaker and speaker.image_path:
            tail = self._open(self.preview_dir / 'ui' / 'dialogue_box_tail_16x16.png')
            canvas.alpha_composite(tail, self.TAIL_RIGHT if speaker.side == 'right' else self.TAIL_LEFT)
        namebar = self._open(self.preview_dir / 'ui' / 'dialogue_name_bar_256x16.png')
        canvas.alpha_composite(namebar, (0, self.NAMEBAR_Y))
        atlas = self._open(self.preview_dir / 'font' / 'western_font_atlas_8x16.png')
        pages = self.pages_from_raw(raw_text, ff2a_placeholder)
        page = max(0, min(int(page), len(pages)-1))
        self._draw_text(canvas, atlas, '\n'.join(pages[page]), *self.TEXT_ORIGIN, shadow=text_shadow, shadow_color=shadow_color, shadow_x=shadow_x, shadow_y=shadow_y)
        if name:
            if speaker and speaker.side == 'right':
                nx = self.NAME_RIGHT_X
            elif speaker and speaker.side == 'left':
                nx = self.NAME_LEFT_X
            else:
                nx = max(2, (256 - len(name) * 8) // 2)
            nx = max(2, min(254 - len(name) * 8, nx))
            self._draw_text(canvas, atlas, name, nx, self.NAMEBAR_Y, shadow=text_shadow, shadow_color=shadow_color, shadow_x=shadow_x, shadow_y=shadow_y)
        if len(pages) > 1 or re.search(r"\{05|\{0C|\{BOX", str(raw_text), re.I):
            arrow = self._open(self.preview_dir / 'ui' / 'dialogue_advance_arrow_16x16.png')
            canvas.alpha_composite(arrow, self.ARROW_TOP_LEFT)
        return canvas, pages, self.metrics(raw_text)
