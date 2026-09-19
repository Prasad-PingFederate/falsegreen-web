"""Put the project root on sys.path so tests can `import app.*`.

Its presence at the root is what makes pytest prepend this directory rather
than tests/, which is all that is needed here - no fixtures belong at this
level.
"""
