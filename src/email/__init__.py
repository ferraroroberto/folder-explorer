"""Email search over an email index, and the one reader allowed to open a ``.msg`` file.

``src/email/reader.py`` is the only module that imports ``extract_msg`` or
opens a scanned file; everything else here works on the index's rows.
"""
