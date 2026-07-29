"""
Astrazione multipiattaforma per l'alternanza monitor a stacking (vedi
PUPA_ARCHITECTURE.md/OBS_CONFIG.md): "apri una volta i 2 Proiettori per
uscita (Programma + scena nera), poi alterna solo portando in primo piano
quello giusto" - stesso principio su entrambe le piattaforme, ma Linux (X11/
wmctrl) e Windows (pywin32) individuano/attivano le finestre in modo
completamente diverso. pupa.py chiama solo get_window_manager() e i due
metodi comuni (open_stacked_pair/activate), senza sapere su quale piattaforma
gira.

2026-07-22: nato per portare l'alternanza monitor anche su Windows (prima
solo Linux) - la logica Linux qui dentro e' quella gia' verificata dal vivo
(2249 flip/900s, 0 falliti), spostata cosi' com'e' da pupa.py senza
modifiche, non riscritta.
"""
import os
import platform
import subprocess
import time

from debug_logger import debug as debug_log


class WindowManager:
    """Interfaccia comune - vedi LinuxWindowManager/WindowsWindowManager."""

    def open_stacked_pair(self, obs, monitor_index, black_scene, position_key):
        """Apre (o ripulisce e riapre) la coppia di Proiettori sovrapposti
        per una singola uscita fisica - Programma + black_scene, stessa
        posizione. Ritorna (on_id, off_id) o (None, None) se non riesce a
        rilevarle. `position_key` e' un hint opzionale (su Linux: la
        posizione X del monitor, usata per non toccare finestre di ALTRE
        uscite durante la pulizia iniziale) - le implementazioni che non ne
        hanno bisogno lo ignorano."""
        raise NotImplementedError

    def activate(self, window_id):
        """Porta la finestra GIA' APERTA in primo piano/sopra le altre.
        Nessuna creazione ne' distruzione. True se il rialzo e' verificato
        riuscito, False altrimenti."""
        raise NotImplementedError


