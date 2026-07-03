

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial
import serial.tools.list_ports
import time
import os
import threading
import queue
import math
import json
import colorsys
import ctypes
import sys

# ─────────────────────────────────────────────────────────────────────────────
# SOLUCIÓN PARA MONITORES CON BAJA RESOLUCIÓN (DPI AWARENESS)
# ─────────────────────────────────────────────────────────────────────────────
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except:
            pass

try:
    import pyembroidery as emb
    PYEMBROIDERY_OK = True
except ImportError:
    PYEMBROIDERY_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES GLOBALES
# ─────────────────────────────────────────────────────────────────────────────
STEPS_PER_MM   = 390        # pasos por milímetro
BAUD_RATE      = 115200     # baudios
CONFIG_FILE    = "Bordadora_config.json"

# Paleta de colores MODO CLARO (Fondo blanco, letras negras)
C_BG           = "#FFFFFF"  # fondo principal (blanco)
C_SURFACE      = "#F0F0F0"  # superficie de paneles (gris muy claro)
C_PANEL        = "#CCCCCC"  # gris medio (más oscuro que #E0E0E0)
C_ACCENT       = "#000000"  # acento primario (negro)
C_ACCENT2      = "#333333"  # acento secundario (gris oscuro)
C_TEXT         = "#000000"  # texto principal (negro)
C_TEXT_DIM     = "#333333"  # texto secundario (gris oscuro)
C_GREEN        = "#008800"  # estado OK / conectado (verde oscuro)
C_ORANGE       = "#CC6600"  # advertencia (naranja)
C_RED          = "#CC0000"  # error / parar (rojo oscuro)
C_CYAN         = "#0066AA"  # info / TX (azul)
C_YELLOW       = "#CCAA00"  # amarillo destacado

# Colores de log (MANTENEMOS LOS ORIGINALES OSCUROS)
LOG_TX  = "#00ffff"   # cyan    → lo que enviamos
LOG_RX  = "#00ff00"   # verde   ← lo que recibimos
LOG_SYS = "#ffffff"   # blanco  sistema
LOG_ERR = "#ff0000"   # rojo    error

