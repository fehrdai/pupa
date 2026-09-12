"""
Debug logger con auto-rotation per evitare file enormi
"""
import logging
from logging.handlers import RotatingFileHandler
import os

def setup_debug_logger(name="pupa_debug", log_file="debug.log", max_bytes=2000000, backup_count=6, log_dir="logs"):
    """
    Setup logger che scrive su file con rotation automatica

    Args:
        name: nome logger (deve essere univoco per ottenere un file separato -
            due chiamate con lo stesso name condividono lo stesso logger/file,
            vedi 2026-08-13: pupa_exhibition.py usa un name diverso apposta
            per non mischiare i suoi log con quelli del DJset)
        log_file: file di output (nome, non percorso - finisce sempre dentro
            log_dir, 2026-07-17: prima scriveva nella root del progetto)
        max_bytes: max dimensione file prima di rotate (2MB default - 2026-07-30:
            alzato da 100KB dopo che un test live di ~1h40 aveva perso oltre
            i 3/4 dei log per rotation, coprendo solo gli ultimi ~28min)
        backup_count: quanti file backup mantenere (6 default, era 3 - insieme
            al nuovo max_bytes copre un'intera serata invece di ~25 minuti)
        log_dir: cartella di destinazione (default "logs" per pupa.py, script
            gemelli come pupa_exhibition.py passano una cartella propria per
            non mischiare i log - vedi name sopra per lo stesso motivo lato
            logger)
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Se ha già handlers, non aggiungere duplicati
    if logger.handlers:
        return logger

    os.makedirs(log_dir, exist_ok=True)

    # RotatingFileHandler - crea backup quando raggiunge max_bytes
    handler = RotatingFileHandler(
        os.path.join(log_dir, log_file),
        maxBytes=max_bytes,
        backupCount=backup_count
    )

    formatter = logging.Formatter(
        '[%(asctime)s] %(name)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger

# Global logger instance
debug_log = setup_debug_logger()

def debug(msg):
    """Log a debug message"""
    debug_log.debug(msg)

def info(msg):
    """Log an info message"""
    debug_log.info(msg)

def warning(msg):
    """Log a warning message"""
    debug_log.warning(msg)

def error(msg):
    """Log an error message"""
    debug_log.error(msg)
