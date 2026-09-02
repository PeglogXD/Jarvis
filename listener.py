"""
J.A.R.V.I.S. — Listener en segundo plano
-----------------------------------------
Escucha continuamente el micrófono y abre jarvis.py al detectar "Hey Jarvis".
Ejecútalo al iniciar Windows para que siempre esté activo.

Uso:
  pythonw listener.py          (sin ventana de consola — recomendado)
  python listener.py           (con consola, para depurar)
"""
import os
import sys
import time
import threading
import subprocess
import json
import speech_recognition as sr

# ─── Configuración ───
JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
JARVIS_SCRIPT = os.path.join(JARVIS_DIR, "jarvis.py")
LOG_FILE = os.path.join(JARVIS_DIR, "listener.log")
COOLDOWN = 3  # segundos tras abrir jarvis.py antes de volver a escuchar

# ─── Wake words (normalizados sin acentos) ───
_WAKE_KEYWORDS = [
    "hey jarvis", "hola jarvis", "oye jarvis", "escucha jarvis",
    "despierta jarvis", "activa jarvis", "senor jarvis", "sir jarvis",
    "aqui jarvis", "presente jarvis", "a la orden jarvis",
    "dale jarvis", "vamos jarvis", "buenas jarvis",
    "buenos dias jarvis", "buenas tardes jarvis", "buenas noches jarvis",
    "comienza jarvis", "inicia jarvis", "abre jarvis",
    "escuchame jarvis", "jarvis", "jervis", "jarvisito", "jiarvis",
]

# ─── Estado ───
_jarvis_proc = None       # referencia al proceso de jarvis.py
_lock = threading.Lock()
_ultimo_lanzamiento = 0   # timestamp del último lanzamiento
_ultimo_audio = 0         # timestamp del último audio captado


def _log(msg):
    """Append al log del listener."""
    try:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _normalizar(texto):
    """Quita acentos y pasa a minúsculas."""
    import unicodedata
    txt = unicodedata.normalize("NFKD", texto)
    txt = txt.encode("ascii", "ignore").decode("ascii").lower()
    return txt


