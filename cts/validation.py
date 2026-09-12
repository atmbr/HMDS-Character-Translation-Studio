from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from .engine import HMDSStudioError, encode_text

CONTROL_RE = re.compile(r"\{(?:[0-9A-Fa-f]{2})(?:\s+[0-9A-Fa-f]{2})?\}|\{BOX\}", re.I)
PAGE_RE = re.compile(r"\{BOX\}|\{05\s+0C\}", re.I)
DYNAMIC_RE = re.compile(r"\{(?:FF|81)\s+[0-9A-Fa-f]{2}\}", re.I)
TRAILING_05_RE = re.compile(r"\{05\}\s*$", re.I)

# User-facing placeholders. FF 2A has an intentionally neutral placeholder because
# its exact runtime meaning is not yet confirmed; the raw token is always preserved underneath.
FRIENDLY_TOKENS: Dict[str, str] = {
    "{FF 24}": "[NOME]",
    "{FF 2A}": "[FF 2A]",
    "{81 99}": "[ÍCONE 99]",
    "{81 F4}": "♪",
    "{81 63}": "…",
    "{81 CD}": "♥",
}
RAW_FROM_FRIENDLY: Dict[str, str] = {v: k for k, v in FRIENDLY_TOKENS.items()}
RAW_FROM_FRIENDLY.update({
    "[nome]": "{FF 24}",
    "[Nome]": "{FF 24}",
})

# The editor shows the page command explicitly. This lets translators see that the
# separator is not ordinary text while still allowing a blank line to create one.
EDITOR_PAGE_TOKEN = "{05 0C}"


@dataclass
class ValidationMessage:
    level: str
    message: str


@dataclass
class LineMetric:
    page: int
    line: int
    byte_count: int
    text: str
    overflow: bool


@dataclass
class ValidationResult:
    ok: bool
    messages: List[ValidationMessage] = field(default_factory=list)
    lines: List[LineMetric] = field(default_factory=list)
    pages: int = 1


def _visible_without_controls(text: str) -> str:
    return CONTROL_RE.sub("", text)


def _dynamic_signature(text: str) -> List[str]:
    return [m.group(0).upper() for m in DYNAMIC_RE.finditer(text or "")]


def _needs_trailing_05(text: str) -> bool:
    return bool(TRAILING_05_RE.search(text or ""))


def raw_to_editor(raw: str, *, hide_terminal: bool = True) -> str:
    text = (raw or "").replace("\r", "")
    for raw_token, placeholder in FRIENDLY_TOKENS.items():
        text = re.sub(re.escape(raw_token), placeholder, text, flags=re.I)
    # Decode {BOX} into an explicit visible command, placed on its own line.
    text = PAGE_RE.sub(f"\n{EDITOR_PAGE_TOKEN}\n", text)
    if hide_terminal:
        # Only the implicit 05 from the physical 05 00 terminator is hidden.
        # Any non-terminal {05} remains visible.
        text = TRAILING_05_RE.sub("", text).rstrip()
    return text


