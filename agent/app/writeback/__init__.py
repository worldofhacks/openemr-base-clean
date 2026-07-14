"""Contained append-only OpenEMR write path (W2-D1/D9/D10)."""

from app.writeback.intents import ExactlyOnceWriter, IntentSpec

__all__ = ["ExactlyOnceWriter", "IntentSpec"]