def _fuzzy_match(palabra, candidato, umbral=0.65):
    """Match difuso: retorna True si 'candidato' se parece a 'palabra'.
    Usa distancia de Levenshtein simplificada."""
    if not candidato or not palabra:
        return False
    a, b = palabra, candidato
    if len(a) > len(b):
        a, b = b, a
    # Si el candidato es prefix del palabra (o viceversa), match
    if b.startswith(a) or a.startswith(b):
        return True
    # Distancia de edición simplificada
    len_a = len(a)
    len_b = len(b)
    if len_b == 0:
        return len_a == 0
    prev = list(range(len_a + 1))
    for j in range(1, len_b + 1):
        curr = [j] + [0] * len_a
        for i in range(1, len_a + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[i] = min(curr[i - 1] + 1, prev[i] + 1, prev[i - 1] + cost)
        prev = curr
    dist = prev[len_a]
    similarity = 1.0 - dist / max(len_a, len_b)
    return similarity >= umbral


def detectar_wake(texto):
    """Retorna True si el texto contiene un wake word (con fuzzy matching)."""
    t = _normalizar(texto).strip()
    if not t:
        return False

    # Exact match primero
    for kw in _WAKE_KEYWORDS:
        kn = _normalizar(kw)
        if kn in t or t.startswith(kn):
            return True

    # Check individual words
    palabras = t.split()
    for kw in ["jarvis", "jervis", "jarvisito", "jiarvis"]:
        if kw in palabras:
            return True
        # Fuzzy match: "jar" puede ser "jarvis" mal escuchado
        for p in palabras:
            if _fuzzy_match(kw, p, 0.55):
                return True

    # "Hey" / "hola" / "oye" solos con 1-3 palabras = probable wake
    # "Hey ya" probablemente es "Hey Jarvis" mal escuchado
    if len(palabras) <= 3 and palabras[0] in ["hey", "hola", "oye", "ey", "ei", "oye"]:
        # Si hay una palabra que suena corta después de hey/hola/oye,
        # probablemente es "jarvis" mal reconocido
        _log(f"Wake probable por '{palabras[0]}' detectado en: '{t}'")
        return True

    return False


def _jarvis_esta_corriendo():
    """Verifica si jarvis.py ya está abierto."""
    global _jarvis_proc
    if _jarvis_proc is not None:
        poll = _jarvis_proc.poll()
        if poll is None:
            return True  # sigue corriendo
        _jarvis_proc = None
    return False


def abrir_jarvis():
    """Lanza jarvis.py si no está corriendo."""
    global _jarvis_proc, _ultimo_lanzamiento

    with _lock:
        if _jarvis_esta_corriendo():
            _log("jarvis.py ya está corriendo, ignorando.")
            return

        ahora = time.time()
        if ahora - _ultimo_lanzamiento < COOLDOWN:
            _log("Cooldown activo, ignorando.")
            return

        try:
            # Usar python.exe directamente (pythonw.exe no tiene acceso a micrófono en background)
            python_exe = sys.executable
            if "pythonw" in python_exe.lower():
                python_exe = python_exe.replace("pythonw.exe", "python.exe")

            _jarvis_proc = subprocess.Popen(
                [python_exe, JARVIS_SCRIPT],
                cwd=JARVIS_DIR,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
            _ultimo_lanzamiento = time.time()
            _log(f"jarvis.py lanzado (PID={_jarvis_proc.pid})")
        except Exception as e:
            _log(f"Error al lanzar jarvis.py: {e}")


def callback_recognizer(recognizer, audio):
    """Callback que se ejecuta cuando hay audio captado."""
    try:
        texto = recognizer.recognize_google(audio, language="es-ES", show_all=True)
        if not texto:
            return
        # show_all retorna un dict con 'alternative'
        candidatos = []
        if isinstance(texto, dict) and "alternative" in texto:
            for alt in texto["alternative"]:
                candidatos.append(alt.get("transcript", ""))
        elif isinstance(texto, str):
            candidatos = [texto]
        for candidato in candidatos:
            if candidato:
                _log(f"Captado: '{candidato}'")
                if detectar_wake(candidato):
                    _log(f"¡Wake word detectada en '{candidato}'! Abriendo J.A.R.V.I.S...")
                    abrir_jarvis()
        # Actualizar timestamp de último audio captado
        global _ultimo_audio
        _ultimo_audio = time.time()
    except sr.UnknownValueError:
        pass
    except sr.RequestError as e:
        _log(f"Error de reconocimiento: {e}")
        time.sleep(2)
    except Exception as e:
        _log(f"Error inesperado: {e}")


LOCK_FILE = os.path.join(JARVIS_DIR, "listener.lock")

def _cleanup_lock():
    try: os.remove(LOCK_FILE)
    except: pass

def _listar_mics():
    """Lista los micrófonos disponibles para debug."""
    try:
        mics = sr.Microphone.list_microphone_names()
        _log(f"Micrófonos disponibles ({len(mics)}): {mics}")
        return mics
    except Exception as e:
        _log(f"Error listando micrófonos: {e}")
        return []


def main():
    # Evitar múltiples instancias
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            import ctypes
            if ctypes.windll.kernel32.OpenProcess(0x1000, False, old_pid):
                _log(f"Ya hay un listener corriendo (PID {old_pid}). Saliendo.")
                return
        except Exception:
            pass
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    import atexit
    atexit.register(_cleanup_lock)

    _log("═══════════════════════════════════════")
    _log("J.A.R.V.I.S. Listener iniciado")
    _log(f"Script: {JARVIS_SCRIPT}")
    _log(f"Python: {sys.executable}")
    _log("Escuchando wake words...")

    # Listar micrófonos
    _listar_mics()

    r = sr.Recognizer()
    r.energy_threshold = 800
    r.dynamic_energy_threshold = True
    r.pause_threshold = 0.5
    r.non_speaking_duration = 0.3

    # Intentar abrir micrófono con reintentos
    mic = None
    for intento in range(5):
        try:
            mic = sr.Microphone()
            _log(f"Micrófono abierto (intento {intento + 1})")
            break
        except Exception as e:
            _log(f"Error abriendo micrófono (intento {intento + 1}): {e}")
            time.sleep(3)

    if mic is None:
        _log("FATAL: No se pudo abrir ningún micrófono. Reintentando en 30s...")
        time.sleep(30)
        main()  # reintentar desde cero
        return

    # Ajustar al ruido ambiental
    try:
        with mic as source:
            _log("Calibrando micrófono (1s de silencio)...")
            r.adjust_for_ambient_noise(source, duration=1)
            _log(f"Energy threshold ajustado a: {r.energy_threshold}")
    except Exception as e:
        _log(f"Error calibrando micrófono: {e}")
        _log("Continuando con energy threshold por defecto...")

    # Escuchar en background — callback cada vez que detecta audio
    try:
        stop_listener = r.listen_in_background(mic, callback_recognizer, phrase_time_limit=2)
        _log("Listener activo. Di 'Hey Jarvis' para abrir J.A.R.V.I.S.")
    except Exception as e:
        _log(f"FATAL: Error al iniciar listen_in_background: {e}")
        time.sleep(5)
        main()  # reintentar
        return

    # Mantener vivo con health check y auto-restart
    fails = 0
    try:
        while True:
            time.sleep(1)
            fails += 1
            if fails % 60 == 0:
                _log(f"Listener sigue activo ({fails}s)")
            # Si no se ha captado audio en 5 minutos, reiniciar el listener
            if _ultimo_audio > 0 and (time.time() - _ultimo_audio) > 300:
                _log("Sin actividad de audio por 5 min, reiniciando listener...")
                try:
                    stop_listener(wait_for_stop=True)
                except Exception:
                    pass
                time.sleep(2)
                main()  # reiniciar
                return
    except KeyboardInterrupt:
        _log("Listener detenido por el usuario.")
        stop_listener(wait_for_stop=True)


if __name__ == "__main__":
    main()
