"""The morning-audio pipeline: plugin registries and their built-in plugins.

Everything here is a plugin behind a registry (wayfinder ticket 09):
script-writer, tts, publish, notify. Call :func:`registries.load_plugins`
before resolving a default so the built-in implementations register
themselves.
"""