class LinuxWindowManager(WindowManager):
    """wmctrl/xprop (X11) - logica invariata rispetto alla versione storica
    in pupa.py, verificata dal vivo (35+ min stabile, 2249 flip/900s senza
    fallimenti nel test isolato). Spostata qui solo per condividere
    un'interfaccia comune con Windows, non riscritta."""

    def _env(self):
        """DISPLAY va impostato esplicitamente, non e' detto sia ereditato
        (es. sessione SSH pura, senza inoltro X11 - verificato dal vivo che
        senza questo wmctrl non vede nessuna finestra)."""
        env = dict(os.environ)
        env.setdefault("DISPLAY", ":0")
        return env

    def _list_projectors(self):
        """Lista (window_id, posizione_x, titolo) di tutte le finestre
        'Proiettore' aperte in questo momento."""
        try:
            result = subprocess.run(["wmctrl", "-l", "-G"], capture_output=True, text=True, timeout=3, env=self._env())
        except Exception as e:
            debug_log(f"[MONITOR] wmctrl -l -G fallito: {e}")
            return []
        projectors = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8 or "Proiettore" not in parts[7]:
                continue
            try:
                projectors.append((parts[0], int(parts[2]), parts[7]))
            except ValueError:
                continue
        return projectors

    def _ids_at(self, x_position):
        """Set degli ID di tutte le finestre Proiettore attualmente aperte
        esattamente su x_position."""
        return set(win_id for win_id, x_pos, _ in self._list_projectors() if x_pos == x_position)

    def _find_new_at(self, x_position, before_ids, timeout=2.0):
        """Cerca (con retry fino a `timeout`) la finestra Proiettore NUOVA in
        posizione x_position - "nuova" rispetto a before_ids (l'insieme gia'
        presente PRIMA di aprire il proiettore), non solo "diversa da quella
        che tracciavamo".

        Escludere solo l'ID tracciato non bastava: se sulla stessa posizione
        erano gia' rimaste finestre orfane da un problema precedente, la
        prima trovata - orfana, non quella appena aperta - veniva "adottata"
        per sbaglio. Confrontare contro l'insieme completo PRIMA
        dell'apertura elimina l'ambiguita' a prescindere da quante finestre
        orfane ci fossero gia'."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            new_ones = self._ids_at(x_position) - before_ids
            if new_ones:
                return sorted(new_ones)[-1] if len(new_ones) > 1 else next(iter(new_ones))
            time.sleep(0.05)
        return None

    def _get_active_window(self):
        """ID (int) della finestra attualmente attiva secondo il window
        manager (_NET_ACTIVE_WINDOW) - usato per verificare che un rialzo
        abbia DAVVERO funzionato, non solo che il comando non abbia
        sollevato un'eccezione. None se non determinabile."""
        try:
            result = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                                     capture_output=True, text=True, timeout=3, env=self._env())
            parts = result.stdout.strip().split()
            if "#" in parts:
                hex_token = parts[parts.index("#") + 1].rstrip(",")
                return int(hex_token, 16)
        except Exception as e:
            debug_log(f"[MONITOR] xprop finestra attiva fallito: {e}")
        return None

    def _close(self, window_id):
        """Chiude una finestra specifica per ID - usata SOLO in fase di
        avvio per ripulire eventuali proiettori rimasti da un lancio
        precedente, mai durante l'alternanza a runtime."""
        try:
            subprocess.run(["wmctrl", "-i", "-c", window_id], timeout=3, env=self._env())
        except Exception as e:
            debug_log(f"[MONITOR] wmctrl -c fallito ({window_id}): {e}")

    def activate(self, window_id):
        """VERIFICA POST-RIALZO: wmctrl puo' "riuscire" (nessuna eccezione)
        senza che la finestra diventi DAVVERO quella attiva. Un breve
        margine (0.1s) prima di controllare lascia al window manager il
        tempo di aggiornare _NET_ACTIVE_WINDOW."""
        try:
            subprocess.run(["wmctrl", "-i", "-a", window_id], timeout=3, env=self._env(), capture_output=True)
        except Exception as e:
            debug_log(f"[MONITOR] wmctrl -i -a fallito ({window_id}): {e}")
            return False

        time.sleep(0.1)
        active = self._get_active_window()
        if active is not None and active != int(window_id, 16):
            debug_log(f"[MONITOR] rialzo NON verificato: richiesta {window_id}, attiva risulta {hex(active)}")
            return False
        return True

    def open_stacked_pair(self, obs, monitor_index, black_scene, position_key):
        """Chiude PRIMA qualunque proiettore residuo in QUESTA posizione
        (scoped per non toccare le finestre dell'ALTRA uscita) - un riavvio
        senza questa pulizia lascerebbe le 2 finestre del lancio precedente
        aperte per sempre, raddoppiando ad ogni riavvio."""
        x_position = position_key
        for win_id in self._ids_at(x_position):
            self._close(win_id)

        before = self._ids_at(x_position)
        obs.open_program_projector(monitor_index)
        on_id = self._find_new_at(x_position, before, timeout=10.0)
        if on_id is None:
            return None, None

        before = self._ids_at(x_position)
        obs.open_scene_projector(black_scene, monitor_index)
        off_id = self._find_new_at(x_position, before, timeout=10.0)
        if off_id is None:
            return None, None

        return on_id, off_id


