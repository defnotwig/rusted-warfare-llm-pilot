# LocalShorts Studio Assets Directory

This directory contains locally bundled, open-licensed or user-authorized assets used by the LocalShorts Studio rendering pipeline:

- **`fonts/`**: Open Font License (OFL) / Apache 2.0 TTF font files (`ArchivoBlack-Regular.ttf`, `BarlowCondensed-Bold.ttf`, `RobotoCondensed-Bold.ttf`) used by the ASS kinetic caption burner and procedural motion-graphics engine.
- **`sfx/`**: Five deterministic CC0 public-domain sound effects (`impact.wav`, `whoosh.wav`, `pop.wav`, `click.wav`, `riser.wav`) synthesized locally at `44.1 kHz` PCM WAV by `localshorts.services.audio_sfx`.
- **`templates/`**: Procedural motion-graphics style configurations.
- **`authorized_media/`**: Place user-owned or licensed video/image assets inside `assets/authorized_media/{CreatorName}/` alongside an optional `manifest.json`:
  ```json
  {
    "owner": "Your Channel / Creator Name",
    "license_type": "User-Owned / Licensed",
    "source_url": "https://example.com",
    "attribution_text": "Courtesy of Creator Name",
    "allowed_use": true
  }
  ```
