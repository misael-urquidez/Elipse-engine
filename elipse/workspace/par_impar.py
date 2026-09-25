#!/usr/bin/env python3
"""Programa simple que indica si un número es par o impar.

Uso:
    python3 par_impar.py <número>

Ejemplo:
    $ python3 par_impar.py 10
    10 es par.

Si no se proporciona argumento, solicita al usuario ingresar un número.
"""

import sys


def es_par(num: int) -> bool:
    """Devuelve True si num es par, False si es impar."""
    return num % 2 == 0


def main():
    if len(sys.argv) > 1:
        try:
            numero = int(sys.argv[1])
        except ValueError:
            print("Error: el argumento debe ser un número entero.")
            sys.exit(1)
    else:
        try:
            numero = int(input("Introduce un número entero: "))
        except ValueError:
            print("Error: debes introducir un número entero.")
            sys.exit(1)

    if es_par(numero):
        print(f"{numero} es par.")
    else:
        print(f"{numero} es impar.")


if __name__ == "__main__":
    main()
