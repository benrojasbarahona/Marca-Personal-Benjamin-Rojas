# Editor CEE

Pipeline que edita videos en la nube con GitHub Actions. A partir de los videos de un proyecto:

- une los clips en orden,
- pone el banner (logo) en una esquina,
- agrega música de fondo,
- genera subtítulos automáticos con Whisper (estilo `cine` o `tiktok`),

y deja el video listo para descargar desde GitHub. No ocupa disco local; también se puede
ejecutar en el computador.

## Guía rápida

1. Copie la plantilla con el nombre del video nuevo:
   ```bash
   cp -r proyectos/_plantilla proyectos/mi-video
   ```
2. Ponga los videos y el `banner.png` en `proyectos/mi-video/media/`.
3. Revise `proyectos/mi-video/config.toml` (tamaño de salida, música, estilo de subtítulos).
4. Suba los cambios a GitHub (`git add`, `git commit`, `git push`).
5. En GitHub: pestaña **Actions** → **Render** → **Run workflow** → escriba `mi-video`.
6. Cuando termine, descargue el video desde **Artifacts**, al final de la página del run.

## Dónde va cada archivo

| Qué | Dónde | Nota |
|---|---|---|
| Videos del proyecto | `proyectos/<nombre>/media/` | Se usan todos, en orden natural (`video2` antes que `video10`) |
| Banner / logo | `proyectos/<nombre>/media/banner.png` | PNG con fondo transparente; va arriba a la derecha |
| Música | `banco/musica/<pista>.mp3` | Se indica en `config.toml`; se comparte entre proyectos |
| Efectos de sonido | `banco/sonidos/` | Aún sin uso en la v1 |
| Fuentes | `proyectos/<nombre>/fuentes/` | `.ttf` + su licencia |
| Subtítulos corregidos | `proyectos/<nombre>/media/subtitulos.srt` o `.json` | Opcional; evita volver a ejecutar Whisper |
| Video final | `proyectos/<nombre>/salida/` | Se genera solo; no se sube al repo |

## Estructura

```
.github/workflows/render.yml   # workflow "Render" (se lanza a mano)
banco/
  musica/                      # pistas de música compartidas
  sonidos/                     # efectos de sonido (aún sin uso en v1)
proyectos/
  _plantilla/                  # se copia para cada video nuevo
    config.toml                # videos, banner, música y estilo de subtítulos
    estilos.toml               # estilos de subtítulos: cine y tiktok
    fuentes/                   # .ttf del proyecto (+ licencias)
    media/                     # videos, banner.png, (opcional) subtitulos.srt / .json
    salida/                    # resultado (no se versiona)
  bingo-2026/                  # video del bingo del CEE
pipeline/render.py             # el script que hace todo
requirements.txt               # Whisper
```

## Configurar un proyecto (`config.toml`)

```toml
[salida]
nombre = "video.mp4"
ancho = 1920          # marcador: ajuste al formato del video (ej. 1080×1920 para vertical)
alto = 1080
fps = 30

[clips]
# orden = ["video1.mp4", "video2.mp4"]   # opcional; si no, se usan todos en orden natural

[subtitulos]
activar = true
estilo = "cine"       # "cine" o "tiktok"
idioma = "es"
modelo = "small"      # tiny, base, small, medium (más grande = más preciso y más lento)

[banner]
archivo = "media/banner.png"   # "" para no poner banner
posicion = "arriba-derecha"    # arriba-derecha, arriba-izquierda, abajo-derecha, abajo-izquierda
margen = 40                    # px desde el borde
ancho = 260                    # px de ancho del banner

[audio]
musica = ""                    # ej. "banco/musica/tema.mp3"; vacío = sin música
volumen_musica = 0.3
volumen_original = 1.0
```

Las rutas se buscan primero dentro de la carpeta del proyecto y, si no están ahí, desde la raíz
del repo. Si la música es más corta que el video, se repite.

## Subtítulos

El pipeline usa, en este orden:

