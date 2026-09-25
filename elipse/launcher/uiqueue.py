"""
Cola compartida para que los hilos de trabajo (red, subprocess) le pidan
a la UI de Tk que corra algo en el hilo principal. Tkinter no es
thread-safe, así que ningún hilo debe tocar widgets directamente: todo
pasa por acá y `App._pump()` lo vacía con `after()`.
"""

import queue

UIQ = queue.Queue()


def ui(fn, *args):
    UIQ.put((fn, args))