# ─────────────────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────────────────
def cargar_config():
    """Lee la configuración guardada en disco."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"puerto": "", "pasos": STEPS_PER_MM}


def guardar_config(cfg: dict):
    """Escribe la configuración en disco."""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def generar_colores_secciones(n: int) -> list:
    """Genera n colores HSL bien diferenciados para las secciones de bordado."""
    colores = []
    for i in range(n):
        h = i / max(n, 1)
        r, g, b = colorsys.hsv_to_rgb(h, 0.85, 0.95)
        # Colores más oscuros para que se vean bien en fondo blanco
        r, g, b = r * 0.7, g * 0.7, b * 0.7
        colores.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return colores


# ─────────────────────────────────────────────────────────────────────────────
# WIDGET: BOTÓN ESTILIZADO (SIN TOOLTIPS)
# ─────────────────────────────────────────────────────────────────────────────
def hacer_btn(parent, texto, cmd, color=C_PANEL, fg=C_TEXT, ancho=None, alto=None, **kw):
    """Crea un tk.Button con estilo consistente y texto nítido."""
    kwargs = dict(text=texto, command=cmd,
                  bg=color, fg=fg, 
                  activebackground=color,
                  activeforeground=C_YELLOW,
                  relief=tk.RAISED, bd=2,           # ← Borde definido
                  highlightthickness=1,              # ← Borde de enfoque
                  highlightbackground="#999999",    # ← Color del borde
                  font=("Segoe UI", 9, "bold"),
                  cursor="hand2", **kw)
    if ancho:
        kwargs["width"] = ancho
    if alto:
        kwargs["height"] = alto
    btn = tk.Button(parent, **kwargs)
    return btn


# ─────────────────────────────────────────────────────────────────────────────
# CLASE: VISUALIZADOR DST (Canvas interactivo)
# ─────────────────────────────────────────────────────────────────────────────
class VisualizadorDST(ttk.Frame):
    """
    Canvas interactivo que muestra el diseño de bordado cargado.
    - Zoom con rueda del ratón
    - Arrastrar para desplazar la vista
    - Colores por sección (cambio de color)
    - Marcadores de cambio de color
    - Coordenadas en tiempo real
    """

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # Estado interno
        self._puntadas   = []     # lista de (x, y, cmd)
        self._secciones  = []     # lista de listas de (x,y) por sección
        self._cambios    = []     # índices donde hay cambio de color
        self._zoom       = 1.0
        self._offset_x   = 0.0
        self._offset_y   = 0.0
        self._drag_start = None
        self._canvas_items = []
        self._info_diseño  = {}

        self._build_ui()

    # ── Construcción de la UI ──────────────────────────────────────────────
    def _build_ui(self):
        # Barra de herramientas
        barra = tk.Frame(self, bg=C_SURFACE, pady=4)
        barra.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 0))

        hacer_btn(barra, "⊕ Zoom +",  self.zoom_in, fg=C_TEXT).pack(side=tk.LEFT, padx=4)
        hacer_btn(barra, "⊖ Zoom −",  self.zoom_out, fg=C_TEXT).pack(side=tk.LEFT, padx=4)
        hacer_btn(barra, "⛶ Ajustar", self.ajustar_vista, fg=C_TEXT).pack(side=tk.LEFT, padx=4)
        hacer_btn(barra, "✕ Limpiar", self.limpiar, fg=C_TEXT).pack(side=tk.LEFT, padx=4)

        self._lbl_zoom = tk.Label(barra, text="Zoom: 100%",
                                  bg=C_SURFACE, fg=C_TEXT_DIM,
                                  font=("Consolas", 8))
        self._lbl_zoom.pack(side=tk.RIGHT, padx=10)

        self._lbl_coords = tk.Label(barra, text="X: — Y: —",
                                    bg=C_SURFACE, fg=C_TEXT_DIM,
                                    font=("Consolas", 8))
        self._lbl_coords.pack(side=tk.RIGHT, padx=10)

        # Canvas
        self._canvas = tk.Canvas(self, bg="#0a0a0a", highlightthickness=1,
                                 highlightbackground=C_PANEL, cursor="crosshair")
        self._canvas.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)

        # Scrollbar sólo decorativa; el pan se hace con drag
        self._canvas.bind("<MouseWheel>",       self._on_wheel)
        self._canvas.bind("<Button-4>",         self._on_wheel)   # Linux scroll up
        self._canvas.bind("<Button-5>",         self._on_wheel)   # Linux scroll down
        self._canvas.bind("<ButtonPress-1>",    self._on_drag_start)
        self._canvas.bind("<B1-Motion>",        self._on_drag_move)
        self._canvas.bind("<ButtonRelease-1>",  self._on_drag_end)
        self._canvas.bind("<Motion>",           self._on_mouse_move)
        self._canvas.bind("<Configure>",        self._on_resize)

        # Texto de bienvenida
        self._id_hint = self._canvas.create_text(
            0, 0, text="Cargá un archivo DST para ver la vista previa",
            fill="#666666", font=("Segoe UI", 11), anchor=tk.CENTER)
        self._posicionar_hint()

    def _posicionar_hint(self):
        w = self._canvas.winfo_width()  or 400
        h = self._canvas.winfo_height() or 300
        self._canvas.coords(self._id_hint, w // 2, h // 2)

    def _on_resize(self, _=None):
        self._posicionar_hint()
        if self._secciones:
            self._dibujar()

    # ── Zoom y pan ─────────────────────────────────────────────────────────
    def _on_wheel(self, ev):
        if not self._secciones:
            return
        factor = 1.15
        if ev.num == 5 or ev.delta < 0:
            factor = 1 / factor
        # Zoom centrado en el cursor
        cx = self._canvas.canvasx(ev.x)
        cy = self._canvas.canvasy(ev.y)
        self._zoom *= factor
        self._zoom = max(0.05, min(50.0, self._zoom))
        self._offset_x = ev.x - (ev.x - self._offset_x) * factor
        self._offset_y = ev.y - (ev.y - self._offset_y) * factor
        self._dibujar()

    def _on_drag_start(self, ev):
        self._drag_start = (ev.x, ev.y)
        self._canvas.config(cursor="fleur")

    def _on_drag_move(self, ev):
        if self._drag_start:
            dx = ev.x - self._drag_start[0]
            dy = ev.y - self._drag_start[1]
            self._offset_x += dx
            self._offset_y += dy
            self._drag_start = (ev.x, ev.y)
            self._dibujar()

    def _on_drag_end(self, _=None):
        self._drag_start = None
        self._canvas.config(cursor="crosshair")

    def _on_mouse_move(self, ev):
        if not self._secciones:
            return
        # Convertir píxeles → coordenadas del diseño (en mm)
        dx = (ev.x - self._offset_x) / self._zoom / 10.0  # 10 unidades/mm en DST
        dy = (ev.y - self._offset_y) / self._zoom / 10.0
        self._lbl_coords.config(text=f"X: {dx:+.2f}mm  Y: {dy:+.2f}mm")

    # ── Zoom buttons ───────────────────────────────────────────────────────
    def zoom_in(self):
        self._zoom = min(50.0, self._zoom * 1.3)
        self._dibujar()

    def zoom_out(self):
        self._zoom = max(0.05, self._zoom / 1.3)
        self._dibujar()

    def ajustar_vista(self):
        if not self._secciones:
            return
        w = self._canvas.winfo_width()  or 400
        h = self._canvas.winfo_height() or 300
        todas = [pt for sec in self._secciones for pt in sec]
        if not todas:
            return
        xs = [p[0] for p in todas]
        ys = [p[1] for p in todas]
        mx, my = min(xs), min(ys)
        Mx, My = max(xs), max(ys)
        rango_x = Mx - mx or 1
        rango_y = My - my or 1
        escala_x = (w - 40) / rango_x
        escala_y = (h - 40) / rango_y
        self._zoom     = min(escala_x, escala_y)
        self._offset_x = 20 - mx * self._zoom
        self._offset_y = 20 - my * self._zoom
        self._dibujar()

    def limpiar(self):
        self._puntadas   = []
        self._secciones  = []
        self._cambios    = []
        self._info_diseño = {}
        self._canvas.delete("all")
        self._id_hint = self._canvas.create_text(
            0, 0, text="Cargá un archivo DST para ver la vista previa",
            fill=C_TEXT_DIM, font=("Segoe UI", 11), anchor=tk.CENTER)
        self._posicionar_hint()
        self._lbl_zoom.config(text="Zoom: 100%")

    # ── Carga de datos ─────────────────────────────────────────────────────
    def cargar_patron(self, puntadas: list):
        """
        Recibe lista de (x, y, cmd) y reorganiza en secciones por color.
        Llama automáticamente a ajustar_vista().
        """
        self._puntadas  = puntadas
        self._secciones = []
        self._cambios   = []

        seccion_actual = []
        for i, (x, y, cmd) in enumerate(puntadas):
            if PYEMBROIDERY_OK and cmd == emb.COLOR_CHANGE:
                if seccion_actual:
                    self._secciones.append(seccion_actual)
                seccion_actual = []
                self._cambios.append(i)
            else:
                seccion_actual.append((x, y))

        if seccion_actual:
            self._secciones.append(seccion_actual)

        self.after(50, self.ajustar_vista)

    # ── Dibujo ─────────────────────────────────────────────────────────────
    def _dibujar(self):
        if not self._secciones:
            return
        self._canvas.delete("all")
        colores = generar_colores_secciones(len(self._secciones))

        # Dibujar cada sección con su color
        for sec_idx, (sec, color) in enumerate(zip(self._secciones, colores)):
            if len(sec) < 2:
                continue
            puntos_canvas = []
            for (x, y) in sec:
                cx = x * self._zoom + self._offset_x
                cy = y * self._zoom + self._offset_y
                puntos_canvas.extend([cx, cy])

            if len(puntos_canvas) >= 4:
                self._canvas.create_line(
                    puntos_canvas, fill=color, width=1.5,
                    smooth=False, tags="stitch")

            # Primer punto de sección (marcador de inicio de color)
            if sec:
                x0, y0 = sec[0]
                cx0 = x0 * self._zoom + self._offset_x
                cy0 = y0 * self._zoom + self._offset_y
                r = max(3, 5 / self._zoom ** 0.3)
                self._canvas.create_oval(
                    cx0 - r, cy0 - r, cx0 + r, cy0 + r,
                    fill=color, outline="white", width=1,
                    tags="cambio_color")
                # Etiqueta de número de color
                if self._zoom > 0.5:
                    self._canvas.create_text(
                        cx0 + r + 3, cy0,
                        text=f"C{sec_idx + 1}",
                        fill="white", font=("Consolas", 7),
                        anchor=tk.W, tags="label")

        # Punto inicial del diseño
        if self._secciones and self._secciones[0]:
            x0, y0 = self._secciones[0][0]
            cx0 = x0 * self._zoom + self._offset_x
            cy0 = y0 * self._zoom + self._offset_y
            self._canvas.create_oval(cx0-5, cy0-5, cx0+5, cy0+5,
                                     fill=C_GREEN, outline="black", width=2,
                                     tags="inicio")
            self._canvas.create_text(cx0, cy0 - 12, text="INICIO",
                                     fill=C_GREEN, font=("Consolas", 7))

        self._lbl_zoom.config(text=f"Zoom: {self._zoom * 100:.0f}%")


# ─────────────────────────────────────────────────────────────────────────────
# CLASE: CONVERTIDOR DST
# ─────────────────────────────────────────────────────────────────────────────
class ConvertidorDST:
    """
    Pestaña 2: carga de DST, vista previa visual y conversión a comandos .txt
    """

    def __init__(self, parent):
        self.parent        = parent
        self.frame         = ttk.Frame(parent)
        self.archivo_actual = None
        self.patron_actual  = None       # objeto pyembroidery
        self.usar_pasos     = tk.BooleanVar(value=True)

        self._build_ui()
        self.frame.pack(fill=tk.BOTH, expand=True)

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.frame.columnconfigure(0, weight=2)
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(0, weight=1)

        # ── Lado izquierdo: visualizador ─────────────────────────────────
        panel_vis = tk.Frame(self.frame, bg=C_BG)
        panel_vis.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=0)
        panel_vis.rowconfigure(1, weight=1)
        panel_vis.columnconfigure(0, weight=1)

        tk.Label(panel_vis, text="Vista Previa del Diseño",
                 bg=C_BG, fg=C_TEXT,
                 font=("Segoe UI", 10, "bold")).grid(
                     row=0, column=0, sticky="w", padx=8, pady=6)

        self.visualizador = VisualizadorDST(panel_vis)
        self.visualizador.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

        # ── Lado derecho: controles ───────────────────────────────────────
        panel_ctrl = tk.Frame(self.frame, bg=C_SURFACE, width=400)
        panel_ctrl.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=0)
        panel_ctrl.columnconfigure(0, weight=1)
        panel_ctrl.grid_propagate(False)

        # Título
        tk.Label(panel_ctrl, text=" CONVERTIDOR DST ",
                 bg=C_SURFACE, fg=C_ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(pady=(12, 4))


        ttk.Separator(panel_ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8, padx=10)

        # — Sección: archivo —
        tk.Label(panel_ctrl, text="ARCHIVO DST",
                 bg=C_SURFACE, fg=C_TEXT_DIM,
                 font=("Consolas", 8)).pack(anchor=tk.W, padx=12)

        self.lbl_archivo = tk.Label(panel_ctrl, text="(ninguno)",
                                    bg=C_SURFACE, fg=C_ORANGE,
                                    font=("Consolas", 9),
                                    wraplength=240, justify=tk.LEFT)
        self.lbl_archivo.pack(anchor=tk.W, padx=12, pady=4)

        hacer_btn(panel_ctrl, "📂  Abrir archivo DST",
                  self.seleccionar_archivo,
                  color=C_PANEL
                  ).pack(fill=tk.X, padx=12, pady=4, ipady=6)

        ttk.Separator(panel_ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8, padx=10)

        # — Sección: info diseño —
        tk.Label(panel_ctrl, text="INFO DEL DISEÑO",
                 bg=C_SURFACE, fg=C_TEXT_DIM,
                 font=("Consolas", 8)).pack(anchor=tk.W, padx=12)

        self.frame_info = tk.Frame(panel_ctrl, bg=C_BG)
        self.frame_info.pack(fill=tk.X, padx=12, pady=6)
        self._info_labels = {}
        for campo, valor_inicial in [
            ("Dimensiones:", "—"),
            ("Puntadas:",    "—"),
            ("Colores:",     "—"),
        ]:
            fila = tk.Frame(self.frame_info, bg=C_BG)
            fila.pack(fill=tk.X, pady=2)
            tk.Label(fila, text=campo, bg=C_BG, fg=C_TEXT_DIM,
                     font=("Segoe UI", 8), width=12, anchor=tk.W).pack(side=tk.LEFT)
            lbl = tk.Label(fila, text=valor_inicial, bg=C_BG, fg=C_TEXT,
                           font=("Consolas", 9))
            lbl.pack(side=tk.LEFT)
            self._info_labels[campo] = lbl

        ttk.Separator(panel_ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8, padx=10)

        # — Sección: opciones de conversión —
        tk.Label(panel_ctrl, text="FORMATO DE SALIDA",
                 bg=C_SURFACE, fg=C_TEXT_DIM,
                 font=("Consolas", 8)).pack(anchor=tk.W, padx=12)

        frame_radio = tk.Frame(panel_ctrl, bg=C_SURFACE)
        frame_radio.pack(fill=tk.X, padx=12, pady=6)

        for texto, valor in [
            ("Pasos directos  (×390/mm)  ← recomendado", True),
            ("Milímetros  (1 decimal)", False),
        ]:
            tk.Radiobutton(frame_radio, text=texto,
                           variable=self.usar_pasos, value=valor,
                           bg=C_SURFACE, fg=C_TEXT,
                           selectcolor=C_BG,
                           activebackground=C_SURFACE,
                           activeforeground=C_ORANGE,
                           font=("Segoe UI", 8)).pack(anchor=tk.W, pady=2)

        ttk.Separator(panel_ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8, padx=10)

        # — Botón convertir —
        self.btn_convertir = hacer_btn(
            panel_ctrl, "⚙  CONVERTIR Y GUARDAR",
            self.convertir, color=C_GREEN)
        self.btn_convertir.pack(fill=tk.X, padx=12, pady=4, ipady=8)
        self.btn_convertir.config(state=tk.DISABLED)

        # Barra de progreso
        self.progress = ttk.Progressbar(panel_ctrl, mode='determinate')
        self.progress.pack(fill=tk.X, padx=12, pady=(4, 0))
        self.lbl_progreso = tk.Label(panel_ctrl, text="",
                                     bg=C_SURFACE, fg=C_TEXT_DIM,
                                     font=("Segoe UI", 8))
        self.lbl_progreso.pack(pady=4)

        # Spacer
        tk.Frame(panel_ctrl, bg=C_SURFACE).pack(expand=True)

        if not PYEMBROIDERY_OK:
            tk.Label(panel_ctrl,
                     text="⚠ pyembroidery no instalado.\npip install pyembroidery",
                     bg=C_SURFACE, fg=C_RED,
                     font=("Segoe UI", 8), justify=tk.LEFT).pack(padx=12, pady=8)

    # ── Selección de archivo ───────────────────────────────────────────────
    def seleccionar_archivo(self):
        if not PYEMBROIDERY_OK:
            messagebox.showerror("Error", "pyembroidery no está instalado.\n"
                                          "Ejecutá: pip install pyembroidery")
            return

        archivo = filedialog.askopenfilename(
            title="Seleccionar archivo DST",
            filetypes=[("Tajima DST", "*.dst"), ("Todos los archivos", "*.*")])
        if not archivo:
            return

        self.archivo_actual = archivo
        self.lbl_archivo.config(text=os.path.basename(archivo))

        # Cargar y analizar en hilo aparte para no bloquear la UI
        threading.Thread(target=self._cargar_y_analizar, daemon=True).start()

    def _cargar_y_analizar(self):
        try:
            patron = emb.read(self.archivo_actual)
            self.patron_actual = patron

            puntadas = list(patron.stitches)

            # Cortar en END si existe
            puntadas_filtradas = []
            for st in puntadas:
                if st[2] == emb.END:
                    break
                puntadas_filtradas.append(st)

            if len(puntadas_filtradas) < 2:
                self.parent.after(0, lambda: messagebox.showerror(
                    "Error", "El archivo no contiene puntadas válidas."))
                return

            # Calcular info
            xs = [p[0] for p in puntadas_filtradas]
            ys = [p[1] for p in puntadas_filtradas]
            ancho_mm = (max(xs) - min(xs)) / 10.0
            alto_mm  = (max(ys) - min(ys)) / 10.0

            n_puntadas = sum(1 for p in puntadas_filtradas
                             if p[2] == emb.STITCH)
            n_colores  = sum(1 for p in puntadas_filtradas
                             if p[2] == emb.COLOR_CHANGE)

            # Actualizar UI en hilo principal
            self.parent.after(0, lambda: self._actualizar_info(
                puntadas_filtradas, ancho_mm, alto_mm, n_puntadas, n_colores))

        except Exception as e:
            self.parent.after(0, lambda: messagebox.showerror(
                "Error al cargar", str(e)))

    def _actualizar_info(self, puntadas, ancho_mm, alto_mm, n_puntadas, n_colores):
        self._info_labels["Dimensiones:"].config(
            text=f"{ancho_mm:.1f} × {alto_mm:.1f} mm")
        self._info_labels["Puntadas:"].config(text=f"{n_puntadas:,}")
        self._info_labels["Colores:"].config(text=str(n_colores + 1))
        self.btn_convertir.config(state=tk.NORMAL)
        self.visualizador.cargar_patron(puntadas)

    # ── Conversión ─────────────────────────────────────────────────────────
    def convertir(self):
        if not self.archivo_actual or not PYEMBROIDERY_OK:
            return

        nombre_sugerido = os.path.basename(
            self.archivo_actual).replace(".dst", "_comandos.txt")
        nombre_txt = filedialog.asksaveasfilename(
            title="Guardar comandos como…",
            defaultextension=".txt",
            initialfile=nombre_sugerido,
            filetypes=[("Texto", "*.txt")])
        if not nombre_txt:
            return

        # Lanzar conversión en hilo para no congelar la UI
        self.btn_convertir.config(state=tk.DISABLED)
        threading.Thread(target=self._convertir_hilo,
                         args=(nombre_txt,), daemon=True).start()

    def _convertir_hilo(self, nombre_txt):
        try:
            usar_pasos = self.usar_pasos.get()
            patron     = emb.read(self.archivo_actual)

            puntadas = []
            for st in patron.stitches:
                if st[2] == emb.END:
                    break
                puntadas.append(st)

            xs = [p[0] for p in puntadas]
            ys = [p[1] for p in puntadas]
            ancho_mm = (max(xs) - min(xs)) / 10.0
            alto_mm  = (max(ys) - min(ys)) / 10.0
            n_puntadas = sum(1 for p in puntadas if p[2] == emb.STITCH)
            n_colores  = sum(1 for p in puntadas if p[2] == emb.COLOR_CHANGE)

            total = len(puntadas)
            self.parent.after(0, lambda: self.progress.config(maximum=total))
            self.parent.after(0, lambda: self.lbl_progreso.config(text="Convirtiendo…"))

            with open(nombre_txt, "w") as f:
                f.write(f"#DST2CMD v2.0\n")
                f.write(f"#ARCHIVO: {os.path.basename(self.archivo_actual)}\n")
                f.write(f"#SIZE: {ancho_mm:.1f}x{alto_mm:.1f}mm\n")
                f.write(f"#STITCHES: {n_puntadas}\n")
                f.write(f"#COLORS: {n_colores + 1}\n")
                if usar_pasos:
                    f.write(f"#STEPS_PER_MM: {STEPS_PER_MM}\n")
                f.write("#FORMAT: dx,dy,flag  (flag: 1=puntada 0=salto 4=cambio_color)\n")
                f.write("#START\n")

                ref_x, ref_y = puntadas[0][0], puntadas[0][1]
                err_x, err_y = 0.0, 0.0

                for i in range(1, len(puntadas)):
                    x, y, cmd = puntadas[i]

                    if cmd == emb.COLOR_CHANGE:
                        f.write("0,0,4\n")
                        ref_x, ref_y = x, y
                        err_x, err_y = 0.0, 0.0
                        # actualizar barra cada 100 pasos
                        if i % 100 == 0:
                            ii = i
                            self.parent.after(0, lambda v=ii: self.progress.config(value=v))
                        continue

                    dx_raw = x - ref_x
                    dy_raw = y - ref_y

                    if usar_pasos:
                        err_x += dx_raw * STEPS_PER_MM / 10.0
                        err_y += dy_raw * STEPS_PER_MM / 10.0
                        sx = round(err_x)
                        sy = round(err_y)
                        err_x -= sx
                        err_y -= sy
                        dx_out = -sx
                        dy_out =  sy
                        flag   = 1 if cmd == emb.STITCH else 0
                        f.write(f"{dx_out},{dy_out},{flag}\n")
                    else:
                        dx_out = round(dx_raw / 10.0, 1)
                        dy_out = round(dy_raw / 10.0, 1)
                        flag   = 1 if cmd == emb.STITCH else 0
                        f.write(f"{dx_out:.1f},{dy_out:.1f},{flag}\n")

                    ref_x, ref_y = x, y

                    if i % 100 == 0:
                        ii = i
                        self.parent.after(0, lambda v=ii: self.progress.config(value=v))

                f.write("#END\n")

            self.parent.after(0, lambda: self._fin_conversion(nombre_txt))

        except Exception as e:
            err = str(e)
            self.parent.after(0, lambda: self._error_conversion(err))

    def _fin_conversion(self, nombre_txt):
        self.progress.config(value=0)
        self.lbl_progreso.config(text="")
        self.btn_convertir.config(state=tk.NORMAL)
        messagebox.showinfo("Conversión exitosa",
                            f"Archivo guardado:\n{nombre_txt}")

    def _error_conversion(self, msg):
        self.progress.config(value=0)
        self.lbl_progreso.config(text="")
        self.btn_convertir.config(state=tk.NORMAL)
        messagebox.showerror("Error en conversión", msg)


# ─────────────────────────────────────────────────────────────────────────────
# CLASE: CONTROL CNC
# ─────────────────────────────────────────────────────────────────────────────
class ControlCNC:
    """
    Pestaña 1: conexión serial, control manual en mm, SD, log de eventos.
    Toda la comunicación es no-bloqueante (threading + queue).
    """

    def __init__(self, parent):
        self.parent     = parent
        self.frame      = ttk.Frame(parent)

        self.ser        = None
        self.conectado  = False
        self.paso       = STEPS_PER_MM
        self.cfg        = cargar_config()
        self._q_log     = queue.Queue()   # cola para mensajes de log desde hilos
        self._archivos_sd = []            # lista para almacenar archivos .txt de la SD

        self._build_ui()
        self.frame.pack(fill=tk.BOTH, expand=True)
        self._tick_log()   # ciclo de actualización del log

    # ── Log ───────────────────────────────────────────────────────────────
    def _log(self, msg: str, tipo: str = "SYS"):
        """Encola un mensaje de log (seguro desde cualquier hilo)."""
        timestamp = time.strftime("%H:%M:%S")
        self._q_log.put((timestamp, msg, tipo))

    def _tick_log(self):
        """Extrae mensajes de la cola y los vuelca al widget Text."""
        try:
            while True:
                timestamp, msg, tipo = self._q_log.get_nowait()
                col = {"TX": LOG_TX, "RX": LOG_RX, "SYS": LOG_SYS,
                       "ERR": LOG_ERR}.get(tipo, LOG_SYS)
                prefijo = {"TX": "→", "RX": "←", "SYS": "·",
                           "ERR": "!"}.get(tipo, "·")
                linea = f"[{timestamp}] {prefijo} {msg}\n"
                self.txt_log.config(state=tk.NORMAL)
                self.txt_log.insert(tk.END, linea, tipo)
                self.txt_log.see(tk.END)
                self.txt_log.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.parent.after(50, self._tick_log)

    # ── Puertos ───────────────────────────────────────────────────────────
    def _actualizar_puertos(self):
        puertos = [p.device for p in serial.tools.list_ports.comports()]
        self.cb_puerto["values"] = puertos
        if puertos:
            if self.cfg.get("puerto") in puertos:
                self.cb_puerto.set(self.cfg["puerto"])
            else:
                self.cb_puerto.set(puertos[0])
        self._log(f"{len(puertos)} puerto(s) detectado(s)")

    def _actualizar_paso(self, _=None):
        try:
            v = int(self.entry_paso.get())
            if v > 0:
                self.paso = v
                self.cfg["pasos"] = v
                guardar_config(self.cfg)
        except ValueError:
            self.entry_paso.delete(0, tk.END)
            self.entry_paso.insert(0, str(self.paso))

    # ── Conexión ──────────────────────────────────────────────────────────
    def _conectar(self):
        if self.conectado:
            self._desconectar()
        else:
            self._intentar_conexion()

    def _desconectar(self):
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.conectado = False
        self.btn_conectar.config(text="Conectar", bg=C_PANEL)
        self.lbl_estado.config(text="● Desconectado", fg=C_RED)
        self._habilitar_controles(False)
        self._log("Desconectado")

    def _intentar_conexion(self):
        port = self.cb_puerto.get()
        if not port:
            messagebox.showwarning("Sin puerto", "Seleccioná un puerto COM.")
            return

        def _conectar_hilo():
            try:
                ser = serial.Serial()
                ser.port     = port
                ser.baudrate = BAUD_RATE
                ser.timeout  = 1
                ser.open()

                # Reset del Arduino por DTR
                ser.setDTR(False)
                time.sleep(0.1)
                ser.setDTR(True)
                time.sleep(1.5)    # esperar boot del Arduino

                self.ser = ser
                self.conectado = True
                self.cfg["puerto"] = port
                guardar_config(self.cfg)

                self.parent.after(0, self._on_conectado)

                # Vaciar buffer de inicio
                time.sleep(0.3)
                while ser.in_waiting:
                    try:
                        linea = ser.readline().decode("utf-8", errors="replace").strip()
                        if linea:
                            self._log(linea, "RX")
                    except Exception:
                        break
                ser.reset_input_buffer()

                # Lanzar hilo lector continuo
                threading.Thread(target=self._lector_serial, daemon=True).start()

            except Exception as e:
                self.parent.after(0, lambda: self._on_error_conexion(str(e)))

        threading.Thread(target=_conectar_hilo, daemon=True).start()

    def _on_conectado(self):
        self.btn_conectar.config(text="Desconectar", bg=C_PANEL, fg=C_TEXT)
        self.lbl_estado.config(text="● CONECTADO", fg=C_GREEN)
        self._habilitar_controles(True)
        self._log(f"Conectado a {self.cb_puerto.get()} @ {BAUD_RATE}", "SYS")

    def _on_error_conexion(self, msg):
        messagebox.showerror("Error de conexión", f"No se pudo conectar:\n{msg}")
        self._log(f"Error conexión: {msg}", "ERR")

    # ── Lector serial continuo ────────────────────────────────────────────
    def _lector_serial(self):
        """Corre en hilo separado: lee líneas y las encola al log. También procesa LIST."""
        modo_lista = False
        while self.conectado and self.ser and self.ser.is_open:
            try:
                if self.ser.in_waiting:
                    linea = self.ser.readline().decode("utf-8", errors="replace").strip()
                    if linea:
                        self._log(linea, "RX")
                        
                        # Procesar respuesta de LIST
                        if linea == "Archivos SD:":
                            modo_lista = True
                            self._archivos_sd = []
                            continue
                        if modo_lista:
                            if linea == "FIN_LISTA":
                                modo_lista = False
                                # Actualizar ComboBox en el hilo principal
                                self.parent.after(0, self._actualizar_combo_sd)
                            else:
                                # Filtrar solo archivos .txt
                                if linea.lower().endswith(".txt"):
                                    self._archivos_sd.append(linea)
                else:
                    time.sleep(0.02)
            except serial.SerialException:
                self._log("Conexión perdida", "ERR")
                self.parent.after(0, self._desconectar)
                break
            except Exception as e:
                self._log(f"Lector error: {e}", "ERR")
                time.sleep(0.1)

    def _actualizar_combo_sd(self):
        """Carga los archivos .txt encontrados en el ComboBox."""
        self.combo_archivos["values"] = self._archivos_sd
        if self._archivos_sd:
            self.combo_archivos.set(self._archivos_sd[0])  # Seleccionar el primero por defecto
        else:
            self.combo_archivos.set("")

    # ── Enviar comandos ───────────────────────────────────────────────────
    def _enviar(self, cmd: str):
        """Envía un comando en un hilo; no bloquea la UI."""
        if not self.conectado:
            self._log("No conectado", "ERR")
            return

        def _tx():
            try:
                self.ser.write((cmd + "\n").encode())
                self._log(cmd, "TX")
            except Exception as e:
                self._log(f"TX error: {e}", "ERR")

        threading.Thread(target=_tx, daemon=True).start()

    # ── Acciones de control ───────────────────────────────────────────────
    def _mover_mm(self, dx_mm, dy_mm):
        """Convierte mm a pasos y envía el comando MOV."""
        try:
            pasos_x = round(dx_mm * self.paso)
            pasos_y = round(dy_mm * self.paso)
            self._enviar(f"MOV {pasos_x} {pasos_y}")
        except Exception:
            pass

    def _zero(self):      self._enviar("ZERO")
    def _home(self):      self._enviar("HOME")
    def _stop(self):      self._enviar("STOP")
    def _pause(self):     self._enviar("PAUSE")
    def _resume(self):    self._enviar("RESUME")
    def _testz(self):     self._enviar("TESTZ")
    def _status(self):    self._enviar("STATUS")

    def _listar_sd(self):
        self._archivos_sd = []
        self.combo_archivos["values"] = []
        self.combo_archivos.set("")
        self._enviar("LIST")

    def _seleccionar_sd(self):
        archivo = self.combo_archivos.get().strip()
        if not archivo:
            self._log("Seleccioná un archivo primero", "ERR")
            return
        self._enviar(f"SELEC {archivo}")

    def _bordar(self):
        if not self.combo_archivos.get().strip():
            messagebox.showwarning("Sin archivo", "Seleccioná un archivo SD primero.")
            return
        if not messagebox.askyesno("Iniciar bordado",
                                   "¿Iniciar el bordado con el archivo seleccionado?"):
            return
        self._enviar("BORDAR")

    # ── Habilitar / deshabilitar controles ───────────────────────────────
    def _habilitar_controles(self, habilitar: bool):
        estado = tk.NORMAL if habilitar else tk.DISABLED
        for btn in self._botones_control:
            btn.config(state=estado)
        self.entry_paso.config(state=estado)
        self.combo_archivos.config(state=estado)
        # También habilitar/deshabilitar entradas de mm
        self.entry_mm_x.config(state=estado)
        self.entry_mm_y.config(state=estado)

    # ── Construcción UI ───────────────────────────────────────────────────
    def _build_ui(self):
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)
        self._botones_control = []

        # ── Barra de conexión ─────────────────────────────────────────────
        barra = tk.Frame(self.frame, bg=C_SURFACE, pady=6)
        barra.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))

        tk.Label(barra, text="Puerto:", bg=C_SURFACE, fg=C_TEXT_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(10, 4))
        self.cb_puerto = ttk.Combobox(barra, width=12)
        self.cb_puerto.pack(side=tk.LEFT, padx=4)

        hacer_btn(barra, "↻",  self._actualizar_puertos
                  ).pack(side=tk.LEFT, padx=4)

        self.btn_conectar = hacer_btn(barra, "Conectar", self._conectar,
                                      color=C_PANEL, ancho=12)
        self.btn_conectar.pack(side=tk.LEFT, padx=8)

        self.lbl_estado = tk.Label(barra, text="● DESCONECTADO",
                                   bg=C_SURFACE, fg=C_RED,
                                   font=("Segoe UI", 9, "bold"))
        self.lbl_estado.pack(side=tk.LEFT, padx=8)

        # Separador vertical
        tk.Frame(barra, bg=C_TEXT_DIM, width=1).pack(
            side=tk.LEFT, fill=tk.Y, padx=12, pady=4)

        tk.Label(barra, text="Pasos/mm:", bg=C_SURFACE, fg=C_TEXT_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=4)
        self.entry_paso = tk.Entry(barra, width=7, bg=C_BG, fg=C_TEXT,
                                   insertbackground=C_TEXT,
                                   font=("Consolas", 9))
        self.entry_paso.insert(0, str(self.cfg.get("pasos", STEPS_PER_MM)))
        self.entry_paso.pack(side=tk.LEFT, padx=4)
        self.entry_paso.bind("<Return>", self._actualizar_paso)
        self.entry_paso.bind("<FocusOut>", self._actualizar_paso)

        # ── Cuerpo principal (log + controles) ────────────────────────────
        cuerpo = tk.Frame(self.frame, bg=C_BG)
        cuerpo.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        cuerpo.columnconfigure(0, weight=3)
        cuerpo.columnconfigure(1, weight=1)
        cuerpo.rowconfigure(0, weight=1)

        # ── Panel de log ──────────────────────────────────────────────────
        panel_log = tk.Frame(cuerpo, bg=C_SURFACE)
        panel_log.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        panel_log.columnconfigure(0, weight=1)
        panel_log.rowconfigure(1, weight=1)

        hdr_log = tk.Frame(panel_log, bg=C_PANEL)
        hdr_log.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(hdr_log, text="  EVENTOS SERIAL",
                 bg=C_PANEL, fg=C_TEXT,
                 font=("Consolas", 8, "bold")).pack(side=tk.LEFT, pady=4)
        hacer_btn(hdr_log, "Limpiar",
                  lambda: (self.txt_log.config(state=tk.NORMAL),
                            self.txt_log.delete("1.0", tk.END),
                            self.txt_log.config(state=tk.DISABLED)),
                  color=C_PANEL
                  ).pack(side=tk.RIGHT, padx=6, pady=2)

        self.txt_log = tk.Text(panel_log, bg="#0a0a0a", fg="#ffffff",
                       font=("Consolas", 9), state=tk.DISABLED,
                       selectbackground=C_ACCENT2)
        self.txt_log.grid(row=1, column=0, sticky="nsew")
        self.txt_log.tag_config("TX",  foreground=LOG_TX)
        self.txt_log.tag_config("RX",  foreground=LOG_RX)
        self.txt_log.tag_config("SYS", foreground=LOG_SYS)
        self.txt_log.tag_config("ERR", foreground=LOG_ERR)

        sb = tk.Scrollbar(panel_log, command=self.txt_log.yview,
                          bg=C_SURFACE, troughcolor=C_BG)
        sb.grid(row=1, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=sb.set)

        # ── Panel lateral de controles ────────────────────────────────────
        panel_ctrl = tk.Frame(cuerpo, bg=C_SURFACE, width=380)
        panel_ctrl.grid(row=0, column=1, sticky="nsew")
        panel_ctrl.columnconfigure(0, weight=1)
        panel_ctrl.grid_propagate(False)

        # ── Control Manual ────────────────────────────────────────────────
        lbl_manual = tk.Label(panel_ctrl, text="CONTROL MANUAL (mm)",
                              bg=C_PANEL, fg=C_TEXT_DIM,
                              font=("Consolas", 12, "bold"))
        lbl_manual.pack(fill=tk.X, pady=(2, 0))

        # Frame para entrada de mm
        frame_mm = tk.Frame(panel_ctrl, bg=C_SURFACE)
        frame_mm.pack(fill=tk.X, padx=6, pady=6)
        
        tk.Label(frame_mm, text="X (mm):", bg=C_SURFACE, fg=C_TEXT_DIM).grid(row=0, column=0, padx=4)
        self.entry_mm_x = tk.Entry(frame_mm, width=8, bg=C_BG, fg=C_TEXT, insertbackground=C_TEXT, font=("Consolas", 10))
        self.entry_mm_x.grid(row=0, column=1, padx=4)
        self.entry_mm_x.insert(0, "10.0")
        
        tk.Label(frame_mm, text="Y (mm):", bg=C_SURFACE, fg=C_TEXT_DIM).grid(row=0, column=2, padx=4)
        self.entry_mm_y = tk.Entry(frame_mm, width=8, bg=C_BG, fg=C_TEXT, insertbackground=C_TEXT, font=("Consolas", 10))
        self.entry_mm_y.grid(row=0, column=3, padx=4)
        self.entry_mm_y.insert(0, "10.0")

        # Grilla de movimiento
        grid_frame = tk.Frame(panel_ctrl, bg=C_SURFACE)
        grid_frame.pack(pady=4)

        GRID = [
            ("↖", -1,  1),  ("↑",  0,  1),  ("↗",  1,  1),
            ("←", -1,  0),  None,            ("→",  1,  0),
            ("↙", -1, -1),  ("↓",  0, -1),  ("↘",  1, -1),
        ]

        for idx, item in enumerate(GRID):
            r, c = divmod(idx, 3)
            if item is None:
                btn = hacer_btn(grid_frame, "■ STOP", self._stop,
                                color=C_RED, fg="white", ancho=8, alto=2)
                btn.grid(row=r, column=c, padx=3, pady=3)
                self._botones_control.append(btn)
            else:
                texto, dx_mm, dy_mm = item
                def cmd(_dx=dx_mm, _dy=dy_mm):
                    try:
                        x_val = float(self.entry_mm_x.get()) * _dx
                        y_val = float(self.entry_mm_y.get()) * _dy
                        self._mover_mm(x_val, y_val)
                    except ValueError:
                        self._log("Valor mm inválido", "ERR")
                btn = hacer_btn(grid_frame, texto, cmd,
                                color=C_PANEL, ancho=8, alto=2)
                btn.grid(row=r, column=c, padx=3, pady=3)
                self._botones_control.append(btn)

        # ── Acciones rápidas ──────────────────────────────────────────────
        acciones_frame = tk.Frame(panel_ctrl, bg=C_SURFACE)
        acciones_frame.pack(fill=tk.X, padx=6, pady=2)
        acciones_frame.columnconfigure((0, 1), weight=1)

        acciones = [
            ("ZERO",   self._zero,   C_CYAN),
            ("HOME",   self._home,   C_CYAN),
            ("PAUSE",  self._pause,  C_ORANGE),
            ("RESUME", self._resume, C_ORANGE),
            ("TEST Z", self._testz,  C_CYAN),
            ("STATUS", self._status, C_CYAN),
        ]
        for i, (texto, cmd, color) in enumerate(acciones):
            r, c = divmod(i, 2)
            btn = hacer_btn(acciones_frame, texto, cmd, color=color)
            btn.grid(row=r, column=c, padx=2, pady=2, sticky="ew", ipady=4)
            self._botones_control.append(btn)

        # ── Panel SD ──────────────────────────────────────────────────────
        lbl_sd = tk.Label(panel_ctrl, text="TARJETA SD",
                          bg=C_PANEL, fg=C_TEXT_DIM,
                          font=("Consolas", 12))
        lbl_sd.pack(fill=tk.X, pady=(2, 0))

        sd_frame = tk.Frame(panel_ctrl, bg=C_SURFACE)
        sd_frame.pack(fill=tk.X, padx=6, pady=6)
        sd_frame.columnconfigure(0, weight=1)

        btn_listar = hacer_btn(sd_frame, "⟳  Listar archivos SD", self._listar_sd,
                               color=C_PANEL)
        btn_listar.grid(row=0, column=0, sticky="ew", ipady=5, pady=2)
        self._botones_control.append(btn_listar)

        tk.Label(sd_frame, text="Archivo:", bg=C_SURFACE, fg=C_TEXT_DIM,
                 font=("Segoe UI", 8)).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.combo_archivos = ttk.Combobox(sd_frame, width=32)
        self.combo_archivos.grid(row=2, column=0, sticky="ew", pady=2)

        btn_sel = hacer_btn(sd_frame, "✓  Seleccionar", self._seleccionar_sd,
                            color=C_PANEL)
        btn_sel.grid(row=3, column=0, sticky="ew", ipady=4, pady=2)
        self._botones_control.append(btn_sel)

        btn_bordar = hacer_btn(sd_frame, "▶  INICIAR BORDADO", self._bordar,
                               color=C_GREEN)
        btn_bordar.grid(row=4, column=0, sticky="ew", ipady=6, pady=(4, 2))
        self._botones_control.append(btn_bordar)

        # Deshabilitar todos hasta conectar
        self._habilitar_controles(False)
        self._actualizar_puertos()
        self._log("Interfaz iniciada. Conectá la máquina.")


# ─────────────────────────────────────────────────────────────────────────────
# CLASE PRINCIPAL: SuiteCNC
# ─────────────────────────────────────────────────────────────────────────────
class SuiteCNC:
    """Ventana principal con las dos pestañas."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BORDADOR CNC ")
        self.root.configure(bg=C_BG)
        self._centrar_ventana(0.82, 0.82)
        self.root.minsize(900, 600)

        # Estilo general ttk
        estilo = ttk.Style()
        estilo.theme_use("clam")
        estilo.configure("TFrame", background=C_BG)
        estilo.configure("TNotebook", background=C_BG, borderwidth=0)
        estilo.configure("TNotebook.Tab",
                         background=C_SURFACE, foreground=C_TEXT_DIM,
                         padding=[14, 6], font=("Segoe UI", 9))
        estilo.map("TNotebook.Tab",
                   background=[("selected", C_PANEL)],
                   foreground=[("selected", C_TEXT)])
        estilo.configure("TLabelframe", background=C_BG, bordercolor=C_PANEL)
        estilo.configure("TLabelframe.Label", background=C_BG, foreground=C_TEXT_DIM,
                         font=("Segoe UI", 8))
        estilo.configure("TCombobox", fieldbackground=C_BG, background=C_BG,
                         foreground=C_TEXT, arrowcolor=C_TEXT_DIM)
        estilo.configure("TProgressbar",
                         troughcolor=C_BG, background=C_ACCENT2, thickness=6)
        estilo.configure("TSeparator", background=C_PANEL)

        # Notebook
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        tab1 = ttk.Frame(self.nb)
        self.nb.add(tab1, text="  🎮  CONTROL CNC  ")
        self.control = ControlCNC(tab1)

        tab2 = ttk.Frame(self.nb)
        self.nb.add(tab2, text="  🔄  CONVERTIDOR DST  ")
        self.conversor = ConvertidorDST(tab2)

        # Barra de estado inferior
        barra_st = tk.Frame(self.root, bg=C_SURFACE, height=22)
        barra_st.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(barra_st,
                 text="  Bordador CNC v1.0 |  Ckech-Tech  |  "
                      f"Python {__import__('sys').version.split()[0]}",
                 bg=C_SURFACE, fg=C_TEXT_DIM,
                 font=("Consolas", 7)).pack(side=tk.LEFT)

        if not PYEMBROIDERY_OK:
            tk.Label(barra_st,
                     text="  ⚠ pyembroidery no instalado  ",
                     bg=C_RED, fg="white",
                     font=("Consolas", 7)).pack(side=tk.RIGHT)

    def _centrar_ventana(self, frac_w: float, frac_h: float):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w  = int(sw * frac_w)
        h  = int(sh * frac_h)
        x  = (sw - w) // 2
        y  = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def run(self):
        self.root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = SuiteCNC()
    app.run()