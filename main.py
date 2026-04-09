from __future__ import annotations

import argparse
import math
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pygame
from mutagen import File as MutagenFile

try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    tk = None
    filedialog = None

SCALE = 2

WIDTH = 275
MAIN_HEIGHT = 116
PLAYLIST_HEIGHT = 232
HEIGHT = MAIN_HEIGHT + PLAYLIST_HEIGHT
FPS = 60
MUSIC_END_EVENT = pygame.USEREVENT + 1
SUPPORTED_EXTENSIONS = {".mp3", ".ogg", ".wav"}

# ---------------------------------------------------------------------------
# PythonAMP 2.x default skin palette
# ---------------------------------------------------------------------------
FRAME_BG = (56, 56, 70)
FRAME_LIGHT = (86, 86, 102)
FRAME_DARK = (28, 28, 36)

TITLE_L = (0, 0, 107)
TITLE_R = (0, 74, 148)
TITLE_GRIP_DARK = (0, 0, 56)
TITLE_GRIP_LIGHT = (40, 90, 190)

LCD_BG = (0, 0, 0)
LCD_GREEN = (0, 255, 0)
LCD_DIM = (0, 150, 0)
LCD_AMBER = (255, 176, 0)

BTN_FACE = (56, 56, 68)
BTN_LIGHT = (84, 84, 100)
BTN_DARK = (28, 28, 36)
BTN_TEXT = (180, 180, 192)

GROOVE_BG = (14, 14, 22)
GROOVE_FILL = (0, 170, 0)
THUMB_FACE = (96, 96, 112)
THUMB_LIGHT = (134, 134, 150)
THUMB_DARK = (52, 52, 66)

PL_BG = (0, 0, 0)
PL_TEXT = (0, 255, 0)
PL_SEL_BG = (0, 0, 120)
PL_SEL_TEXT = (255, 255, 255)
PL_CUR_TEXT = (255, 255, 255)

TOGGLE_ON = (0, 190, 0)

# ---------------------------------------------------------------------------
# 7-segment LED digit data
# Segments: a=top  b=top-right  c=bot-right  d=bottom  e=bot-left  f=top-left  g=middle
# ---------------------------------------------------------------------------
SEG_MAP: dict[str, tuple[int, ...]] = {
    "0": (1, 1, 1, 1, 1, 1, 0),
    "1": (0, 1, 1, 0, 0, 0, 0),
    "2": (1, 1, 0, 1, 1, 0, 1),
    "3": (1, 1, 1, 1, 0, 0, 1),
    "4": (0, 1, 1, 0, 0, 1, 1),
    "5": (1, 0, 1, 1, 0, 1, 1),
    "6": (1, 0, 1, 1, 1, 1, 1),
    "7": (1, 1, 1, 0, 0, 0, 0),
    "8": (1, 1, 1, 1, 1, 1, 1),
    "9": (1, 1, 1, 1, 0, 1, 1),
    " ": (0, 0, 0, 0, 0, 0, 0),
}

DIGIT_W = 13
DIGIT_H = 23
SEG_T = 2
COLON_W = 7


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}"


def truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: max(0, n - 3)] + "..."


# ---------------------------------------------------------------------------
# Drawing primitives
# ---------------------------------------------------------------------------

def bevel(
    surf: pygame.Surface,
    rect: pygame.Rect,
    *,
    fill: tuple[int, int, int],
    light: tuple[int, int, int] = FRAME_LIGHT,
    dark: tuple[int, int, int] = FRAME_DARK,
    pressed: bool = False,
    n: int = 1,
) -> None:
    surf.fill(fill, rect)
    hi, lo = (dark, light) if pressed else (light, dark)
    for i in range(n):
        r = rect.inflate(-(i * 2), -(i * 2))
        pygame.draw.line(surf, hi, r.topleft, (r.right - 1, r.top))
        pygame.draw.line(surf, hi, r.topleft, (r.left, r.bottom - 1))
        pygame.draw.line(surf, lo, (r.left, r.bottom - 1), (r.right - 1, r.bottom - 1))
        pygame.draw.line(surf, lo, (r.right - 1, r.top), (r.right - 1, r.bottom - 1))


