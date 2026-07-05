import random

class PupaBrain:
    def __init__(self, scenes_cfg):
        """
        Inizializza il cervello decisionale di PUPA.
        Riceve la configurazione da yaml e prepara la memoria storica.
        """
        self.cfg = scenes_cfg
        
        # Buffer storici per l'algoritmo anti-ripetizione
        self.history_A = []
        self.history_B = []
        
        # Limiti di memoria (quante scene escludere prima che possano tornare in onda)
        self.max_history_A = 3  # Esclude le ultime 3 scene principali di tipo _A
        self.max_history_B = 2  # Esclude le ultime 2 scene di transizione/bridge di tipo _B
        
        # Stato interno per tracciare l'alternanza dei flussi
        self.last_type_was_A = False

    def decide_next_scene(self, cached_obs_scenes, audio_data):
        """
        Analizza i dati audio attuali e sceglie la scena ottimale filtrando
        in base alla memoria storica e allo stato della traccia.
        """
        # 1. Filtro di sicurezza rigido: eliminiamo le scene rimosse dal parco attivo
        scene_valide = [
            s for s in cached_obs_scenes 
            if s not in ["Scena 0", "dust_B", "NERO_MASTER"]
        ]
        
        if not scene_valide:
            print("[⚠️ WARN] Nessuna scena valida trovata nei filtri. Fallback su NERO_MASTER.")
            return "NERO_MASTER", {}, "EMPTY_POOL", 5.0

        # Estraggo le metriche di interesse
        is_drop = audio_data.get("is_drop", False)
        bass_energy = audio_data.get("bass", 0.0)
        
        # 2. SELEZIONE DEL POOL ED EVALUATION DELLO STATO AUDIO
        if is_drop or bass_energy > 65.0:
            # STATO ALTA ENERGIA: Alternanza Ritmica Main (_A) e Bridge/Shader (_B)
            if self.last_type_was_A:
                # Abbiamo appena fatto una scena principale. Sparami una variazione rapida (_B)
                pool = [s for s in scene_valide if s.endswith("_B")]
                history = self.history_B
                max_hist = self.max_history_B
                duration = float(random.choice([2.5, 3.5, 4.0]))  # Bridge corti e impattanti
                self.last_type_was_A = False
                energy_str = "🔥 DROP -> RIEMPIMENTO SHORT (_B)"
            else:
                # Torna sul corpo principale della traccia con una scena pesante (_A)
                pool = [s for s in scene_valide if s.endswith("_A")]
                history = self.history_A
                max_hist = self.max_history_A
                duration = float(random.randint(8, 15))  # Le scene _A tengono la struttura
                self.last_type_was_A = True
                energy_str = "⚡ DROP -> SCENA CORPO (_A)"
        else:
            # STATO LOW ENERGY / BREAK / SFUMATURE IPNOTICHE
            # Usiamo un pool selezionato di scene adatte a rallentare il frame rate visivo
            scene_ambient = ["mri_A", "urbanfree_A", "tunnelwave_B", "projectx_B"]
            pool = [s for s in scene_valide if s in scene_ambient]
            
            # Fallback se le scene ambient non sono caricate su OBS in questo momento
            if not pool:
                pool = [s for s in scene_valide if s.endswith("_A")]
                
            history = self.history_A
            max_hist = 2
            duration = float(random.randint(14, 22))  # Cambi lenti, ipnotici e distesi
            self.last_type_was_A = True
            energy_str = "🌌 BREAK / LOW ENERGY"

        # 3. ALGORITMO ANTI-RIPETIZIONE
        # Filtra via le scene che sono presenti nella cronologia recente di quel pool
        scelte_filtrate = [s for s in pool if s not in history]
        
        # Salvagente: Se la storia satura tutto il pool, svuota il buffer per evitare stalli
        if not scelte_filtrate:
            scelte_filtrate = pool if pool else scene_valide
            history.clear()

        # Scelta casuale ponderata dal filtro
        next_scene = random.choice(scelte_filtrate)

        # 4. AGGIORNAMENTO DELLA MEMORIA STORICA
        history.append(next_scene)
        if len(history) > max_hist:
            history.pop(0)  # Rilascia la scena più vecchia permettendo il riuso futuro

        # Statistiche di debug da ritornare al main loop
        stats = {
            "pool_size": len(pool),
            "history_current_len": len(history),
            "bass_level": bass_energy
        }

        return next_scene, stats, energy_str, duration