1. `media/subtitulos.srt`, si existe.
2. `media/subtitulos.json` (formato de Whisper), si existe.
3. Si no hay ninguno, transcribe con Whisper y deja `subtitulos.srt` y `subtitulos.json` en
   `salida/` (vienen dentro del artifact).

**Corregir subtítulos:** descargue el artifact, corrija `subtitulos.srt` (o `subtitulos.json`),
cópielo a `proyectos/<nombre>/media/` y vuelva a ejecutar. Así Whisper no se ejecuta de nuevo.

- La transcripción automática se equivoca con nombres propios y siglas del CEE: conviene
  revisarla siempre.
- Si corrige el `.srt`, el estilo `tiktok` reparte los tiempos de cada palabra de forma
  aproximada (el `.srt` no guarda tiempos por palabra). Si corrige solo el texto en el `.json`,
  se mantienen los tiempos reales.

### Estilos (`estilos.toml`)

Todos los colores, tamaños y márgenes son ajustables.

- **`cine`**: Arimo, color amarillento, contorno negro.
- **`tiktok`**: Bebas Neue, sin contorno; cada bloque de `palabras_por_bloque` palabras va
  dentro de un cuadro de color `color_caja`. La caja es rectangular: esquinas redondeadas o
  resaltar palabra por palabra dentro de una línea requieren otra técnica (por ejemplo
  Remotion) y quedan fuera de la v1.

## Ejecutar en local (opcional)

Requiere Python 3.11+ y ffmpeg:

```bash
pip install -r requirements.txt
python pipeline/render.py bingo-2026
```

El resultado queda en `proyectos/bingo-2026/salida/`. Para dibujar subtítulos, ffmpeg necesita
**libass**: el `ffmpeg` normal de Homebrew no la trae (`brew install ffmpeg-full` sí, pero ocupa
bastante disco). Sin libass, el script igual transcribe con Whisper, deja los subtítulos en
`salida/` y se detiene con un aviso; o puede poner `activar = false`.

## Archivos grandes (Git LFS)

Videos y audio (`.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v`, `.mp3`, `.wav`, `.m4a`) se suben con
Git LFS, según `.gitattributes`. Antes del primer `git add` de un video en un computador nuevo,
ejecute una vez:

```bash
git lfs install
```

## Consideraciones

- **Arial** no es redistribuible y no está en el servidor de GitHub. Por eso se usa **Arimo**
  (libre, mismas medidas que Arial). Si tiene licencia de Arial, puede poner el `.ttf` en
  `fuentes/` y cambiar `fuente_nombre = "Arial"` bajo su responsabilidad. Si una fuente
  descargada es *variable* y no se ve bien, use la versión estática.
- **Whisper en CPU** (el servidor de Actions no tiene GPU) puede tardar más que la duración del
  video, y cada ejecución consume minutos de Actions. Para ahorrar: transcriba una vez, revise y
  suba `subtitulos.srt` o `subtitulos.json` a `media/`.
- **Cuotas:** revise en su cuenta los minutos de Actions y el almacenamiento y ancho de banda de
  Git LFS que incluye su plan (GitHub Education). Cada ejecución descarga los archivos LFS del
  proyecto y de `banco/`.
- **Derechos:** los logos del CEE y de la UACh son institucionales. La música debe ser propia o de
  licencia libre; anote autor y licencia en la tabla de abajo.
- **Alcance de la v1:** solo videos como clips (sin imágenes como clips), sin efectos de sonido en
  transiciones y un estilo de subtítulos por proyecto.

## Fuentes, música y licencias

| Archivo | Autor | Licencia |
|---|---|---|
| `fuentes/Arimo-Regular.ttf`, `Arimo-Bold.ttf` | The Arimo Project Authors | SIL OFL 1.1 (`OFL-Arimo.txt`) |
| `fuentes/BebasNeue-Regular.ttf` | Ryoichi Tsunekawa (Dharma Type) | SIL OFL 1.1 (`OFL-BebasNeue.txt`) |
| `banco/sonidos/*` | PENDIENTE | PENDIENTE |
| `banco/musica/*` | PENDIENTE | PENDIENTE |
