import logging
from logging.handlers import TimedRotatingFileHandler
import os

def setup_logger(log_file="pupa.log"):
    """Un file per giorno (rotazione a mezzanotte, 30 giorni tenuti) invece
    di un unico file che cresce all'infinito - 2026-07-17: pupa.log era
    arrivato a 13.4MB senza mai ruotare. Il file rotato prende un suffisso
    data automatico (es. pupa.log.2026-07-17), il nome base resta sempre
    il log del giorno corrente."""
    logger = logging.getLogger("pupa_brain")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    os.makedirs("logs", exist_ok=True)
    fh = TimedRotatingFileHandler(f"logs/{log_file}", when="midnight", backupCount=30, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        '%(asctime)s - [%(levelname)s] - %(message)s',
        datefmt='%H:%M:%S'
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger

def log_decision(from_scene, to_scene, reason, energy, duration, logger, trans_type=""):
    if logger:
        trans_label = f" | {trans_type}" if trans_type else ""
        logger.info(
            f"SWITCH: {from_scene:20} -> {to_scene:20} | "
            f"{reason:50} | {energy:15} | {duration}ms{trans_label}"
        )