def groove(surf: pygame.Surface, rect: pygame.Rect) -> None:
    bevel(surf, rect, fill=GROOVE_BG, light=FRAME_DARK, dark=FRAME_LIGHT, n=1)


def hgradient(surf: pygame.Surface, rect: pygame.Rect, c1: tuple[int, int, int], c2: tuple[int, int, int]) -> None:
    w = max(1, rect.width)
    for x in range(w):
        t = x / w
        c = (int(c1[0] + (c2[0] - c1[0]) * t), int(c1[1] + (c2[1] - c1[1]) * t), int(c1[2] + (c2[2] - c1[2]) * t))
        pygame.draw.line(surf, c, (rect.left + x, rect.top), (rect.left + x, rect.bottom - 1))


def grip_lines(surf: pygame.Surface, x1: int, x2: int, y1: int, y2: int) -> None:
    for gx in range(x1, x2, 4):
        pygame.draw.line(surf, TITLE_GRIP_DARK, (gx, y1), (gx, y2))
        pygame.draw.line(surf, TITLE_GRIP_LIGHT, (gx + 1, y1), (gx + 1, y2))


# ---------------------------------------------------------------------------
# LED 7-segment drawing
# ---------------------------------------------------------------------------

def led_digit(surf: pygame.Surface, x: int, y: int, ch: str, on: tuple[int, int, int], off: tuple[int, int, int]) -> None:
    segs = SEG_MAP.get(ch, SEG_MAP[" "])
    w, h, t = DIGIT_W, DIGIT_H, SEG_T
    half = h // 2
    defs = [
        (x + t, y, w - 2 * t, t),
        (x + w - t, y + t, t, half - t),
        (x + w - t, y + half + 1, t, half - t - 1),
        (x + t, y + h - t, w - 2 * t, t),
        (x, y + half + 1, t, half - t - 1),
        (x, y + t, t, half - t),
        (x + t, y + half - t // 2, w - 2 * t, t),
    ]
    for i, d in enumerate(defs):
        pygame.draw.rect(surf, on if segs[i] else off, d)


def led_colon(surf: pygame.Surface, x: int, y: int, c: tuple[int, int, int]) -> None:
    h, t = DIGIT_H, SEG_T
    cx = x + COLON_W // 2 - t // 2
    pygame.draw.rect(surf, c, (cx, y + h // 3, t, t))
    pygame.draw.rect(surf, c, (cx, y + 2 * h // 3, t, t))


def led_time(surf: pygame.Surface, x: int, y: int, s: str, on: tuple[int, int, int], off: tuple[int, int, int]) -> None:
    gap = 2
    cx = x
    for ch in s:
        if ch == ":":
            led_colon(surf, cx, y, on)
            cx += COLON_W + gap
        else:
            led_digit(surf, cx, y, ch, on, off)
            cx += DIGIT_W + gap


# ---------------------------------------------------------------------------
# Audio metadata
# ---------------------------------------------------------------------------

def extract_meta(path: Path) -> tuple[str, float, int, int]:
    title = path.stem
    duration = 0.0
    bitrate = 0
    sample_rate = 0

    try:
        audio = MutagenFile(path)
    except Exception:
        audio = None

    if audio and getattr(audio, "info", None):
        info = audio.info
        duration = float(getattr(info, "length", 0.0) or 0.0)
        bitrate = int(getattr(info, "bitrate", 0) or 0)
        sample_rate = int(getattr(info, "sample_rate", 0) or 0)

    if audio and getattr(audio, "tags", None):
        for key in ("TIT2", "title", "\xa9nam"):
            value = audio.tags.get(key)
            if not value:
                continue
            if hasattr(value, "text") and value.text:
                title = str(value.text[0])
            elif isinstance(value, list):
                title = str(value[0])
            else:
                title = str(value)
            break

    return title.strip() or path.stem, duration, bitrate, sample_rate


# ---------------------------------------------------------------------------
# File pickers
# ---------------------------------------------------------------------------

def pick_files_macos() -> tuple[str, ...]:
    script = (
        'set f to choose file with prompt "Add songs" with multiple selections allowed\n'
        "set o to {}\n"
        "repeat with i in f\nset end of o to POSIX path of i\nend repeat\n"
        "set AppleScript's text item delimiters to linefeed\nreturn o as text\n"
    )
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
    except OSError:
        return ()
    if r.returncode != 0:
        return ()
    return tuple(l.strip() for l in r.stdout.splitlines() if l.strip())


def pick_files_tk() -> tuple[str, ...]:
    if tk is None or filedialog is None:
        return ()
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askopenfilenames(
            title="Add songs",
            filetypes=[("Audio files", "*.mp3 *.ogg *.wav"), ("All files", "*.*")],
        )
    finally:
        root.destroy()
    return tuple(chosen)


# ---------------------------------------------------------------------------
# Track
# ---------------------------------------------------------------------------

@dataclass
class Track:
    path: Path
    title: str
    duration: float
    bitrate: int = 0
    sample_rate: int = 0

    @classmethod
    def from_path(cls, p: str | Path) -> Track:
        path = Path(p).expanduser().resolve()
        title, dur, br, sr = extract_meta(path)
        return cls(path=path, title=title, duration=dur, bitrate=br, sample_rate=sr)


# ---------------------------------------------------------------------------
# PlayerApp
# ---------------------------------------------------------------------------

class PlayerApp:
    def __init__(self) -> None:
        pygame.display.set_caption("PythonAMP")
        self.window = pygame.display.set_mode((WIDTH * SCALE, HEIGHT * SCALE))
        self.screen = pygame.Surface((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()

        self.f8 = pygame.font.SysFont("Arial", 9, bold=True)
        self.f9 = pygame.font.SysFont("Arial", 10, bold=True)
        self.f10 = pygame.font.SysFont("Arial", 11, bold=True)
        self.f_lcd = pygame.font.SysFont("Courier New", 11, bold=False)

        # Hit regions --------------------------------------------------------
        self.display_rect = pygame.Rect(11, 16, 253, 42)
        self.seek_rect = pygame.Rect(16, 62, 243, 10)
        self.volume_rect = pygame.Rect(107, 77, 68, 10)
        self.balance_rect = pygame.Rect(177, 77, 38, 10)
        self.eq_rect = pygame.Rect(219, 77, 23, 10)
        self.pl_rect = pygame.Rect(244, 77, 23, 10)

        self.btn = {
            "prev":  pygame.Rect(16, 88, 23, 18),
            "play":  pygame.Rect(39, 88, 23, 18),
            "pause": pygame.Rect(62, 88, 23, 18),
            "stop":  pygame.Rect(85, 88, 23, 18),
            "next":  pygame.Rect(108, 88, 22, 18),
            "open":  pygame.Rect(136, 88, 22, 18),
        }

        self.pl_list = pygame.Rect(4, MAIN_HEIGHT + 20, WIDTH - 8, PLAYLIST_HEIGHT - 36)
        self.pl_btn = {
            "add":   pygame.Rect(5, HEIGHT - 15, 40, 12),
            "clear": pygame.Rect(48, HEIGHT - 15, 40, 12),
        }

        # State --------------------------------------------------------------
        self.running = True
        self.dragging_seek = False
        self.dragging_volume = False
        self.last_pl_click = 0.0
        self.last_click_idx: int | None = None
        self.scroll_x = 0.0

        self.tracks: list[Track] = []
        self.sel: int | None = None
        self.cur: int | None = None
        self.scroll_off = 0
        self.volume = 0.75
        self.paused = False
        self.stopped = True
        self.start_off = 0.0
        self.session_t = 0.0
        self.pause_pos = 0.0
        self.eq_on = True
        self.pl_on = True

        pygame.mixer.music.set_volume(self.volume)
        pygame.mixer.music.set_endevent(MUSIC_END_EVENT)

    # -- helpers --

    def cur_track(self) -> Track | None:
        if self.cur is not None and 0 <= self.cur < len(self.tracks):
            return self.tracks[self.cur]
        return None

    def cur_pos(self) -> float:
        t = self.cur_track()
        if not t:
            return 0.0
        if self.stopped:
            return 0.0
        if self.paused:
            return clamp(self.pause_pos, 0.0, t.duration)
        return clamp(self.start_off + (time.perf_counter() - self.session_t), 0.0, t.duration)

    def _vis_rows(self) -> int:
        return max(1, (self.pl_list.height - 4) // 16)

    def ensure_vis(self) -> None:
        if self.sel is None:
            return
        vis = self._vis_rows()
        if self.sel < self.scroll_off:
            self.scroll_off = self.sel
        elif self.sel >= self.scroll_off + vis:
            self.scroll_off = self.sel - vis + 1

    # -- file management --

    def add_files(self, paths: list[str] | tuple[str, ...]) -> None:
        seen = {t.path for t in self.tracks}
        for rp in paths:
            p = Path(rp).expanduser()
            if not p.exists() or p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            r = p.resolve()
            if r in seen:
                continue
            self.tracks.append(Track.from_path(r))
            seen.add(r)
        if self.sel is None and self.tracks:
            self.sel = 0
            self.ensure_vis()

    def open_dialog(self) -> None:
        chosen = pick_files_macos() if sys.platform == "darwin" else pick_files_tk()
        if chosen:
            self.add_files(chosen)

    # -- playback --

    def _restart(self, at: float, *, paused: bool = False) -> None:
        t = self.cur_track()
        if not t:
            return
        at = clamp(at, 0.0, t.duration or at)
        try:
            pygame.mixer.music.load(str(t.path))
            pygame.mixer.music.set_volume(self.volume)
            pygame.mixer.music.play(start=at)
        except pygame.error:
            self.stopped = True
            return
        self.start_off = at
        self.session_t = time.perf_counter()
        self.pause_pos = at
        self.stopped = False
        self.paused = False
        if paused:
            pygame.mixer.music.pause()
            self.pause_pos = at
            self.paused = True

    def play_idx(self, i: int, *, at: float = 0.0) -> None:
        if not (0 <= i < len(self.tracks)):
            return
        self.cur = i
        self.sel = i
        self.scroll_x = 0.0
        self.ensure_vis()
        self._restart(at)

    def toggle_pause(self) -> None:
        if self.cur is None:
            if self.sel is not None:
                self.play_idx(self.sel)
            return
        if self.stopped:
            self.play_idx(self.cur)
            return
        if self.paused:
            pygame.mixer.music.unpause()
            self.start_off = self.pause_pos
            self.session_t = time.perf_counter()
            self.paused = False
        else:
            self.pause_pos = self.cur_pos()
            pygame.mixer.music.pause()
            self.paused = True

    def do_stop(self) -> None:
        pygame.mixer.music.stop()
        self.stopped = True
        self.paused = False
        self.start_off = self.pause_pos = 0.0

    def next_track(self, *, auto: bool = False) -> None:
        if not self.tracks:
            return
        if self.cur is None:
            self.play_idx(0)
            return
        if auto and self.cur >= len(self.tracks) - 1:
            self.do_stop()
            return
        self.play_idx(min(len(self.tracks) - 1, self.cur + 1))

    def prev_track(self) -> None:
        if not self.tracks:
            return
        if self.cur is not None and self.cur_pos() > 3:
            self.play_idx(self.cur)
            return
        self.play_idx(max(0, (self.cur or 1) - 1))

    def seek_ratio(self, r: float) -> None:
        t = self.cur_track()
        if t and t.duration > 0:
            self._restart(clamp(r, 0.0, 1.0) * t.duration, paused=self.paused)

    def set_vol(self, r: float) -> None:
        self.volume = clamp(r, 0.0, 1.0)
        pygame.mixer.music.set_volume(self.volume)

    def clear_pl(self) -> None:
        self.do_stop()
        self.tracks.clear()
        self.sel = self.cur = None
        self.scroll_off = 0

    def remove_sel(self) -> None:
        if self.sel is None or not (0 <= self.sel < len(self.tracks)):
            return
        was_cur = self.sel == self.cur
        del self.tracks[self.sel]
        if was_cur:
            pygame.mixer.music.stop()
            self.cur = None
            self.stopped = True
            self.paused = False
            self.start_off = self.pause_pos = 0.0
        elif self.cur is not None and self.sel < self.cur:
            self.cur -= 1
        if not self.tracks:
            self.sel = self.cur = None
        else:
            self.sel = min(self.sel, len(self.tracks) - 1)
        self.ensure_vis()

    # -- events --

    def _click_btn(self, name: str) -> None:
        actions = {
            "prev": self.prev_track,
            "play": lambda: self.play_idx(self.sel if self.sel is not None else 0) if self.tracks else None,
            "pause": self.toggle_pause,
            "stop": self.do_stop,
            "next": self.next_track,
            "open": self.open_dialog,
            "add": self.open_dialog,
            "clear": self.clear_pl,
        }
        fn = actions.get(name)
        if fn:
            fn()

    def _pl_idx(self, pos: tuple[int, int]) -> int | None:
        if not self.pl_list.collidepoint(pos):
            return None
        row = (pos[1] - self.pl_list.top - 2) // 16
        idx = self.scroll_off + row
        return idx if 0 <= idx < len(self.tracks) else None

    def _mouse_down(self, pos: tuple[int, int]) -> None:
        if self.seek_rect.collidepoint(pos):
            self.dragging_seek = True
            self.seek_ratio((pos[0] - self.seek_rect.left) / self.seek_rect.width)
            return
        if self.volume_rect.collidepoint(pos):
            self.dragging_volume = True
            self.set_vol((pos[0] - self.volume_rect.left) / self.volume_rect.width)
            return
        for name, r in self.btn.items():
            if r.collidepoint(pos):
                self._click_btn(name)
                return
        for name, r in self.pl_btn.items():
            if r.collidepoint(pos):
                self._click_btn(name)
                return
        if self.eq_rect.collidepoint(pos):
            self.eq_on = not self.eq_on
            return
        if self.pl_rect.collidepoint(pos):
            self.pl_on = not self.pl_on
            return
        idx = self._pl_idx(pos)
        if idx is not None:
            now = time.perf_counter()
            self.sel = idx
            self.ensure_vis()
            if self.last_click_idx == idx and now - self.last_pl_click < 0.35:
                self.play_idx(idx)
            self.last_click_idx = idx
            self.last_pl_click = now

    @staticmethod
    def _scaled(pos: tuple[int, int]) -> tuple[int, int]:
        return (pos[0] // SCALE, pos[1] // SCALE)

    def handle_event(self, ev: pygame.event.Event) -> None:
        if ev.type == pygame.QUIT:
            self.running = False
        elif ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_SPACE:
                self.toggle_pause()
            elif ev.key == pygame.K_RETURN and self.sel is not None:
                self.play_idx(self.sel)
            elif ev.key == pygame.K_o:
                self.open_dialog()
            elif ev.key == pygame.K_DELETE:
                self.remove_sel()
            elif ev.key == pygame.K_UP and self.tracks:
                self.sel = max(0, (self.sel or 1) - 1) if self.sel is not None else 0
                self.ensure_vis()
            elif ev.key == pygame.K_DOWN and self.tracks:
                self.sel = min(len(self.tracks) - 1, (self.sel if self.sel is not None else -1) + 1)
                self.ensure_vis()
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            pos = self._scaled(ev.pos)
            if ev.button == 1:
                self._mouse_down(pos)
            elif ev.button in (4, 5):
                if self.tracks:
                    vis = self._vis_rows()
                    mx = max(0, len(self.tracks) - vis)
                    self.scroll_off = int(clamp(self.scroll_off + (-1 if ev.button == 4 else 1), 0, mx))
        elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            self.dragging_seek = self.dragging_volume = False
        elif ev.type == pygame.MOUSEMOTION:
            pos = self._scaled(ev.pos)
            if self.dragging_seek:
                self.seek_ratio((pos[0] - self.seek_rect.left) / self.seek_rect.width)
            if self.dragging_volume:
                self.set_vol((pos[0] - self.volume_rect.left) / self.volume_rect.width)
        elif ev.type == pygame.DROPFILE:
            self.add_files([ev.file])
        elif ev.type == MUSIC_END_EVENT and not self.stopped and not self.paused:
            self.next_track(auto=True)

    # ======================================================================
    # Drawing
    # ======================================================================

    def _draw_main_frame(self) -> None:
        bevel(self.screen, pygame.Rect(0, 0, WIDTH, MAIN_HEIGHT), fill=FRAME_BG, n=2)

    def _draw_titlebar(self) -> None:
        bar = pygame.Rect(3, 3, WIDTH - 6, 13)
        hgradient(self.screen, bar, TITLE_L, TITLE_R)
        grip_lines(self.screen, bar.left + 2, bar.left + 28, bar.top + 3, bar.bottom - 4)
        lbl = self.f9.render("PythonAMP", True, (255, 255, 255))
        self.screen.blit(lbl, (bar.left + 32, bar.top + 1))
        grip_lines(self.screen, bar.left + 76, bar.right - 34, bar.top + 3, bar.bottom - 4)
        bw, bh = 9, 9
        by = bar.top + 2
        for i, (ch, c) in enumerate([("_", BTN_TEXT), ("\u25A0", BTN_TEXT), ("X", (255, 80, 80))]):
            bx = bar.right - (3 - i) * (bw + 1)
            br = pygame.Rect(bx, by, bw, bh)
            bevel(self.screen, br, fill=(0, 0, 66), light=(50, 50, 100), dark=(0, 0, 30), n=1)
            s = self.f8.render(ch, True, c)
            self.screen.blit(s, s.get_rect(center=br.center))

    def _draw_display(self) -> None:
        dr = self.display_rect
        outer = dr.inflate(4, 4)
        bevel(self.screen, outer, fill=FRAME_BG, pressed=True, n=1)
        pygame.draw.rect(self.screen, LCD_BG, dr)

        playing = not self.stopped and not self.paused

        # Play-status icon
        sx, sy = dr.left + 6, dr.top + 4
        if playing:
            pygame.draw.polygon(self.screen, LCD_GREEN, [(sx, sy), (sx, sy + 8), (sx + 7, sy + 4)])
        elif self.paused:
            pygame.draw.rect(self.screen, LCD_GREEN, (sx, sy, 3, 8))
            pygame.draw.rect(self.screen, LCD_GREEN, (sx + 5, sy, 3, 8))
        else:
            pygame.draw.rect(self.screen, LCD_DIM, (sx, sy, 8, 8))

        # LED time
        led_time(self.screen, dr.left + 18, dr.top + 7, fmt_time(self.cur_pos()), LCD_GREEN, LCD_DIM)

        # Spectrum analyzer / VU bars
        vu_x, vu_y, vu_h = dr.left + 6, dr.top + 18, 20
        t = time.perf_counter()
        for bi in range(19):
            if playing:
                h = int(vu_h * (0.2 + 0.8 * abs(math.sin(t * 2.7 + bi * 0.73) * math.cos(t * 1.3 + bi * 1.1))))
            else:
                h = 1
            bx = vu_x + bi * 4
            for py in range(h):
                gy = vu_y + vu_h - 1 - py
                if py > vu_h * 0.8:
                    c = (220, 30, 30)
                elif py > vu_h * 0.6:
                    c = (220, 220, 0)
                else:
                    c = (0, 190, 0)
                pygame.draw.line(self.screen, c, (bx, gy), (bx + 2, gy))

        # Scrolling song title
        tx = dr.left + 90
        ty = dr.top + 5
        tw = dr.right - tx - 4
        track = self.cur_track()
        title = track.title if track else "  ***  PythonAmp Classic  ***  "
        title_s = self.f_lcd.render(title + "   ***   ", True, LCD_GREEN)
        sw = title_s.get_width()
        old_clip = self.screen.get_clip()
        self.screen.set_clip(pygame.Rect(tx, ty, tw, 13))
        if sw > tw:
            off = int(self.scroll_x) % sw
            self.screen.blit(title_s, (tx - off, ty))
            self.screen.blit(title_s, (tx - off + sw, ty))
        else:
            self.screen.blit(title_s, (tx, ty))
        self.screen.set_clip(old_clip)

        # Bitrate / sample rate
        iy = dr.top + 22
        if track and (track.bitrate or track.sample_rate):
            info = f"{track.bitrate // 1000} kbps  {track.sample_rate // 1000} kHz"
        else:
            info = "--- kbps  -- kHz"
        self.screen.blit(self.f_lcd.render(info, True, (0, 200, 0)), (tx, iy))

        # Stereo / Mono
        sy2 = dr.top + 34
        self.screen.blit(self.f8.render("MONO", True, LCD_DIM), (tx, sy2))
        self.screen.blit(self.f8.render("STEREO", True, LCD_GREEN), (tx + 38, sy2))

    def _draw_seek(self) -> None:
        groove(self.screen, self.seek_rect)
        t = self.cur_track()
        pos = self.cur_pos()
        r = pos / t.duration if t and t.duration > 0 else 0.0
        inner = self.seek_rect.inflate(-4, -4)
        fw = int(inner.width * clamp(r, 0, 1))
        if fw:
            pygame.draw.rect(self.screen, GROOVE_FILL, (inner.left, inner.top, fw, inner.height))
        tx = inner.left + fw
        thumb = pygame.Rect(tx - 4, self.seek_rect.top - 1, 9, self.seek_rect.height + 2)
        bevel(self.screen, thumb, fill=THUMB_FACE, light=THUMB_LIGHT, dark=THUMB_DARK, n=1)

    def _draw_slider(self, rect: pygame.Rect, ratio: float, label: str) -> None:
        lbl = self.f8.render(label, True, BTN_TEXT)
        self.screen.blit(lbl, (rect.left - lbl.get_width() - 3, rect.top))
        groove(self.screen, rect)
        inner = rect.inflate(-4, -4)
        fw = int(inner.width * clamp(ratio, 0, 1))
        if fw:
            pygame.draw.rect(self.screen, GROOVE_FILL, (inner.left, inner.top, fw, inner.height))
        tx = inner.left + fw
        thumb = pygame.Rect(tx - 3, rect.top - 1, 7, rect.height + 2)
        bevel(self.screen, thumb, fill=THUMB_FACE, light=THUMB_LIGHT, dark=THUMB_DARK, n=1)

    def _draw_eq_pl(self) -> None:
        for rect, label, on in [(self.eq_rect, "EQ", self.eq_on), (self.pl_rect, "PL", self.pl_on)]:
            fill = TOGGLE_ON if on else BTN_FACE
            bevel(self.screen, rect, fill=fill, light=BTN_LIGHT, dark=BTN_DARK, n=1)
            c = (255, 255, 255) if on else BTN_TEXT
            s = self.f8.render(label, True, c)
            self.screen.blit(s, s.get_rect(center=rect.center))

    def _draw_transport(self) -> None:
        for name, rect in self.btn.items():
            bevel(self.screen, rect, fill=BTN_FACE, light=BTN_LIGHT, dark=BTN_DARK, n=1)
            cx, cy = rect.centerx, rect.centery
            ic = BTN_TEXT
            if name == "prev":
                pygame.draw.rect(self.screen, ic, (cx - 6, cy - 4, 2, 9))
                pygame.draw.polygon(self.screen, ic, [(cx + 4, cy - 4), (cx + 4, cy + 4), (cx - 3, cy)])
            elif name == "play":
                pygame.draw.polygon(self.screen, ic, [(cx - 4, cy - 5), (cx - 4, cy + 5), (cx + 5, cy)])
            elif name == "pause":
                pygame.draw.rect(self.screen, ic, (cx - 4, cy - 4, 3, 9))
                pygame.draw.rect(self.screen, ic, (cx + 1, cy - 4, 3, 9))
            elif name == "stop":
                pygame.draw.rect(self.screen, ic, (cx - 4, cy - 4, 9, 9))
            elif name == "next":
                pygame.draw.polygon(self.screen, ic, [(cx - 4, cy - 4), (cx - 4, cy + 4), (cx + 3, cy)])
                pygame.draw.rect(self.screen, ic, (cx + 4, cy - 4, 2, 9))
            elif name == "open":
                pygame.draw.polygon(self.screen, ic, [(cx - 4, cy - 1), (cx + 4, cy - 1), (cx, cy - 6)])
                pygame.draw.rect(self.screen, ic, (cx - 4, cy + 1, 9, 2))

    def _draw_playlist(self) -> None:
        pf = pygame.Rect(0, MAIN_HEIGHT, WIDTH, PLAYLIST_HEIGHT)
        bevel(self.screen, pf, fill=FRAME_BG, n=2)

        hdr = pygame.Rect(3, MAIN_HEIGHT + 3, WIDTH - 6, 14)
        hgradient(self.screen, hdr, TITLE_L, TITLE_R)
        grip_lines(self.screen, hdr.left + 2, hdr.left + 18, hdr.top + 3, hdr.bottom - 4)
        self.screen.blit(self.f9.render("PLAYLIST EDITOR", True, (255, 255, 255)), (hdr.left + 22, hdr.top + 1))
        grip_lines(self.screen, hdr.left + 130, hdr.right - 4, hdr.top + 3, hdr.bottom - 4)

        bevel(self.screen, self.pl_list, fill=FRAME_BG, pressed=True, n=1)
        inner = self.pl_list.inflate(-4, -4)
        pygame.draw.rect(self.screen, PL_BG, inner)

        if not self.tracks:
            self.screen.blit(self.f10.render("No songs loaded.", True, PL_TEXT), (inner.left + 8, inner.top + 10))
            self.screen.blit(self.f8.render("Press + ADD or drag files to add music.", True, LCD_DIM),
                             (inner.left + 8, inner.top + 28))
        else:
            vis = self._vis_rows()
            top = self.scroll_off
            bot = min(len(self.tracks), top + vis)
            for row, idx in enumerate(range(top, bot)):
                ry = inner.top + row * 16
                rr = pygame.Rect(inner.left, ry, inner.width, 16)
                sel = idx == self.sel
                cur = idx == self.cur and not self.stopped
                if sel:
                    pygame.draw.rect(self.screen, PL_SEL_BG, rr)
                pfx = "\u25B6" if cur else " "
                txt = f"{pfx} {idx + 1}. {truncate(self.tracks[idx].title, 28)}"
                dur = fmt_time(self.tracks[idx].duration)
                c = PL_SEL_TEXT if sel else (PL_CUR_TEXT if cur else PL_TEXT)
                self.screen.blit(self.f10.render(txt, True, c), (rr.left + 4, rr.top + 1))
                ds = self.f10.render(dur, True, c)
                self.screen.blit(ds, (rr.right - ds.get_width() - 4, rr.top + 1))

        for name, rect in self.pl_btn.items():
            bevel(self.screen, rect, fill=BTN_FACE, light=BTN_LIGHT, dark=BTN_DARK, n=1)
            t = {"add": "+ ADD", "clear": "CLR"}.get(name, name)
            s = self.f8.render(t, True, BTN_TEXT)
            self.screen.blit(s, s.get_rect(center=rect.center))

        total = sum(t.duration for t in self.tracks)
        info = f"{len(self.tracks)} tracks  [{fmt_time(total)}]"
        self.screen.blit(self.f8.render(info, True, BTN_TEXT), (WIDTH - 100, HEIGHT - 13))

    def draw(self) -> None:
        self.screen.fill((0, 0, 0))
        self.scroll_x += 30.0 / max(1, FPS)
        self._draw_main_frame()
        self._draw_titlebar()
        self._draw_display()
        self._draw_seek()
        self._draw_slider(self.volume_rect, self.volume, "VOL")
        self._draw_slider(self.balance_rect, 0.5, "BAL")
        self._draw_eq_pl()
        self._draw_transport()
        self._draw_playlist()

    def run(self) -> None:
        while self.running:
            for ev in pygame.event.get():
                self.handle_event(ev)
            self.draw()
            pygame.transform.scale(self.screen, (WIDTH * SCALE, HEIGHT * SCALE), self.window)
            pygame.display.flip()
            self.clock.tick(FPS)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PythonAmp Classic")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    pygame.mixer.pre_init(44100, -16, 2, 2048)
    pygame.init()
    try:
        pygame.mixer.init()
    except pygame.error as exc:
        print(f"Audio init failed: {exc}", file=sys.stderr)
        return 1

    app = PlayerApp()
    try:
        if args.self_test:
            app.draw()
            pygame.transform.scale(app.screen, (WIDTH * SCALE, HEIGHT * SCALE), app.window)
            pygame.display.flip()
            return 0
        app.run()
    finally:
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
