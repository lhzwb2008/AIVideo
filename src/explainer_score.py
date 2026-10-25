"""自动配乐：按每页能量合成节拍和和声，按画面事件卡音效，人声出来时压低音乐，最后统一响度。"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

from paths import ffmpeg_executable

SR = 44100

# 和弦：(根音 MIDI, 和声音符)，每两小节换一个
MOODS = {
    "hype": {"bpm": 124, "chords": [(45, [57, 60, 64, 67]), (41, [57, 60, 65, 69]), (48, [55, 60, 64, 67]), (43, [55, 59, 62, 67])]},
    "epic": {"bpm": 96, "chords": [(38, [50, 53, 57, 62]), (46, [50, 53, 58, 62]), (41, [48, 53, 57, 60]), (48, [48, 52, 55, 60])]},
    "tense": {"bpm": 112, "chords": [(40, [52, 55, 59, 64]), (40, [52, 55, 60, 64]), (41, [53, 57, 60, 65]), (39, [51, 55, 58, 63])]},
    "warm": {"bpm": 92, "chords": [(48, [55, 60, 64, 67]), (45, [57, 60, 64, 69]), (41, [57, 60, 65, 69]), (43, [55, 59, 62, 67])]},
}

# 能量 → (底鼓型, 踩镲, 拍手, 贝斯, 琶音, 铺底亮度)
LEVELS = {
    0: (None, False, False, False, False, 3),
    1: ("heart", False, False, False, False, 3),
    2: ("heart", False, False, False, True, 4),
    3: ("four", True, False, True, True, 5),
    4: ("four", True, True, True, True, 6),
    5: ("four", "open", True, True, True, 8),
}


def _filt(x, lo=None, hi=None):
    n = len(x)
    X = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(n, 1 / SR)
    g = np.ones_like(f)
    if lo:
        g *= 1 / np.sqrt(1 + (lo / np.maximum(f, 1)) ** 4)
    if hi:
        g *= 1 / np.sqrt(1 + (f / hi) ** 4)
    if X.ndim == 2:
        g = g[:, None]
    return np.fft.irfft(X * g, n, axis=0).astype(np.float32)


def _norm(x):
    return x / (np.abs(x).max() + 1e-6)


def _hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


class _Bus:
    def __init__(self, seconds: float):
        self.N = int(SR * seconds)
        self.rng = np.random.default_rng(7)

    def buf(self):
        return np.zeros((self.N, 2), np.float32)

    def place(self, dst, sig, t, gain=1.0, pan=0.0):
        i = int(t * SR)
        if i >= self.N or i + len(sig) <= 0:
            return
        s = sig if sig.ndim == 2 else np.stack([sig * np.sqrt(.5 * (1 - pan)), sig * np.sqrt(.5 * (1 + pan))], 1)
        j0, j1 = max(0, -i), min(len(s), self.N - i)
        dst[i + j0:i + j1] += s[j0:j1] * gain

    def noise(self, n):
        return self.rng.normal(0, 1, n).astype(np.float32)


class _Synth:
    def __init__(self, bus: _Bus):
        self.b = bus

    def _t(self, dur):
        return np.arange(int(dur * SR)) / SR

    def kick(self):
        t = self._t(.45)
        ph = 2 * np.pi * np.cumsum(44 + 110 * np.exp(-t * 32)) / SR
        s = np.sin(ph) * np.exp(-t * 6.5) + .25 * np.sin(ph * 2) * np.exp(-t * 30) + self.b.noise(len(t)) * np.exp(-t * 400) * .25
        return np.tanh(s * 1.6).astype(np.float32) * .8

    def clap(self):
        t = self._t(.35)
        e = np.exp(-t * 16)
        for d in (0, .011, .022):
            e += np.where(t >= d, np.exp(-(t - d) * 120), 0) * .5
        return (_norm(_filt(self.b.noise(len(t)), 900, 7000)) * e * .55 + np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30) * .3).astype(np.float32)

    def hat(self, open_=False):
        t = self._t(.25 if open_ else .05)
        return (_norm(_filt(self.b.noise(len(t)), 7000)) * np.exp(-t * (14 if open_ else 70))).astype(np.float32)

    def pad(self, f, dur, bright):
        t = self._t(dur)
        out = np.zeros((len(t), 2), np.float32)
        for det, side in ((.997, -.6), (1.003, .6), (1, 0)):
            s = sum(np.sin(2 * np.pi * f * det * k * t + k * .7) / k ** 1.25 for k in range(1, bright + 1))
            out[:, 0] += s * np.sqrt(.5 * (1 - side))
            out[:, 1] += s * np.sqrt(.5 * (1 + side))
        e = np.minimum(1, t / .9) * np.minimum(1, np.maximum(0, dur - t) / 1.2)
        return out * e[:, None] * .33

    def pluck(self, f, dur=.5):
        t = self._t(dur)
        s = np.sin(2 * np.pi * f * t) + .3 * np.sin(4 * np.pi * f * t) + .08 * np.sin(6 * np.pi * f * t)
        return (s * np.exp(-t * 8.5) * np.minimum(1, t / .003)).astype(np.float32)

    def bass(self, f, dur):
        t = self._t(dur)
        s = np.sin(2 * np.pi * f * t) + .18 * np.sin(4 * np.pi * f * t)
        return (s * np.minimum(1, t / .005) * np.clip((dur - t) / .03, 0, 1)).astype(np.float32)

    def riser(self, dur):
        t = self._t(dur)
        sw = np.sin(2 * np.pi * np.cumsum(200 + 1800 * (t / dur) ** 2) / SR) * .15
        return ((_norm(_filt(self.b.noise(len(t)), 1500, 12000)) * .6 + sw) * (t / dur) ** 2.2).astype(np.float32)

    def boom(self):
        t = self._t(2.6)
        b = np.sin(2 * np.pi * np.cumsum(32 + 60 * np.exp(-t * 6)) / SR) * np.exp(-t * 2.2)
        return np.tanh((b * 1.2 + _norm(_filt(self.b.noise(len(t)), 200, 6000)) * np.exp(-t * 5) * .35) * 1.3).astype(np.float32) * .85

    def thump(self):
        t = self._t(.5)
        b = np.sin(2 * np.pi * np.cumsum(50 + 90 * np.exp(-t * 18)) / SR) * np.exp(-t * 9)
        return np.tanh(b * 1.5 + _norm(_filt(self.b.noise(len(t)), 300, 4000)) * np.exp(-t * 40) * .3).astype(np.float32) * .7

    def whoosh(self, dur=.45, d=1):
        t = self._t(dur)
        u = t / dur
        s = (_norm(_filt(self.b.noise(len(t)), 200, 1500)) * (1 - u) + _norm(_filt(self.b.noise(len(t)), 1500, 9000)) * u) * np.sin(np.pi * u) ** 2
        pan = (u * 2 - 1) * .8 * d
        return np.stack([s * np.sqrt(.5 * (1 - pan)), s * np.sqrt(.5 * (1 + pan))], 1).astype(np.float32)

    def tick(self, f=1500, dur=.04):
        t = self._t(dur)
        return (np.sin(2 * np.pi * f * t) * np.exp(-t * 120)).astype(np.float32)

    def bell(self, f=1318.5, dur=1.4):
        t = self._t(dur)
        s = np.sin(2 * np.pi * f * t) + .45 * np.sin(2 * np.pi * f * 2.01 * t) * np.exp(-t * 3) + .2 * np.sin(2 * np.pi * f * 3.02 * t) * np.exp(-t * 6)
        return (s * np.exp(-t * 3.2) * np.minimum(1, t / .002)).astype(np.float32)

    def swish(self, dur=.22):
        t = self._t(dur)
        return (_norm(_filt(self.b.noise(len(t)), 2500, 10000)) * np.sin(np.pi * t / dur) ** 3).astype(np.float32)

    def scratch(self):
        """圆珠笔划纸。"""
        t = self._t(.18)
        return (_norm(_filt(self.b.noise(len(t)), 2000, 6000)) * np.sin(np.pi * t / .18) ** 2 * (.6 + .4 * np.sin(t * 90))).astype(np.float32)


def _reverb(x, rng, secs=2.4, mix=.3):
    n = int(secs * SR)
    t = np.arange(n) / SR
    ir = _filt(rng.normal(0, 1, (n, 2)).astype(np.float32) * np.exp(-t * 3.2)[:, None], 150, 6500)
    ir /= np.sqrt((ir ** 2).sum(0, keepdims=True))
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, size, axis=0) * np.fft.rfft(ir, size, axis=0), size, axis=0)[:len(x)]
    return (x * (1 - mix) + wet.astype(np.float32) * mix * .9).astype(np.float32)


def _music(bus: _Bus, sy: _Synth, total: float, pages: list[dict], mood: str, drops: list[float]) -> np.ndarray:
    m = MOODS.get(mood, MOODS["hype"])
    beat = 60 / m["bpm"]
    seg = 8 * beat

    def level(t):
        for p in pages:
            if p["start"] <= t < p["end"]:
                return LEVELS[p.get("energy", 3)]
        return LEVELS[2]

    def chord(t):
        return m["chords"][int(t // seg) % 4]

    drums, bass, pad, arp, fx = bus.buf(), bus.buf(), bus.buf(), bus.buf(), bus.buf()
    K, C, HC, HO = sy.kick(), sy.clap(), sy.hat(), sy.hat(True)
    kicks = []
    # 高潮前半拍让鼓停一下，落点更狠
    gaps = [(d - beat * .9, d) for d in drops]
    for b in range(int(total / beat) + 1):
        t = b * beat
        if any(a <= t < z for a, z in gaps):
            continue
        kp, hats, clap, _, _, _ = level(t)
        bib = b % 4
        if kp == "four" or (kp == "heart" and bib == 0):
            mx = .55 if kp == "heart" else .9
            bus.place(drums, K, t, mx)
            kicks.append(t)
            if kp == "heart":
                bus.place(drums, K, t + .22, .3)
        if clap and bib in (1, 3):
            bus.place(drums, C, t, .42)
        if hats:
            bus.place(drums, HC, t + beat / 2, .16, pan=.3)
            bus.place(drums, HC, t + beat * .25, .06, pan=-.3)
            if hats == "open" and bib == 3:
                bus.place(drums, HO, t + beat / 2, .09, pan=.2)
    for d in drops:
        ft = d - 2 * beat
        for i in range(8):
            bus.place(drums, C, ft + i * beat / 4, .1 + .035 * i, pan=(-1) ** i * .3)
        bus.place(fx, sy.riser(1.6), d - 1.6, .2)
    for k in range(int(total / seg) + 1):
        t0 = k * seg
        if t0 >= total - .5:
            break
        for n in chord(t0 + .01)[1]:
            bus.place(pad, sy.pad(_hz(n), seg + 1.2, level(t0 + .01)[5]), t0, .07)
    for i in range(int(total / (beat / 2)) + 1):
        t = i * beat / 2
        if not level(t)[3]:
            continue
        root = chord(t)[0]
        off = i % 2 == 1
        bus.place(bass, sy.bass(_hz(root - 12 + (12 if off else 0)), beat / 2 * .9), t, .24 if off else .3)
    pattern = [0, 2, 3, 1, 2, 3, 1, 0]
    for i in range(int(total / (beat / 4)) + 1):
        t = i * beat / 4
        if not level(t)[4]:
            continue
        notes = chord(t)[1]
        bus.place(arp, sy.pluck(_hz(notes[pattern[i % 8]] + 12)), t, .05 if i % 4 else .075, pan=.35 * np.sin(i * .7))
    d = int(beat * .75 * SR)
    echo = np.zeros_like(arp)
    echo[d:, 0] = arp[:-d, 1] * .38
    echo[2 * d:, 1] = arp[:-2 * d, 0] * .22
    arp += echo
    sc = np.ones(bus.N, np.float32)
    for t in kicks:
        i = int(t * SR)
        j = min(bus.N, i + int(.28 * SR))
        sc[i:j] = np.minimum(sc[i:j], 1 - .55 * np.exp(-np.arange(j - i) / SR * 14))
    pad *= sc[:, None]
    bass *= sc[:, None]
    arp *= (.5 + .5 * sc)[:, None]
    wet = _reverb(pad + arp * .8 + drums * .12, bus.rng, 2.6, .35)
    return _filt(wet + drums * .88 + bass + fx, 28, 16000)


def _sfx(bus: _Bus, sy: _Synth, events: list[dict]) -> np.ndarray:
    s = bus.buf()
    for e in events:
        t, k = float(e["t"]), e["k"]
        if k == "whoosh":
            bus.place(s, sy.whoosh(.5, e.get("d", 1)), t - .25, .3)
        elif k == "boom":
            bus.place(s, sy.boom(), t, .8)
        elif k == "thump":
            bus.place(s, sy.thump(), t, .45)
        elif k == "pop":
            bus.place(s, sy.tick(1900, .03), t, .07)
        elif k == "tick":
            bus.place(s, sy.tick(1200, .05), t, .09)
        elif k == "swish":
            bus.place(s, sy.swish(.2), t, .1, pan=.2)
        elif k == "scratch":
            bus.place(s, sy.scratch(), t, .05, pan=-.2)
        elif k == "counter":
            tt, step = t, .035
            while tt < t + float(e.get("dur", .8)):
                bus.place(s, sy.tick(2200, .018), tt, .04)
                step *= 1.09
                tt += step
        elif k == "ding":
            bus.place(s, sy.bell(float(e.get("f", 1318.5)), 1.2), t, .11)
        elif k == "chime":
            for i, f in enumerate((1046.5, 1318.5, 1568.0, 2093.0)):
                bus.place(s, sy.bell(f, 1.6), t + i * .09, .09)
        elif k == "shine":
            bus.place(s, sy.swish(.9), t, .07, pan=-.3)
    return _reverb(s, bus.rng, 1.2, .2)


def _decode(path: Path) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_executable(), "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def _duck(vo: np.ndarray) -> np.ndarray:
    win = int(.05 * SR)
    lvl = np.convolve(np.abs(vo), np.ones(win, np.float32) / win, mode="same")
    active = (lvl > .01).astype(np.float32)
    out = np.zeros_like(active)
    g, step = 0.0, 64
    att, rel = 1 - np.exp(-1 / (.06 * SR)), 1 - np.exp(-1 / (.45 * SR))
    for i in range(0, len(active), step):
        target = active[i:i + step].max()
        coef = att if target > g else rel
        g += (target - g) * (1 - (1 - coef) ** step)
        out[i:i + step] = g
    return 1 - .6 * out


def build(*, total: float, pages: list[dict], mood: str, events: list[dict], voices: list[tuple[Path, float]],
          out: Path, music_gain: float = .42) -> Path:
    bus = _Bus(total + 1.5)
    sy = _Synth(bus)
    drops = sorted({round(float(e["t"]), 3) for e in events if e["k"] == "boom" and float(e["t"]) > 2})
    music = _music(bus, sy, total, pages, mood, drops)
    sfx = _sfx(bus, sy, events)
    vo = np.zeros(bus.N, np.float32)
    for path, at in voices:
        d = _decode(path)
        i = int(at * SR)
        j = min(bus.N, i + len(d))
        vo[i:j] += d[:j - i]
    fade = np.ones(bus.N, np.float32)
    fs = int(max(0, total - 1.2) * SR)
    fade[fs:] = np.clip(1 - np.arange(bus.N - fs) / (1.2 * SR), 0, 1) ** 1.5
    fade[:int(.3 * SR)] = np.linspace(0, 1, int(.3 * SR))
    music = music / (np.abs(music).max() + 1e-6) * music_gain * fade[:, None]
    mix = music * _duck(vo)[:, None] + sfx * .9 + np.stack([vo, vo], 1) * .95
    mix = np.tanh(mix * 1.1) / np.tanh(1.1)
    mix = mix[:int(total * SR)]
    raw = out.with_name(out.stem + "_raw.wav")
    with wave.open(str(raw), "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(mix, -1, 1) * 32767).astype(np.int16).tobytes())
    subprocess.run(
        [ffmpeg_executable(), "-v", "error", "-y", "-i", str(raw), "-af", "loudnorm=I=-14:TP=-1.2:LRA=9", "-ar", str(SR), str(out)],
        check=True,
    )
    raw.unlink(missing_ok=True)
    return out