class WindowsWindowManager(WindowManager):
    """pywin32 - nessun bisogno di scoping per posizione (a differenza di
    Linux): ogni apertura viene rilevata con un prima/dopo sull'INTERO
    elenco finestre visibili, che isola gia' in modo inequivocabile la
    finestra appena creata da QUELLA chiamata specifica, a prescindere da
    quante altre finestre 'Proiettore' esistano gia' per l'altra uscita.
    Verificato dal vivo (2026-07-22) su OBS 32.1.2: richiede che OBS NON
    giri elevato (Amministratore) mentre pupa.py non lo e' - altrimenti
    Windows nega l'accesso a QUALUNQUE manipolazione di finestra (UIPI),
    non solo SetForegroundWindow (quello fallisce comunque per il separato
    "lock" anti-furto-focus di Windows, non serve pero': bastare essere in
    cima nello z-order, non avere il focus tastiera vero, dato che sono
    Proiettori fullscreen dedicati a un monitor fisico)."""

    TITLE_SUBSTRING = "Proiettore"

    def __init__(self):
        import win32gui  # import qui, non in cima al file: modulo Windows-only
        self._win32gui = win32gui
        self._cleaned_once = False

    def _list_windows(self):
        wins = []

        def cb(hwnd, _):
            if self._win32gui.IsWindowVisible(hwnd):
                title = self._win32gui.GetWindowText(hwnd)
                if title:
                    wins.append((hwnd, title))
        self._win32gui.EnumWindows(cb, None)
        return wins

    def _ids_titled(self, substring):
        return set(hwnd for hwnd, title in self._list_windows() if substring in title)

    def _find_new(self, before_ids, timeout=2.0):
        """Stesso principio del retry Linux: aspetta una finestra NUOVA
        (titolo 'Proiettore', non in before_ids) fino a timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            new_ones = self._ids_titled(self.TITLE_SUBSTRING) - before_ids
            if new_ones:
                return next(iter(new_ones))
            time.sleep(0.05)
        return None

    def _close(self, hwnd):
        try:
            import win32con
            self._win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception as e:
            debug_log(f"[MONITOR] chiusura finestra Windows fallita ({hwnd}): {e}")

    def _is_above(self, hwnd_a, hwnd_b, max_scan=2000):
        """True se hwnd_a precede hwnd_b nell'ordine Z (dal piu' in cima in
        giu') - verifica RELATIVA tra le 2 finestre della stessa coppia, non
        "e' la finestra piu' in cima di tutto il desktop" (troppo severo,
        ci sono sempre finestre di sistema sopra). None se nessuna delle due
        viene trovata entro max_scan finestre (non dovrebbe capitare)."""
        h = self._win32gui.GetTopWindow(0)
        seen = 0
        while h and seen < max_scan:
            if h == hwnd_a:
                return True
            if h == hwnd_b:
                return False
            h = self._win32gui.GetWindow(h, 3)  # GW_HWNDNEXT
            seen += 1
        return None

    def activate(self, window_id):
        """Porta la finestra in cima allo z-order (SetWindowPos, non
        SetForegroundWindow: quest'ultimo fallisce sempre per un processo
        senza focus tastiera reale - vincolo Windows separato dal problema
        di elevazione, non serve pero' per un Proiettore fullscreen
        dedicato). Nessuna verifica post-rialzo qui (a differenza di Linux)
        perche' SetWindowPos con HWND_TOP non fallisce silenziosamente allo
        stesso modo di wmctrl - un'eccezione e' gia' un segnale affidabile."""
        try:
            import win32con
            self._win32gui.SetWindowPos(
                window_id, win32con.HWND_TOP, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
            )
            return True
        except Exception as e:
            debug_log(f"[MONITOR] SetWindowPos fallito ({window_id}): {e}")
            return False

    def open_stacked_pair(self, obs, monitor_index, black_scene, position_key):
        """Pulizia UNA TANTUM (non per-chiamata come Linux: qui non serve
        scoping per posizione, un solo giro copre gia' entrambe le uscite)
        di eventuali Proiettori residui da un lancio precedente, poi apre
        la coppia Programma + black_scene per questo monitor_index."""
        if not self._cleaned_once:
            for hwnd in self._ids_titled(self.TITLE_SUBSTRING):
                self._close(hwnd)
            self._cleaned_once = True
            time.sleep(0.3)  # lascia il tempo alle finestre chiuse di sparire prima del prossimo prima/dopo

        before = self._ids_titled(self.TITLE_SUBSTRING)
        obs.open_program_projector(monitor_index)
        on_id = self._find_new(before, timeout=10.0)
        if on_id is None:
            return None, None

        before = self._ids_titled(self.TITLE_SUBSTRING)
        obs.open_scene_projector(black_scene, monitor_index)
        off_id = self._find_new(before, timeout=10.0)
        if off_id is None:
            return None, None

        return on_id, off_id


def get_window_manager():
    """Factory: ritorna l'implementazione giusta per la piattaforma
    corrente. Solleva RuntimeError su piattaforme non supportate (invece di
    ritornare None silenziosamente) - l'alternanza monitor e' una feature
    opzionale (vedi MONITOR_SHOW1_INDEX in secrets_local.py), il chiamante
    in pupa.py gia' non la attiva affatto se la piattaforma non e' nota."""
    system = platform.system()
    if system == "Linux":
        return LinuxWindowManager()
    if system == "Windows":
        return WindowsWindowManager()
    raise RuntimeError(f"Alternanza monitor non supportata sulla piattaforma '{system}'")
