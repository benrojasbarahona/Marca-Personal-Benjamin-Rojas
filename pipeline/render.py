#!/usr/bin/env python3
"""Renderiza un proyecto de proyectos/<nombre>/ con ffmpeg.

Uso: python pipeline/render.py <nombre-proyecto>
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}
POSICIONES = {
    "arriba-derecha": ("W-w-{m}", "{m}"),
    "arriba-izquierda": ("{m}", "{m}"),
    "abajo-derecha": ("W-w-{m}", "H-h-{m}"),
    "abajo-izquierda": ("{m}", "H-h-{m}"),
}


def run(cmd):
    cmd = [str(c) for c in cmd]
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def leer_toml(p):
    if not p.exists():
        sys.exit(f"Falta {p}")
    with open(p, "rb") as f:
        return tomllib.load(f)


def resolver(ruta, proyecto):
    """Ruta relativa al proyecto; si no existe ahí, relativa a la raíz del repo."""
    for base in (proyecto, RAIZ):
        p = (base / ruta).resolve()
        if p.exists():
            return p
    sys.exit(f"No se encuentra '{ruta}' (se buscó en {proyecto} y en {RAIZ})")


def clave_natural(p):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", p.name)]


def tiene_audio(p):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(p)],
        capture_output=True, text=True)
    return bool(r.stdout.strip())


def duracion(p):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def tiene_libass():
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(l.split()[1:2] == ["ass"] for l in r.stdout.splitlines())


def ruta_filtro(p):
    """Escapa una ruta para usarla entre comillas como opción de un filtro de ffmpeg."""
    return str(p).replace("\\", "/").replace("'", r"'\''").replace(":", r"\:")


# ---------- unir clips ----------

def normalizar(clip, salida, w, h, fps):
    """Deja el clip con tamaño, fps, códec y audio uniformes (agrega silencio si no tiene audio)."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p")
    cmd = ["ffmpeg", "-y", "-i", clip]
    if tiene_audio(clip):
        # apad + -shortest: el audio dura lo mismo que el video y no se desfasa al unir
        cmd += ["-map", "0:v:0", "-map", "0:a:0", "-af", "apad"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-shortest", "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", salida]
    run(cmd)


def unir(clips, tmp, w, h, fps):
    norm = []
    for i, c in enumerate(clips):
        out = tmp / f"n{i:03d}.mp4"
        normalizar(c, out, w, h, fps)
        norm.append(out)
    lista = tmp / "lista.txt"
    lista.write_text("".join(f"file '{n}'\n" for n in norm), encoding="utf-8")
    base = tmp / "base.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lista, "-c", "copy", base])
    return base


# ---------- subtítulos ----------

def t_srt(t):
    ms = round(t * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def t_ass(t):
    cs = round(t * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def hex_ass(color):
    c = color.lstrip("#")
    return f"&H00{c[4:6]}{c[2:4]}{c[0:2]}".upper()  # ASS usa BGR


def parse_srt(texto):
    segs = []
    patron = r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)"
    for bloque in re.split(r"\n\s*\n", texto.strip().replace("\r\n", "\n")):
        lineas = bloque.split("\n")
        for i, linea in enumerate(lineas):
            m = re.match(patron, linea.strip())
            if m:
                g = list(map(int, m.groups()))
                segs.append({
                    "start": g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                    "end": g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                    "text": " ".join(lineas[i + 1:]).strip(),
                    "words": None,
                })
                break
    return segs


def a_srt(segs):
    return "\n".join(
        f"{i}\n{t_srt(s['start'])} --> {t_srt(s['end'])}\n{s['text']}\n"
        for i, s in enumerate(segs, 1))


def segmentos_de_whisper(data):
    segs = []
    for s in data["segments"]:
        words = [{"w": w["word"].strip(), "start": w["start"], "end": w["end"]}
                 for w in s.get("words", []) if w["word"].strip()]
        segs.append({"start": s["start"], "end": s["end"],
                     "text": s["text"].strip(), "words": words or None})
    return segs


def cargar_segmentos(proyecto, base, tmp, salida_dir, sub):
    """Prioridad: media/subtitulos.srt > media/subtitulos.json > transcribir con Whisper."""
    srt = proyecto / "media" / "subtitulos.srt"
    js = proyecto / "media" / "subtitulos.json"
    if srt.exists():
        print(f"Usando {srt}")
        return parse_srt(srt.read_text(encoding="utf-8-sig"))
    if js.exists():
        print(f"Usando {js}")
        return segmentos_de_whisper(json.loads(js.read_text(encoding="utf-8")))
    if shutil.which("whisper") is None:
        sys.exit("No hay subtitulos.srt/json en media/ y Whisper no está instalado "
                 "(pip install -r requirements.txt).")
    run(["whisper", base, "--language", sub.get("idioma", "es"),
         "--model", sub.get("modelo", "small"), "--fp16", "False",
         "--word_timestamps", "True", "--output_format", "json", "--output_dir", tmp])
    bruto = tmp / (base.stem + ".json")
    segs = segmentos_de_whisper(json.loads(bruto.read_text(encoding="utf-8")))
    # se dejan en salida/ para que el usuario los revise, corrija y los copie a media/
    shutil.copy(bruto, salida_dir / "subtitulos.json")
    (salida_dir / "subtitulos.srt").write_text(a_srt(segs), encoding="utf-8")
    return segs


def repartir(seg):
    """Sin tiempos por palabra: reparte la duración del segmento según el largo de cada palabra."""
    ws = seg["text"].split()
    if not ws:
        return []
    total = sum(len(w) for w in ws)
    dur = seg["end"] - seg["start"]
    t, out = seg["start"], []
    for w in ws:
        d = dur * len(w) / total
        out.append({"w": w, "start": t, "end": t + d})
        t += d
    return out


def agrupar(segs, n):
    eventos = []
    for s in segs:
        words = s["words"] or repartir(s)
        for i in range(0, len(words), n):
            g = words[i:i + n]
            eventos.append((g[0]["start"], g[-1]["end"], " ".join(w["w"] for w in g)))
    return eventos


def limpiar(txt):
    return txt.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def construir_ass(segs, estilo, w, h, destino):
    caja = estilo.get("modo", "contorno") == "caja"
    color = hex_ass(estilo.get("color", "#FFFFFF"))
    if caja:
        # BorderStyle 3: libass dibuja un rectángulo de color OutlineColour; Outline es el relleno
        fondo = hex_ass(estilo.get("color_caja", "#1A1A1A"))
        borde, grosor = 3, estilo.get("relleno_caja", 12)
        contorno = respaldo = fondo
        eventos = agrupar(segs, estilo.get("palabras_por_bloque", 2))
    else:
        borde, grosor = 1, estilo.get("grosor_contorno", 3)
        contorno = hex_ass(estilo.get("color_contorno", "#000000"))
        respaldo = "&H00000000"
        eventos = [(s["start"], s["end"], s["text"]) for s in segs]
    cabecera = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{estilo.get('fuente_nombre', 'Arimo')},{estilo.get('tamano', 64)},"
        f"{color},&H000000FF,{contorno},{respaldo},{-1 if estilo.get('negrita') else 0},"
        f"0,0,0,100,100,0,0,{borde},{grosor},0,2,60,60,{estilo.get('margen_vertical', 70)},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lineas = [f"Dialogue: 0,{t_ass(a)},{t_ass(b)},Default,,0,0,0,,{limpiar(txt)}"
              for a, b, txt in eventos]
    destino.write_text(cabecera + "\n".join(lineas) + "\n", encoding="utf-8")


# ---------- render ----------

def render(nombre):
    proyecto = RAIZ / "proyectos" / nombre
    if not proyecto.is_dir():
        sys.exit(f"No existe {proyecto}")
    cfg = leer_toml(proyecto / "config.toml")
    estilos = leer_toml(proyecto / "estilos.toml")
    w, h = cfg["salida"]["ancho"], cfg["salida"]["alto"]
    fps = cfg["salida"].get("fps", 30)
    salida_dir = proyecto / "salida"
    salida_dir.mkdir(exist_ok=True)

    media = proyecto / "media"
    orden = cfg.get("clips", {}).get("orden")
    if orden:
        clips = [media / n for n in orden]
    else:
        clips = sorted((p for p in media.iterdir() if p.suffix.lower() in VIDEO_EXT),
                       key=clave_natural)
    faltan = [c for c in clips if not c.exists()]
    if faltan:
        sys.exit(f"Faltan videos: {faltan}")
    if not clips:
        sys.exit(f"No hay videos en {media}")
    print(f"Proyecto {nombre}: {len(clips)} video(s)")
    for i, c in enumerate(clips, 1):
        print(f"  {i}. {c.name}")

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        base = unir(clips, tmp, w, h, fps)
        entradas, filtros, vid = [["-i", base]], [], "[0:v]"

        b = cfg.get("banner")
        if b and b.get("archivo"):
            # -loop 1: la imagen se repite durante todo el video (si no, desaparece a los pocos segundos)
            entradas.append(["-loop", "1", "-i", resolver(b["archivo"], proyecto)])
            m = b.get("margen", 40)
            x, y = (e.format(m=m) for e in POSICIONES[b.get("posicion", "arriba-derecha")])
            filtros += [f"[{len(entradas) - 1}:v]scale={b.get('ancho', 260)}:-1[bn]",
                        f"{vid}[bn]overlay={x}:{y}:shortest=1[vb]"]
            vid = "[vb]"

        sub = cfg.get("subtitulos")
        if sub and sub.get("activar", True):
            nombre_estilo = sub.get("estilo", "cine")
            if nombre_estilo not in estilos:
                sys.exit(f"El estilo '{nombre_estilo}' no está en estilos.toml "
                         f"(hay: {', '.join(estilos)})")
            segs = cargar_segmentos(proyecto, base, tmp, salida_dir, sub)
            if not tiene_libass():
                sys.exit("Este ffmpeg no tiene libass y no puede dibujar subtítulos. "
                         "Use GitHub Actions, instale un ffmpeg con libass "
                         "(en macOS: brew install ffmpeg-full) o ponga activar = false.")
            ass = tmp / "subs.ass"
            construir_ass(segs, estilos[nombre_estilo], w, h, ass)
            fuentes = proyecto / "fuentes"
            extra = f":fontsdir='{ruta_filtro(fuentes)}'" if fuentes.is_dir() else ""
            filtros.append(f"{vid}ass='{ruta_filtro(ass)}'{extra}[vs]")
            vid = "[vs]"

        audio_map = ["-map", "0:a"]
        a = cfg.get("audio")
        if a and a.get("musica"):
            entradas.append(["-stream_loop", "-1", "-i", resolver(a["musica"], proyecto)])
            j = len(entradas) - 1
            vo = a.get("volumen_original", 1.0)
            vm = a.get("volumen_musica", 0.3)
            # normalize=0: amix no reparte el volumen entre entradas; alimiter evita que sature
            # (límite -1 dB para dejar margen al codificar en AAC)
            filtros += [f"[0:a]volume={vo}[a0]", f"[{j}:a]volume={vm}[a1]",
                        "[a0][a1]amix=inputs=2:duration=first:normalize=0,"
                        "alimiter=limit=0.89:level=0[aout]"]
            audio_map = ["-map", "[aout]"]

        destino = salida_dir / cfg["salida"]["nombre"]
        cmd = ["ffmpeg", "-y"]
        for e in entradas:
            cmd += e
        if filtros:
            cmd += ["-filter_complex", ";".join(filtros)]
        mapv = "0:v" if vid == "[0:v]" else vid
        cmd += ["-map", mapv, *audio_map, "-t", f"{duracion(base):.3f}"]
        if vid == "[0:v]":
            cmd += ["-c:v", "copy"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", destino]
        run(cmd)

    print(f"\nListo: {destino} ({destino.stat().st_size / 1024**2:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not re.fullmatch(r"[\w\-]+", sys.argv[1]):
        sys.exit("Uso: python pipeline/render.py <nombre-proyecto>")
    render(sys.argv[1])
