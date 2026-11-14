"""Compatibility entry point for the active CNEFE rooftop implementation."""

from cnefe_rooftop_alg import *  # noqa: F401,F403
import cnefe_rooftop_alg as _implementation


if __name__ == "__main__":
    _implementation._cli()
