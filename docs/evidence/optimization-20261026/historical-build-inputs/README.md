# Historical image build inputs

These 12 files were local, Git-ignored evaluation outputs copied into the reviewed Agent image by `COPY evals ./evals`. They are retained here as exact original build inputs so a clean CI checkout can reproduce the frozen application fingerprint. This makes the original inclusion visible; it does not reclassify them as application source or fresh test evidence.

The JSON/Markdown reports describe historical August/September retrieval checks or synthetic offline HTTP checks. The old successful and failed results remain unchanged. The two non-UTF-8 log files, CRLF line endings, and empty logs are preserved byte for byte. Secret scanning found no credentials; reviewed retrieval text comes from the existing public demonstration knowledge, with no individual human trip observed. This bounded review is not a universal privacy guarantee.

`manifest.json` binds each raw SHA256 and its existing approved application hash. The helper only checks locally by default. CI must explicitly restore these files before the Agent build and still verify the unchanged 87 OS packages, 1086 native ELF paths, application map, source trees, runtime constraints and expiring authorization. Missing, changed or additional destination files block; nothing is silently overwritten or rebaselined.