def editor_to_raw(editor_text: str) -> str:
    text = (editor_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: List[str] = []
    blank_run = 0

    for line in lines:
        stripped = line.strip()
        if stripped == "":
            blank_run += 1
            continue

        # A visible 05 0C command on its own line is a page break. We intentionally
        # normalize it to {BOX}; encode_text later turns it into bytes 05 0C.
        if re.fullmatch(r"\{05\s+0C\}|\{BOX\}", stripped, re.I):
            if out and out[-1] != "{BOX}":
                out.append("{BOX}")
            blank_run = 0
            continue

        if blank_run and out and out[-1] != "{BOX}":
            # Backward-friendly input: an empty line also creates a new box.
            out.append("{BOX}")
        blank_run = 0
        out.append(line)

    text = "\n".join(out)
    text = text.replace("\n{BOX}\n", "{BOX}")
    text = text.replace("\n{BOX}", "{BOX}").replace("{BOX}\n", "{BOX}")

    for placeholder, raw_token in sorted(RAW_FROM_FRIENDLY.items(), key=lambda kv: len(kv[0]), reverse=True):
        text = text.replace(placeholder, raw_token)
    return text.strip()


def ensure_terminal_control(original: str, translated: str) -> str:
    translated = translated.rstrip()
    if _needs_trailing_05(original) and not _needs_trailing_05(translated):
        translated += "{05}"
    return translated


def validate_translation(original: str, translated: str, max_line_bytes: int = 30,
                         max_lines_per_page: int = 3, protect_controls: bool = True) -> ValidationResult:
    msgs: List[ValidationMessage] = []
    lines: List[LineMetric] = []
    text = translated or ""

    try:
        encode_text(text)
    except HMDSStudioError as exc:
        msgs.append(ValidationMessage("error", str(exc)))
    except Exception as exc:
        msgs.append(ValidationMessage("error", f"Falha ao codificar o texto: {exc}"))

    if protect_controls:
        if _dynamic_signature(original) != _dynamic_signature(text):
            msgs.append(ValidationMessage(
                "error",
                "Códigos dinâmicos {FF xx}/{81 xx} foram alterados. Use os placeholders/botões ou o modo técnico somente quando souber que a mudança é segura."
            ))
        if "Wait>" in original and "Wait>" not in text:
            msgs.append(ValidationMessage("error", "O marcador Wait> existente no original foi removido."))
        if _needs_trailing_05(original) and not _needs_trailing_05(text):
            msgs.append(ValidationMessage("info", "O terminador final 05 00 será preservado automaticamente ao salvar."))
        original_boxes = len(PAGE_RE.findall(original or ""))
        new_boxes = len(PAGE_RE.findall(text or ""))
        if original_boxes != new_boxes:
            msgs.append(ValidationMessage("warning", f"A quantidade de comandos 05 0C mudou: {original_boxes} no original, {new_boxes} na edição."))

    parts = PAGE_RE.split(text)
    pages = max(1, len(parts))
    for pidx, part in enumerate(parts, 1):
        raw_lines = part.replace("\r", "").split("\n")
        if len(raw_lines) > max_lines_per_page:
            msgs.append(ValidationMessage("warning", f"Caixa {pidx} possui {len(raw_lines)} linhas; o limite recomendado é {max_lines_per_page}."))
        for lidx, line in enumerate(raw_lines, 1):
            visible = _visible_without_controls(line)
            try:
                count = len(encode_text(visible))
            except Exception:
                count = len(visible)
            overflow = count > max_line_bytes
            lines.append(LineMetric(pidx, lidx, count, visible, overflow))
            if overflow:
                msgs.append(ValidationMessage("warning", f"Caixa {pidx}, linha {lidx}: {count} bytes (recomendado ≤ {max_line_bytes})."))

    if not text.strip():
        msgs.append(ValidationMessage("warning", "A tradução está vazia."))
    return ValidationResult(ok=not any(m.level == "error" for m in msgs), messages=msgs, lines=lines, pages=pages)


def preview_pages(text: str, max_chars: int = 30, max_lines: int = 3) -> List[List[str]]:
    for raw_token, placeholder in FRIENDLY_TOKENS.items():
        text = re.sub(re.escape(raw_token), placeholder, text or "", flags=re.I)
    parts = PAGE_RE.split(text)
    out: List[List[str]] = []
    for part in parts:
        part = _visible_without_controls(part)
        logical = part.replace("\r", "").split("\n")
        wrapped: List[str] = []
        for ln in logical:
            if len(ln) <= max_chars:
                wrapped.append(ln)
                continue
            cur = ""
            for word in ln.split(" "):
                cand = word if not cur else cur + " " + word
                if len(cand) <= max_chars:
                    cur = cand
                else:
                    if cur:
                        wrapped.append(cur)
                    while len(word) > max_chars:
                        wrapped.append(word[:max_chars])
                        word = word[max_chars:]
                    cur = word
            if cur or not ln:
                wrapped.append(cur)
        if not wrapped:
            wrapped = [""]
        for i in range(0, len(wrapped), max_lines):
            out.append(wrapped[i:i+max_lines])
    return out or [[""]]
